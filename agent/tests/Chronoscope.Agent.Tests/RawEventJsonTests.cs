using System.Text.Json;
using Chronoscope.Agent.Contract;

namespace Chronoscope.Agent.Tests;

/// <summary>
/// Проверки формы JSON, которую получает Core.
///
/// Это контракт, а не деталь реализации: <c>raw-event.schema.json</c> объявлена
/// с <c>additionalProperties: false</c>, а Pydantic-модель Core — с
/// <c>extra="forbid"</c>. Лишнее поле, отсутствующее обязательное или время со
/// смещением вместо суффикса <c>Z</c> означают не «предупреждение», а отказ
/// принять событие целиком.
/// </summary>
public class RawEventJsonTests
{
    private static readonly string[] ExpectedEnvelopeKeys =
    [
        "schema_version",
        "raw_event_id",
        "collector",
        "collector_version",
        "observed_at",
        "source_timestamp",
        "host_id",
        "boot_id",
        "payload_type",
        "payload",
    ];

    private static RawEvent SampleEvent() => new()
    {
        SchemaVersion = RawEventJson.SupportedSchemaVersion,
        RawEventId = "raw_01K5R8Z9M5P8W3X7Y2C4E6G8J0",
        Collector = "windows.process",
        CollectorVersion = "0.0.1",
        ObservedAt = DateTimeOffset.Parse("2026-09-18T10:42:15.281Z"),
        SourceTimestamp = DateTimeOffset.Parse("2026-09-18T10:42:15.220Z"),
        HostId = "host_01K5R8Z9M4Q7T2V6X1B3D5F7H9",
        BootId = "boot_01K5R8Z9M4Q7T2V6X1B3D5F7HA",
        PayloadType = "process_start",
        Payload = new Dictionary<string, object?>
        {
            ["pid"] = 9812,
            ["parent_pid"] = 4312,
            ["name"] = "notepad.exe",
            ["path"] = @"C:\Windows\System32\notepad.exe",
            ["process_started_at"] = "2026-09-18T10:42:15.220Z",
        },
    };

    [Fact]
    public void EnvelopeCarriesExactlyTheContractFields()
    {
        using var document = JsonDocument.Parse(RawEventJson.Serialize(SampleEvent()));

        var actual = document.RootElement.EnumerateObject().Select(property => property.Name).ToArray();

        Assert.Equal(ExpectedEnvelopeKeys, actual);
    }

    [Fact]
    public void TimestampsUseZSuffixAndNoOffset()
    {
        using var document = JsonDocument.Parse(RawEventJson.Serialize(SampleEvent()));
        var root = document.RootElement;

        var observedAt = root.GetProperty("observed_at").GetString()!;
        var sourceTimestamp = root.GetProperty("source_timestamp").GetString()!;

        Assert.EndsWith("Z", observedAt, StringComparison.Ordinal);
        Assert.EndsWith("Z", sourceTimestamp, StringComparison.Ordinal);
        Assert.DoesNotContain("+", observedAt, StringComparison.Ordinal);
        Assert.Equal("2026-09-18T10:42:15.281Z", observedAt);
        Assert.Equal("2026-09-18T10:42:15.220Z", sourceTimestamp);
        // Тот же pattern, что в схеме: суффикс Z обязателен.
        Assert.Matches(@"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$", observedAt);
    }

    /// <summary>
    /// Null-поля обязаны присутствовать: схема объявляет их как
    /// <c>oneOf [значение, null]</c>, и пропуск поля — это другая форма объекта.
    /// </summary>
    [Fact]
    public void NullFieldsAreWrittenRatherThanOmitted()
    {
        var withoutOptionals = SampleEvent() with { SourceTimestamp = null, BootId = null };

        using var document = JsonDocument.Parse(RawEventJson.Serialize(withoutOptionals));
        var root = document.RootElement;

        Assert.Equal(JsonValueKind.Null, root.GetProperty("source_timestamp").ValueKind);
        Assert.Equal(JsonValueKind.Null, root.GetProperty("boot_id").ValueKind);
        Assert.Equal(ExpectedEnvelopeKeys.Length, root.EnumerateObject().Count());
    }

    [Fact]
    public void PayloadIsPreservedAsIs()
    {
        using var document = JsonDocument.Parse(RawEventJson.Serialize(SampleEvent()));
        var payload = document.RootElement.GetProperty("payload");

        Assert.Equal(9812, payload.GetProperty("pid").GetInt32());
        Assert.Equal(4312, payload.GetProperty("parent_pid").GetInt32());
        Assert.Equal("notepad.exe", payload.GetProperty("name").GetString());
        Assert.Equal(@"C:\Windows\System32\notepad.exe", payload.GetProperty("path").GetString());
    }

    [Fact]
    public void BatchCarriesVersionAndEvents()
    {
        using var document = JsonDocument.Parse(RawEventJson.SerializeBatch([SampleEvent(), SampleEvent()]));
        var root = document.RootElement;

        Assert.Equal(RawEventJson.SupportedSchemaVersion, root.GetProperty("schema_version").GetInt32());
        Assert.Equal(2, root.GetProperty("events").GetArrayLength());
        Assert.Equal(2, root.EnumerateObject().Count());
    }

    [Fact]
    public void BatchEventsAreFullEnvelopes()
    {
        using var document = JsonDocument.Parse(RawEventJson.SerializeBatch([SampleEvent()]));

        var first = document.RootElement.GetProperty("events")[0];
        var actual = first.EnumerateObject().Select(property => property.Name).ToArray();

        Assert.Equal(ExpectedEnvelopeKeys, actual);
    }
}

/// <summary>
/// Форма времени — та же, что у <c>format_utc</c> в Core: миллисекунды, когда
/// точность кратна миллисекунде, иначе микросекунды. Расхождение здесь не
/// косметическое: суффикс <c>Z</c> обязателен, а смещение отвергается.
/// </summary>
public class UtcTimestampConverterTests
{
    [Theory]
    [InlineData("2026-09-18T10:42:15Z", "2026-09-18T10:42:15Z")]
    [InlineData("2026-09-18T10:42:15.220Z", "2026-09-18T10:42:15.220Z")]
    [InlineData("2026-09-18T10:42:15.2205Z", "2026-09-18T10:42:15.220500Z")]
    public void FormatsLikeCore(string input, string expected)
    {
        var value = DateTimeOffset.Parse(input, System.Globalization.CultureInfo.InvariantCulture);

        Assert.Equal(expected, UtcTimestampConverter.Format(value));
    }

    [Fact]
    public void ConvertsLocalOffsetToUtc()
    {
        var withOffset = DateTimeOffset.Parse("2026-09-18T15:42:15.220+05:00", System.Globalization.CultureInfo.InvariantCulture);

        Assert.Equal("2026-09-18T10:42:15.220Z", UtcTimestampConverter.Format(withOffset));
    }
}
