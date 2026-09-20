using System.Collections.Concurrent;
using System.Management;
using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Collectors;
using Chronoscope.Agent.Configuration;

namespace Chronoscope.Agent.Collectors.Windows;

/// <summary>
/// Наблюдение за процессами через WMI (§8.4).
///
/// §8.4 разрешает для первой итерации «более простой Windows-механизм
/// наблюдения», и цель 0.0.1 — проверить pipeline. Выбраны собственные события
/// WMI (<c>__InstanceCreationEvent</c> / <c>__InstanceDeletionEvent</c> по
/// <c>Win32_Process</c>), а не <c>Win32_ProcessStartTrace</c>: трассировочные
/// классы требуют повышенных прав, а §67 запрещает запускать весь продукт от
/// администратора из-за одного коллектора. Цена выбора — при работе без
/// повышения прав <c>ExecutablePath</c> и <c>CommandLine</c> для процессов других
/// пользователей недоступны; поля эти необязательные (§37), поэтому их отсутствие
/// является нормальным состоянием конфигурации, а не ошибкой.
///
/// <b>Память о запусках.</b> Событие выхода обязано нести то же время старта, что
/// и событие запуска, иначе Core выведет по нему другой
/// <c>process_instance_id</c> и связь «запустился → завершился» потеряется (§14,
/// §75). Собственное событие удаления процесса времени старта не сообщает,
/// поэтому источник запоминает его из события создания и подставляет при
/// завершении. Если процесс стартовал до запуска Agent, времени старта нет — оно
/// не выдумывается, и Core подставит запасное значение.
/// </summary>
public sealed class WindowsProcessObservationSource : IProcessObservationSource
{
    /// <summary>
    /// Предел числа запомненных процессов. Записи удаляются при завершении, но
    /// пропущенное событие выхода оставило бы запись навсегда; при достижении
    /// предела карта очищается, и следующие выходы просто не будут знать времени
    /// старта — Core это допускает.
    /// </summary>
    private const int TrackedProcessLimit = 32_768;

    private const string CimV2Scope = @"\\.\root\cimv2";
    private const string ProcessInstanceQuery = "TargetInstance ISA 'Win32_Process'";

    private readonly ProcessCollectorSettings _settings;
    private readonly ConcurrentDictionary<int, TrackedProcess> _tracked = new();

    public WindowsProcessObservationSource(ProcessCollectorSettings settings)
    {
        _settings = settings;
    }

