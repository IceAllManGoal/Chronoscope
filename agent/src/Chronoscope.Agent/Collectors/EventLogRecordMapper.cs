using Chronoscope.Agent.Contract;
using Chronoscope.Agent.Identity;

namespace Chronoscope.Agent.Collectors;

/// <summary>
/// Преобразование наблюдения за записью журнала в сырое событие (§12, §77.2).
///
/// Здесь заканчивается платформенная часть коллектора журнала: дальше идут
/// только контракт и политика приватности. Поэтому именно эту точку проверяют
/// тесты — в том числе на то, что в событие не попадает ничего, кроме явно
/// разрешённых полей.
/// </summary>
public sealed class EventLogRecordMapper
{
    /// <summary>Дискриминатор payload внутри коллектора и в Core.</summary>
    public const string PayloadType = "event_log_record";

    private readonly AgentIdentity _identity;
    private readonly string _collectorVersion;
    private readonly TimeProvider _timeProvider;

    public EventLogRecordMapper(AgentIdentity identity, string collectorVersion, TimeProvider? timeProvider = null)
    {
        _identity = identity;
        _collectorVersion = collectorVersion;
        _timeProvider = timeProvider ?? TimeProvider.System;
    }

    /// <summary>Собрать сырое событие из наблюдения.</summary>
    public RawEvent Map(EventLogObservation observation)
    {
        ArgumentNullException.ThrowIfNull(observation);

        return new RawEvent
        {
            SchemaVersion = RawEventJson.SupportedSchemaVersion,
            RawEventId = Ulid.NewPrefixed("raw", _timeProvider.GetUtcNow()),
            Collector = EventLogCollector.CollectorName,
            CollectorVersion = _collectorVersion,
            ObservedAt = _timeProvider.GetUtcNow(),
            // Время записи из журнала. Оно может отличаться от времени наблюдения
            // на любую величину, и это не ошибка: §24 требует хранить оба.
            SourceTimestamp = observation.RecordedAt,
            HostId = _identity.HostId,
            BootId = _identity.BootId,
            PayloadType = PayloadType,
            Payload = BuildPayload(observation),
        };
    }

    /// <summary>
    /// Собрать payload по явному списку разрешённых полей.
    ///
    /// Список разрешающий, а не запрещающий, и это существенно. У записи журнала
    /// есть XML, данные события (<c>EventData</c>, <c>UserData</c>), SID
    /// пользователя, вставленные в сообщение параметры и отформатированный текст.
    /// Реализация вида «взять весь объект, потом убрать лишнее» означала бы, что
    /// любое новое поле Windows однажды уедет в Core по умолчанию. Здесь по
    /// умолчанию не уезжает ничего: чтобы поле попало в историю, его нужно
    /// добавить сюда осознанно.
    ///
    /// Отсутствующее значение не подменяется нулём — поле просто не добавляется.
    /// Ноль в <c>event_id</c> выглядел бы как настоящее событие с идентификатором
    /// 0, а разница между «не сообщили» и «сообщили ноль» невосстановима.
    /// </summary>
    private static Dictionary<string, object?> BuildPayload(EventLogObservation observation)
    {
        var payload = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["channel"] = observation.Channel,
        };

        if (!string.IsNullOrEmpty(observation.Provider))
        {
            payload["provider"] = observation.Provider;
        }

        if (observation.EventId is not null)
        {
            payload["event_id"] = observation.EventId.Value;
        }

        if (observation.RecordId is not null)
        {
            payload["record_id"] = observation.RecordId.Value;
        }

        if (observation.Level is not null)
        {
            payload["level"] = observation.Level.Value;
        }

        if (observation.Task is not null)
        {
            payload["task"] = observation.Task.Value;
        }

        if (observation.Opcode is not null)
        {
            payload["opcode"] = observation.Opcode.Value;
        }

        // Сообщение добавляется последним и только тогда, когда его действительно
        // собрали: это единственное поле, которое может содержать чужие данные.
        if (observation.Message is not null)
        {
            payload["message"] = observation.Message;
        }

        return payload;
    }
}
