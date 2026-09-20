using System.Text.Json;

namespace Chronoscope.Agent.Logging;

/// <summary>
/// Структурированное логирование (§35).
///
/// §35 требует его от обоих runtime-компонентов, поэтому формат совпадает с тем,
/// что пишет Core: одна JSON-строка на событие, поля <c>level</c>,
/// <c>component</c>, <c>event</c>, <c>message</c> плюс произвольные поля. Поле
/// <c>event</c> — короткий машинный идентификатор, по которому можно фильтровать
/// без разбора текста.
/// </summary>
public static class JsonLog
{
    // object, а не System.Threading.Lock: последний появился только в .NET 9, а
    // проект нацелен на net8.0 (README требует .NET SDK 8+).
    private static readonly object Gate = new();

    /// <summary>Куда пишутся логи. Вынесено в свойство, чтобы тесты могли перехватывать вывод.</summary>
    public static TextWriter Output { get; set; } = Console.Error;

    public static void Info(string eventName, string message, params (string Key, object? Value)[] fields)
        => Write("info", eventName, message, fields);

    public static void Warning(string eventName, string message, params (string Key, object? Value)[] fields)
        => Write("warning", eventName, message, fields);

    public static void Error(string eventName, string message, params (string Key, object? Value)[] fields)
        => Write("error", eventName, message, fields);

    private static void Write(string level, string eventName, string message, (string Key, object? Value)[] fields)
    {
        var payload = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["timestamp"] = DateTimeOffset.UtcNow.ToString("yyyy-MM-dd'T'HH:mm:ss.fff'Z'", System.Globalization.CultureInfo.InvariantCulture),
            ["level"] = level,
            ["component"] = "agent",
            ["event"] = eventName,
            ["message"] = message,
        };

        foreach (var (key, value) in fields)
        {
            // Служебные поля не перезаписываются: иначе поле с именем "level"
            // сломало бы формат, по которому лог фильтруется.
            if (!payload.ContainsKey(key))
            {
                payload[key] = value;
            }
        }

        var line = JsonSerializer.Serialize(payload);

        // Строка целиком под блокировкой: два параллельных коллектора иначе
        // перемешали бы половины строк, и лог перестал бы быть построчным JSON.
        lock (Gate)
        {
            Output.WriteLine(line);
        }
    }
}
