using System.Text.Json;
using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Collectors;
using Chronoscope.Agent.Contract;
using Chronoscope.Agent.Identity;

namespace Chronoscope.Agent.Tests;

/// <summary>
/// Преобразование записи журнала в контрактное событие и приватность (§12, §37, §77.2).
///
/// Здесь проверяется обещание приватности на том, что действительно уйдёт в Core:
/// часть проверок смотрит не на объект наблюдения, а на <b>сериализованный</b>
/// RawEvent — именно его увидит сеть и база. Проверка «в объекте mapper нет
/// запрещённых полей» слабее: поле, добавленное позже в другом месте пути, прошло
/// бы её незамеченным.
/// </summary>
public class EventLogRecordMapperTests : IDisposable
{
    private readonly string _dataDirectory = Path.Combine(Path.GetTempPath(), $"chronoscope-eventlog-{Guid.NewGuid():N}");

    public void Dispose()
    {
        if (Directory.Exists(_dataDirectory))
        {
            Directory.Delete(_dataDirectory, recursive: true);
        }

        GC.SuppressFinalize(this);
    }

    private sealed class FixedBootSession : IBootSessionProvider
    {
        public DateTimeOffset? GetBootTimeUtc() => DateTimeOffset.Parse("2026-10-09T05:00:00Z");
    }

    private EventLogRecordMapper Mapper(TimeProvider? timeProvider = null)
    {
        var identity = AgentIdentity.Resolve(_dataDirectory, new FixedBootSession());
        return new EventLogRecordMapper(identity, "0.0.4", timeProvider);
    }

    private static EventLogObservation Observation() => new()
    {
        Channel = "System",
        Provider = "Microsoft-Windows-Kernel-General",
        EventId = 12,
        RecordId = 123_456,
        Level = 4,
        Task = 0,
        Opcode = 0,
        RecordedAt = DateTimeOffset.Parse("2026-10-09T10:42:15.281Z"),
    };

    [Fact]
    public void MapsRecordToContractEvent()
    {
        var rawEvent = Mapper().Map(Observation());

        Assert.Equal(1, rawEvent.SchemaVersion);
        Assert.Equal("windows.eventlog", rawEvent.Collector);
        Assert.Equal("event_log_record", rawEvent.PayloadType);
        Assert.Equal("0.0.4", rawEvent.CollectorVersion);
        Assert.StartsWith("raw_", rawEvent.RawEventId, StringComparison.Ordinal);
        Assert.StartsWith("host_", rawEvent.HostId, StringComparison.Ordinal);
        Assert.StartsWith("boot_", rawEvent.BootId, StringComparison.Ordinal);
    }

    /// <summary>
    /// Время записи и время наблюдения — разные величины (§24). Время записи
    /// приходит из журнала и может быть заметно раньше: часть записей System
    /// появляется позже того, о чём сообщает.
    /// </summary>
    [Fact]
    public void KeepsSourceTimestampSeparateFromObservedAt()
    {
        var timeProvider = new FixedTimeProvider(DateTimeOffset.Parse("2026-10-09T12:00:00Z"));

        var rawEvent = Mapper(timeProvider).Map(Observation());

        Assert.Equal(DateTimeOffset.Parse("2026-10-09T10:42:15.281Z"), rawEvent.SourceTimestamp);
        Assert.Equal(DateTimeOffset.Parse("2026-10-09T12:00:00Z"), rawEvent.ObservedAt);
    }

    [Fact]
    public void PayloadContainsExactlyTheAllowedFields()
    {
        var rawEvent = Mapper().Map(Observation());

        Assert.Equal(
            ["channel", "event_id", "level", "opcode", "provider", "record_id", "task"],
            rawEvent.Payload.Keys.Order(StringComparer.Ordinal).ToArray());
        Assert.Equal("System", rawEvent.Payload["channel"]);
        Assert.Equal("Microsoft-Windows-Kernel-General", rawEvent.Payload["provider"]);
        Assert.Equal(12, rawEvent.Payload["event_id"]);
        Assert.Equal(123_456L, rawEvent.Payload["record_id"]);
        Assert.Equal(4, rawEvent.Payload["level"]);
        Assert.Equal(0, rawEvent.Payload["task"]);
        Assert.Equal(0, rawEvent.Payload["opcode"]);
    }

    /// <summary>
    /// Неизвестное значение не превращается в ноль: поля просто нет.
    ///
    /// Ноль в <c>event_id</c> выглядел бы как настоящее событие с идентификатором
    /// 0, и отличить «Windows не сообщила» от «событие 0» по истории было бы
    /// нельзя. Поэтому проверяется отсутствие ключа, а не его пустота.
    /// </summary>
    [Fact]
    public void OmitsFieldsWindowsDidNotReport()
    {
        var observation = new EventLogObservation { Channel = "Application" };

        var rawEvent = Mapper().Map(observation);

        Assert.Equal(["channel"], rawEvent.Payload.Keys.ToArray());
        Assert.Null(rawEvent.SourceTimestamp);
    }

    /// <summary>
    /// Запрещённые поля отсутствуют именно в сериализованной форме — в той, что
    /// уйдёт в Core. Здесь же фиксируется, что XML, данные события и SID в payload
    /// не попадают даже случайно: набор ключей закрыт.
    /// </summary>
    [Fact]
    public void SerializedEventContainsNoForbiddenFields()
    {
        var json = RawEventJson.Serialize(Mapper().Map(Observation()));

        foreach (var forbidden in new[] { "message", "event_data", "EventData", "UserData", "xml", "Xml", "user_sid", "properties", "sid" })
        {
            Assert.DoesNotContain(forbidden, json, StringComparison.Ordinal);
        }

        using var document = JsonDocument.Parse(json);
        var payload = document.RootElement.GetProperty("payload");
        var keys = payload.EnumerateObject().Select(property => property.Name).ToArray();

        Assert.Equal(
            ["channel", "event_id", "level", "opcode", "provider", "record_id", "task"],
            keys.Order(StringComparer.Ordinal).ToArray());
    }

    /// <summary>
    /// Сообщение добавляется только тогда, когда его действительно прочитали, и
    /// тогда оно — единственное, что добавляется.
    /// </summary>
    [Fact]
    public void CapturedMessageAddsExactlyTheMessage()
    {
        var withoutMessage = Mapper().Map(Observation());
        var withMessage = Mapper().Map(Observation() with { Message = "Служба запущена: C:\\Users\\someone\\app.exe" });

        var added = withMessage.Payload.Keys.Except(withoutMessage.Payload.Keys, StringComparer.Ordinal).ToArray();

        Assert.Equal(["message"], added);
        Assert.Equal("Служба запущена: C:\\Users\\someone\\app.exe", withMessage.Payload["message"]);
    }

    /// <summary>
    /// Идентификатор записи — данные источника, а не ключ дедупликации: две разные
    /// записи с одним <c>record_id</c> (например, из разных каналов) обязаны
    /// получить разные <c>raw_event_id</c>, иначе дедупликация ingest съела бы
    /// вторую.
    /// </summary>
    [Fact]
    public void RecordIdIsNotAnIdentityOfChronoscope()
    {
        var mapper = Mapper();
        var first = mapper.Map(Observation() with { Channel = "System" });
        var second = mapper.Map(Observation() with { Channel = "Application" });

        Assert.Equal(first.Payload["record_id"], second.Payload["record_id"]);
        Assert.NotEqual(first.RawEventId, second.RawEventId);
    }

    private sealed class FixedTimeProvider(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
    }
}
