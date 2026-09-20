using System.Text.RegularExpressions;
using Chronoscope.Agent.Configuration;
using Chronoscope.Agent.Contract;
using Chronoscope.Agent.Identity;

namespace Chronoscope.Agent.Collectors;

/// <summary>
/// Преобразование наблюдения за процессом в сырое событие (§12, §8.4).
///
/// Здесь заканчивается платформенная часть Agent: дальше идут только контракт и
/// политика приватности. Поэтому именно эту точку проверяют тесты — в том числе
/// на то, что при выключенных настройках чувствительные поля не попадают в
/// событие вообще, а не «попадают пустыми».
///
/// Имена полей payload соответствуют docs/EVENT_MODEL.md §8: источник отдаёт
/// <c>path</c>, а нормализованное событие Core называет это <c>executable</c> —
/// переименование делает Core, потому что raw-слой обязан хранить терминологию
/// источника.
/// </summary>
public sealed class ProcessEventMapper
{
    /// <summary>Чем заменяется совпадение с шаблоном редакции (§38).</summary>
    public const string RedactionReplacement = "***";

    private readonly AgentIdentity _identity;
    private readonly ProcessCollectorSettings _settings;
    private readonly IReadOnlyList<Regex> _redactionPatterns;
    private readonly string _collectorVersion;
    private readonly TimeProvider _timeProvider;

    public ProcessEventMapper(
        AgentIdentity identity,
        ProcessCollectorSettings settings,
        PrivacySettings privacy,
        string collectorVersion,
        TimeProvider? timeProvider = null)
    {
        _identity = identity;
        _settings = settings;
        _redactionPatterns = privacy.RedactCommandLinePatterns
            .Select(pattern => new Regex(pattern, RegexOptions.None, TimeSpan.FromMilliseconds(100)))
            .ToArray();
        _collectorVersion = collectorVersion;
        _timeProvider = timeProvider ?? TimeProvider.System;
    }

    /// <summary>Собрать сырое событие из наблюдения.</summary>
    public RawEvent Map(ProcessObservation observation)
    {
        ArgumentNullException.ThrowIfNull(observation);

        return new RawEvent
        {
            SchemaVersion = RawEventJson.SupportedSchemaVersion,
            RawEventId = Ulid.NewPrefixed("raw", _timeProvider.GetUtcNow()),
            Collector = ProcessCollector.CollectorName,
            CollectorVersion = _collectorVersion,
            ObservedAt = _timeProvider.GetUtcNow(),
            SourceTimestamp = observation.IsExit ? observation.ExitedAt ?? observation.StartedAt : observation.StartedAt,
            HostId = _identity.HostId,
            BootId = _identity.BootId,
            PayloadType = ProcessCollector.PayloadTypeFor(observation.IsExit),
            Payload = BuildPayload(observation),
        };
    }

    /// <summary>
    /// Собрать payload. Поля добавляются только тогда, когда их разрешено
    /// собирать: контракт объявляет их необязательными, поэтому «выключено»
    /// означает отсутствие поля, а не пустое значение. Разница существенная —
    /// пустое поле выглядело бы как «источник не сообщил», а отсутствующее
    /// честно означает «не собирали».
    /// </summary>
    private Dictionary<string, object?> BuildPayload(ProcessObservation observation)
    {
        var payload = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["pid"] = observation.ProcessId,
            ["name"] = observation.Name,
            // §14: время старта нужно Core, чтобы вывести тот же
            // process_instance_id для запуска и для выхода.
            ["process_started_at"] = UtcTimestampConverter.Format(observation.StartedAt),
        };

        if (observation.ParentProcessId is not null)
        {
            payload["parent_pid"] = observation.ParentProcessId.Value;
        }

        if (_settings.CapturePath && !string.IsNullOrEmpty(observation.ExecutablePath))
        {
            payload["path"] = observation.ExecutablePath;
        }

        if (_settings.CaptureCommandLine && !string.IsNullOrEmpty(observation.CommandLine))
        {
            payload["command_line"] = Redact(observation.CommandLine);
        }

        if (_settings.CaptureUser && !string.IsNullOrEmpty(observation.UserSid))
        {
            payload["user_sid"] = observation.UserSid;
        }

        if (observation.IsExit)
        {
            payload["exited_at"] = UtcTimestampConverter.Format(observation.ExitedAt ?? _timeProvider.GetUtcNow());

            if (observation.ExitCode is not null)
            {
                payload["exit_code"] = observation.ExitCode.Value;
            }
        }

        return payload;
    }

    /// <summary>
    /// Вырезать из командной строки всё, что совпало с настроенными шаблонами (§38).
    ///
    /// Редакция не может быть идеальной, поэтому она и не подменяет собой решение
    /// пользователя: по умолчанию командная строка не собирается вовсе, а шаблоны
    /// нужны для тех, кто собирать её всё же решил.
    /// </summary>
    private string Redact(string commandLine)
    {
        var result = commandLine;
        foreach (var pattern in _redactionPatterns)
        {
            result = pattern.Replace(result, RedactionReplacement);
        }

        return result;
    }
}
