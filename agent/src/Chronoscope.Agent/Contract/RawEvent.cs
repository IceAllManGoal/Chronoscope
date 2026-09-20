using System.Text.Json.Serialization;

namespace Chronoscope.Agent.Contract;

/// <summary>
/// Сырое событие — универсальная оболочка вокруг source-specific payload (§12).
///
/// Оболочка — <b>строгий</b> контракт между Agent и Core: схема
/// <c>shared/schemas/raw-event.schema.json</c> объявлена с
/// <c>additionalProperties: false</c>, а Pydantic-модель Core — с
/// <c>extra="forbid"</c>. Поэтому лишнее поле здесь не «пройдёт молча», а
/// отвергнет всё событие: любой новый член этого типа обязан сначала появиться
/// в схеме.
///
/// <c>payload</c>, наоборот, схемой намеренно не ограничен — это точка
/// расширения, развивающаяся вместе с источником (docs/EVENT_MODEL.md §11).
/// </summary>
public sealed record RawEvent
{
    /// <summary>Версия схемы транспортного объекта (§52). Core отвергает неподдерживаемую явно.</summary>
    [JsonPropertyName("schema_version")]
    public required int SchemaVersion { get; init; }

    /// <summary>
    /// Стабильный идентификатор сырого события, назначается Agent.
    /// Ключ идемпотентности ingest: повторная отправка того же значения не
    /// создаёт дубликат (§34), поэтому при повторе идентификатор обязан
    /// сохраняться.
    /// </summary>
    [JsonPropertyName("raw_event_id")]
    public required string RawEventId { get; init; }

    /// <summary>Коллектор-источник, например <c>windows.process</c>.</summary>
    [JsonPropertyName("collector")]
    public required string Collector { get; init; }

    /// <summary>Версия коллектора на момент наблюдения.</summary>
    [JsonPropertyName("collector_version")]
    public required string CollectorVersion { get; init; }

    /// <summary>
    /// Когда Chronoscope увидел событие. Всегда не раньше реального времени
    /// события и отличается от <see cref="SourceTimestamp"/> при задержках (§24).
    /// </summary>
    [JsonPropertyName("observed_at")]
    public required DateTimeOffset ObservedAt { get; init; }

    /// <summary>Время источника. Может отсутствовать, если источник его не даёт.</summary>
    [JsonPropertyName("source_timestamp")]
    public DateTimeOffset? SourceTimestamp { get; init; }

    /// <summary>Случайный локальный идентификатор компьютера (§15), не hardware fingerprint.</summary>
    [JsonPropertyName("host_id")]
    public required string HostId { get; init; }

    /// <summary>Идентификатор загрузки ОС (§16). <c>null</c>, если источник его не позволяет определить.</summary>
    [JsonPropertyName("boot_id")]
    public string? BootId { get; init; }

    /// <summary>Дискриминатор payload внутри коллектора: <c>process_start</c>, <c>process_exit</c>.</summary>
    [JsonPropertyName("payload_type")]
    public required string PayloadType { get; init; }

    /// <summary>Source-specific данные в форме, максимально близкой к источнику.</summary>
    [JsonPropertyName("payload")]
    public required IReadOnlyDictionary<string, object?> Payload { get; init; }
}
