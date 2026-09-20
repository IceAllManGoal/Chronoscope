using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Chronoscope.Agent.Contract;

/// <summary>
/// Сериализация в форму, которую требует <c>shared/schemas/raw-event.schema.json</c>.
///
/// Ручная работа здесь нужна из-за времени. Сериализатор .NET по умолчанию пишет
/// смещение (<c>+00:00</c>), а схема требует суффикс <c>Z</c> и отвергает
/// смещения; кроме того, <c>format: date-time</c> сам по себе UTC не
/// гарантирует — именно поэтому в схеме рядом стоит <c>pattern</c>. Поэтому
/// время пишет <see cref="UtcTimestampConverter"/> в той же форме, что и
/// <c>format_utc</c> в Core: миллисекунды, когда точность кратна миллисекунде,
/// иначе микросекунды.
/// </summary>
public static class RawEventJson
{
    /// <summary>Версия схемы, которую поддерживает эта версия Agent (§52).</summary>
    public const int SupportedSchemaVersion = 1;

    private static readonly JsonSerializerOptions Options = BuildOptions();

    /// <summary>Сериализовать одно событие — так, как его увидит Core.</summary>
    public static string Serialize(RawEvent rawEvent) => JsonSerializer.Serialize(rawEvent, Options);

    /// <summary>
    /// Собрать envelope пакета: <c>{"schema_version": 1, "events": [...]}</c>.
    ///
    /// Версия есть и у envelope: §52 требует её у каждого транспортного объекта,
    /// а не только у вложенных. Схема пакета объявлена с
    /// <c>additionalProperties: false</c>, поэтому полей ровно два.
    /// </summary>
    public static string SerializeBatch(IReadOnlyList<RawEvent> events)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions { Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping }))
        {
            writer.WriteStartObject();
            writer.WriteNumber("schema_version", SupportedSchemaVersion);
            writer.WritePropertyName("events");
            writer.WriteStartArray();
            foreach (var rawEvent in events)
            {
                JsonSerializer.Serialize(writer, rawEvent, Options);
            }

            writer.WriteEndArray();
            writer.WriteEndObject();
        }

        return System.Text.Encoding.UTF8.GetString(stream.ToArray());
    }

    private static JsonSerializerOptions BuildOptions() => new()
    {
        // Null не опускается: source_timestamp и boot_id объявлены в схеме как
        // oneOf [значение, null], то есть отсутствие поля — это уже другая форма
        // объекта, а не «то же самое».
        DefaultIgnoreCondition = JsonIgnoreCondition.Never,
        Converters = { new UtcTimestampConverter() },
    };
}

/// <summary>
/// Время в RFC 3339 UTC с обязательным суффиксом <c>Z</c> (§24, docs/EVENT_MODEL.md §3).
///
/// Смещения вида <c>+05:00</c> формально UTC-эквивалентны, но схема их
/// отвергает: иначе правило «хранится только UTC» осталось бы пожеланием.
/// Значение, пришедшее с локальным смещением, приводится к UTC — это
/// преобразование без потери информации, в отличие от наивного времени.
/// </summary>
public sealed class UtcTimestampConverter : JsonConverter<DateTimeOffset>
{
    private const string WholeSecondsFormat = "yyyy-MM-dd'T'HH:mm:ss'Z'";
    private const string MillisecondsFormat = "yyyy-MM-dd'T'HH:mm:ss.fff'Z'";
    private const string MicrosecondsFormat = "yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'";

    public override DateTimeOffset Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
        => DateTimeOffset.Parse(reader.GetString()!, System.Globalization.CultureInfo.InvariantCulture, System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal);

    public override void Write(Utf8JsonWriter writer, DateTimeOffset value, JsonSerializerOptions options)
        => writer.WriteStringValue(Format(value));

    /// <summary>Отображающая форма времени: миллисекунды, когда точность кратна миллисекунде, иначе микросекунды.</summary>
    public static string Format(DateTimeOffset value)
    {
        var utc = value.ToUniversalTime();
        var microseconds = utc.Ticks % TimeSpan.TicksPerSecond / 10;

        var format = microseconds switch
        {
            0 => WholeSecondsFormat,
            _ when microseconds % 1000 == 0 => MillisecondsFormat,
            _ => MicrosecondsFormat,
        };

        return utc.ToString(format, System.Globalization.CultureInfo.InvariantCulture);
    }
}
