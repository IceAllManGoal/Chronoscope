using System.Diagnostics.Eventing.Reader;
using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Collectors;
using Chronoscope.Agent.Configuration;

namespace Chronoscope.Agent.Collectors.Windows;

/// <summary>
/// Наблюдение за журналом Windows через <see cref="EventLogWatcher"/> (§77.2).
///
/// Подписка, а не опрос: Windows сама сообщает о появлении записи, и между
/// появлением записи и её наблюдением нет интервала опроса — того самого,
/// из-за которого коллектор процессов теряет короткоживущие процессы.
///
/// Уже существующие записи не читаются (<c>readExistingEvents: false</c>):
/// включение источника не превращается в импорт накопленного журнала, и история
/// начинается с момента запуска (§12, §77.2 пункт 3).
///
/// Обработчик вызывается потоком Windows, а не потоком <see cref="WatchAsync"/>.
/// Отсюда два правила, которые здесь соблюдаются буквально: обработчик ничего не
/// ждёт и не выпускает исключений наружу, а критическая ошибка подписки не
/// остаётся строкой в логе — она завершает наблюдение с
/// <see cref="CollectorException"/>, чтобы надзиратель пометил коллектор
/// потерянным, а не продолжал считать источник живым.
/// </summary>
public sealed class WindowsEventLogObservationSource : IEventLogObservationSource
{
    /// <summary>Запрос «все события канала». Канал уже задан в <see cref="EventLogQuery"/>.</summary>
    private const string AllEventsQuery = "*";

    private readonly EventLogCollectorSettings _settings;

    /// <summary>Наблюдение остановлено. Читается обработчиками из потоков Windows.</summary>
    private volatile bool _stopped;

    public WindowsEventLogObservationSource(EventLogCollectorSettings settings)
    {
        ArgumentNullException.ThrowIfNull(settings);
        _settings = settings;
    }

    public async Task WatchAsync(Action<EventLogObservation> onObservation, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(onObservation);

        var failure = new TaskCompletionSource<Exception>(TaskCreationOptions.RunContinuationsAsynchronously);
        var cancelled = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var watchers = new List<EventLogWatcher>(_settings.Channels.Count);

        using var registration = cancellationToken.Register(() => cancelled.TrySetResult());

        try
        {
            foreach (var channel in _settings.Channels)
            {
                watchers.Add(Subscribe(channel, onObservation, failure));
            }

            var finished = await Task.WhenAny(failure.Task, cancelled.Task).ConfigureAwait(false);

            if (finished == failure.Task)
            {
                var error = await failure.Task.ConfigureAwait(false);

                // Отказ одного канала завершает наблюдение целиком, и это
                // осознанно: коллектор либо наблюдает за тем, что просили, либо
                // потерян. Продолжить с одним каналом из двух значило бы выдавать
                // частичное наблюдение за полное.
                throw new CollectorException(error.Message, error);
            }
        }
        finally
        {
            Stop(watchers);
        }
    }

    /// <summary>Подписаться на канал. Ошибка подписки — сразу <see cref="CollectorException"/>.</summary>
    private EventLogWatcher Subscribe(
        string channel,
        Action<EventLogObservation> onObservation,
        TaskCompletionSource<Exception> failure)
    {
        EventLogWatcher watcher;

        try
        {
            var query = new EventLogQuery(channel, PathType.LogName, AllEventsQuery) { TolerateQueryErrors = false };
            watcher = new EventLogWatcher(query, bookmark: null, readExistingEvents: false);
        }
        catch (Exception exception)
        {
            throw new CollectorException($"канал {channel} недоступен: {exception.Message}", exception);
        }

        watcher.EventRecordWritten += (_, arguments) => OnRecordWritten(channel, arguments, onObservation, failure);

        try
        {
            // Именно здесь обнаруживается то, что канал нельзя читать: его нет,
            // он отключён или прав недостаточно. Молчаливое «подписка не
            // состоялась, но Agent работает» — тот случай, ради которого эта
            // ошибка и превращается в исключение.
            watcher.Enabled = true;
        }
        catch (Exception exception)
        {
            watcher.Dispose();
            throw new CollectorException($"канал {channel} не удалось подписать: {exception.Message}", exception);
        }

        return watcher;
    }