    public async Task WatchAsync(Action<ProcessObservation> onObservation, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(onObservation);

        var scope = new ManagementScope(CimV2Scope);
        try
        {
            scope.Connect();
        }
        catch (ManagementException exception)
        {
            // Наружу уходит нейтральный тип: вызывающий код платформенно
            // нейтрален и не должен видеть типы WMI.
            throw new CollectorException(
                $"не удалось подключиться к WMI ({CimV2Scope}): {exception.Message}", exception);
        }

        using var creationWatcher = new ManagementEventWatcher(scope, BuildQuery("__InstanceCreationEvent"));
        using var deletionWatcher = new ManagementEventWatcher(scope, BuildQuery("__InstanceDeletionEvent"));

        creationWatcher.EventArrived += (_, arguments) => HandleCreation(arguments, onObservation);
        deletionWatcher.EventArrived += (_, arguments) => HandleDeletion(arguments, onObservation);

        creationWatcher.Start();
        deletionWatcher.Start();

        try
        {
            await Task.Delay(Timeout.Infinite, cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // Штатная остановка наблюдения.
        }
        finally
        {
            StopQuietly(creationWatcher);
            StopQuietly(deletionWatcher);
        }
    }

    private static WqlEventQuery BuildQuery(string eventClass) => new(
        eventClass,
        // WITHIN задаёт период опроса. Меньше секунды означает больше нагрузки на
        // WMI, больше — риск не заметить короткоживущий процесс вроде
        // crashpad_handler, который живёт доли секунды.
        new TimeSpan(0, 0, 1),
        ProcessInstanceQuery);

    private void HandleCreation(EventArrivedEventArgs arguments, Action<ProcessObservation> onObservation)
    {
        var process = TargetInstance(arguments);
        if (process is null)
        {
            return;
        }

        var startedAt = ReadCreationDate(process) ?? DateTimeOffset.UtcNow;
        var name = ReadString(process, "Name") ?? string.Empty;

        Remember(process, startedAt, name);

        onObservation(new ProcessObservation
        {
            ProcessId = ReadInt(process, "ProcessId"),
            ParentProcessId = ReadNullableInt(process, "ParentProcessId"),
            Name = name,
            // Читаем только то, что разрешено собирать: незачем держать
            // командную строку в памяти, если её решено не собирать (§6.2).
            ExecutablePath = _settings.CapturePath ? ReadString(process, "ExecutablePath") : null,
            CommandLine = _settings.CaptureCommandLine ? ReadString(process, "CommandLine") : null,
            UserSid = _settings.CaptureUser ? ReadOwnerSid(process) : null,
            StartedAt = startedAt,
            IsExit = false,
        });
    }

    private void HandleDeletion(EventArrivedEventArgs arguments, Action<ProcessObservation> onObservation)
    {
        var process = TargetInstance(arguments);
        if (process is null)
        {
            return;
        }

        var processId = ReadInt(process, "ProcessId");
        var name = ReadString(process, "Name") ?? string.Empty;

        // Время старта берётся из памяти о запуске: собственное событие удаления
        // его не сообщает, а без него Core не свяжет завершение с запуском.
        _tracked.TryRemove(processId, out var tracked);

        onObservation(new ProcessObservation
        {
            ProcessId = processId,
            ParentProcessId = ReadNullableInt(process, "ParentProcessId"),
            Name = string.IsNullOrEmpty(name) ? tracked?.Name ?? string.Empty : name,
            StartedAt = tracked?.StartedAt ?? ReadCreationDate(process) ?? DateTimeOffset.UtcNow,
            IsExit = true,
            ExitedAt = DateTimeOffset.UtcNow,
        });
    }

    private void Remember(ManagementBaseObject process, DateTimeOffset startedAt, string name)
    {
        if (_tracked.Count >= TrackedProcessLimit)
        {
            Console.Error.WriteLine(
                $"[agent] карта запущенных процессов достигла предела {TrackedProcessLimit}: очищаю. "
                + "Следующие завершения не будут знать времени старта.");
            _tracked.Clear();
        }

        _tracked[ReadInt(process, "ProcessId")] = new TrackedProcess(startedAt, name);
    }

    private static ManagementBaseObject? TargetInstance(EventArrivedEventArgs arguments)
        => arguments.NewEvent?["TargetInstance"] as ManagementBaseObject;

    private static int ReadInt(ManagementBaseObject process, string property)
        => ReadNullableInt(process, property) ?? 0;

    private static int? ReadNullableInt(ManagementBaseObject process, string property)
    {
        var value = process[property];
        return value is null ? null : Convert.ToInt32(value, System.Globalization.CultureInfo.InvariantCulture);
    }

    private static string? ReadString(ManagementBaseObject process, string property)
    {
        var value = process[property] as string;
        return string.IsNullOrEmpty(value) ? null : value;
    }

    /// <summary>
    /// Время старта из <c>CreationDate</c>. WMI отдаёт DMTF-дату, а конвертер
    /// возвращает локальное время — приводим его к UTC, потому что в хранилище
    /// попадает только UTC (§24).
    /// </summary>
    private static DateTimeOffset? ReadCreationDate(ManagementBaseObject process)
    {
        if (process["CreationDate"] is not string raw || string.IsNullOrEmpty(raw))
        {
            return null;
        }

        try
        {
            var local = ManagementDateTimeConverter.ToDateTime(raw);
            return new DateTimeOffset(DateTime.SpecifyKind(local, DateTimeKind.Local)).ToUniversalTime();
        }
        catch (Exception exception) when (exception is ArgumentException or FormatException)
        {
            return null;
        }
    }

    /// <summary>
    /// SID владельца процесса. Отдельный вызов WMI, поэтому выполняется только
    /// при включённом сборе пользователя; неудача не считается ошибкой — SID
    /// необязателен (§8.4).
    /// </summary>
    private static string? ReadOwnerSid(ManagementBaseObject process)
    {
        try
        {
            // InvokeMethod есть у ManagementObject, а TargetInstance события —
            // ManagementBaseObject без привязки к пути. Путь экземпляра лежит в
            // системном свойстве __PATH, и по нему объект создаётся заново.
            var path = process.SystemProperties["__PATH"]?.Value as string;
            if (string.IsNullOrEmpty(path))
            {
                return null;
            }

            using var bound = new ManagementObject(new ManagementPath(path));
            using var result = bound.InvokeMethod("GetOwnerSid", null, null);
            return result?["Sid"] as string;
        }
        catch (ManagementException)
        {
            return null;
        }
        catch (UnauthorizedAccessException)
        {
            return null;
        }
    }

    private static void StopQuietly(ManagementEventWatcher watcher)
    {
        try
        {
            watcher.Stop();
        }
        catch (ManagementException)
        {
            // Остановка наблюдения не должна превращаться в отказ завершения.
        }
    }

    private sealed record TrackedProcess(DateTimeOffset StartedAt, string Name);
}