    /// <summary>
    /// Обработчик записи. Выполняется потоком Windows, поэтому здесь нельзя
    /// ждать, и отсюда не должно выходить ни одного исключения.
    /// </summary>
    private void OnRecordWritten(
        string channel,
        EventRecordWrittenEventArgs arguments,
        Action<EventLogObservation> onObservation,
        TaskCompletionSource<Exception> failure)
    {
        try
        {
            // О потере подписки Windows сообщает тем же событием, что и о записи:
            // в этом случае заполнено EventException, а записи нет.
            if (arguments.EventException is not null)
            {
                Fail(failure, channel, arguments.EventException);
                return;
            }

            var record = arguments.EventRecord;
            if (record is null)
            {
                Fail(failure, channel, new EventLogException($"канал {channel}: подписка сообщила о событии без записи"));
                return;
            }

            try
            {
                if (_stopped)
                {
                    // Запись пришла уже после остановки наблюдения. Публиковать её
                    // нельзя: буфер к этому моменту может быть закрыт, и событие
                    // было бы сосчитано потерей переполнения — ошибка остановки
                    // выглядела бы как ошибка доставки.
                    return;
                }

                onObservation(Map(channel, record));
            }
            finally
            {
                // Запись пригодна только во время обработчика: всё нужное из неё
                // уже скопировано в наблюдение, а сама она освобождается.
                record.Dispose();
            }
        }
        catch (Exception exception)
        {
            // Исключение не имеет права уйти в Windows: там его никто не ждёт, а
            // наблюдение при этом уже потеряно. Поэтому оно превращается в отказ
            // подписки, который увидит надзиратель.
            Fail(failure, channel, exception);
        }
    }

    /// <summary>Скопировать из записи только разрешённые поля.</summary>
    private EventLogObservation Map(string channel, EventRecord record) => new()
    {
        Channel = channel,
        Provider = record.ProviderName,
        EventId = record.Id,
        RecordId = record.RecordId,
        Level = record.Level,
        Task = record.Task,
        Opcode = record.Opcode,
        RecordedAt = ToUtc(record.TimeCreated),

        // Сообщение читается и форматируется только при явном разрешении: пока
        // флага нет, оно не попадает даже в память Agent.
        Message = _settings.CaptureMessage ? record.FormatDescription() : null,
    };

    /// <summary>
    /// Время записи в UTC.
    ///
    /// <c>EventRecord.TimeCreated</c> отдаёт местное время, поэтому приведение
    /// делается здесь, а не «как-нибудь потом»: §24 требует хранить только UTC, а
    /// наивное местное время в базе неотличимо от UTC и потому опаснее
    /// отсутствующего.
    /// </summary>
    private static DateTimeOffset? ToUtc(DateTime? timeCreated)
        => timeCreated is null ? null : new DateTimeOffset(timeCreated.Value.ToUniversalTime());

    private static void Fail(TaskCompletionSource<Exception> failure, string channel, Exception exception)
        => failure.TrySetResult(new CollectorException($"канал {channel}: {exception.Message}", exception));

    /// <summary>
    /// Закрыть подписки.
    ///
    /// <see cref="_stopped"/> выставляется до отключения наблюдателей: обработчик,
    /// уже находящийся в работе, иначе мог бы опубликовать запись после того, как
    /// точка входа закрыла буфер.
    /// </summary>
    private void Stop(List<EventLogWatcher> watchers)
    {
        _stopped = true;

        foreach (var watcher in watchers)
        {
            try
            {
                watcher.Enabled = false;
            }
            catch (Exception)
            {
                // Наблюдатель мог отказать сам — при остановке это не новость.
            }

            try
            {
                watcher.Dispose();
            }
            catch (Exception)
            {
                // Освобождение ресурса не должно превращать остановку в отказ.
            }
        }
    }
}
