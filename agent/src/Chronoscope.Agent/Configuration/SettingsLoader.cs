using System.Text.RegularExpressions;
using Tomlyn;
using Tomlyn.Model;

namespace Chronoscope.Agent.Configuration;

/// <summary>
/// Чтение конфигурации (§36).
///
/// Формат — TOML, потому что §36 предполагает один файл на весь проект: Core
/// читает из него <c>[core]</c> и <c>[storage]</c>, Agent — <c>[core]</c>,
/// <c>[agent]</c>, <c>[collectors.*]</c> и <c>[privacy]</c>. Секции другого
/// компонента пропускаются, но <b>неизвестные ключи внутри своих секций
/// отвергаются</b>: опечатка в имени параметра иначе выглядела бы как
/// «настройка не применилась». Core ведёт себя так же.
/// </summary>
public static class SettingsLoader
{
    /// <summary>Переменная окружения с путём к файлу конфигурации. Имя то же, что у Core (§36).</summary>
    public const string ConfigPathEnvironmentVariable = "CHRONOSCOPE_CONFIG";

    public const string DefaultConfigFileName = "chronoscope.toml";

    /// <summary>Хосты, на которые Agent вправе отправлять события (ADR-0007, §33).</summary>
    public static readonly IReadOnlySet<string> LoopbackHosts =
        new HashSet<string>(StringComparer.OrdinalIgnoreCase) { "127.0.0.1", "::1", "localhost" };

    private static readonly string[] CoreKeys = ["host", "port", "log_level", "max_request_bytes"];
    private static readonly string[] AgentKeys = ["batch_size", "flush_interval_ms", "buffer_capacity", "request_timeout_ms", "data_directory"];
    private static readonly string[] ProcessKeys = ["enabled", "capture_command_line", "capture_user"];
    private static readonly string[] PrivacyKeys = ["redact_command_line_patterns"];

    /// <summary>Определить путь к конфигурации: явный аргумент, затем переменная окружения, затем файл в текущем каталоге.</summary>
    public static string? ResolveConfigPath(string? explicitPath = null)
    {
        if (!string.IsNullOrWhiteSpace(explicitPath))
        {
            return explicitPath;
        }

        var fromEnvironment = Environment.GetEnvironmentVariable(ConfigPathEnvironmentVariable);
        if (!string.IsNullOrWhiteSpace(fromEnvironment))
        {
            return fromEnvironment;
        }

        return File.Exists(DefaultConfigFileName) ? DefaultConfigFileName : null;
    }

    /// <summary>Прочитать настройки, наложив файл поверх значений по умолчанию.</summary>
    public static AgentSettings Load(string? configPath = null)
    {
        var path = ResolveConfigPath(configPath);
        if (path is null)
        {
            return Defaults();
        }

        if (!File.Exists(path))
        {
            throw new ConfigurationException($"конфигурационный файл не найден: {path}");
        }

        string text;
        try
        {
            // BOM допускается по той же причине, что и в Core: Windows-first
            // инструмент (ADR-0006), а «Блокнот» и PowerShell записывают BOM по
            // умолчанию. TOML формально этого не разрешает, но падение на BOM
            // означало бы, что штатно сохранённый файл не читается.
            text = File.ReadAllText(path).TrimStart('\uFEFF');
        }
        catch (IOException exception)
        {
            throw new ConfigurationException($"не удалось прочитать {path}: {exception.Message}", exception);
        }

        return Parse(text, path);
    }

    /// <summary>Разобрать конфигурацию из текста. Вынесено отдельно ради тестов.</summary>
    public static AgentSettings Parse(string tomlText, string? sourcePath = null)
    {
        var where = sourcePath is null ? string.Empty : $" ({sourcePath})";

        TomlTable root;
        try
        {
            // Tomlyn 2.x — переработанный API в стиле System.Text.Json: статического
            // Toml.Parse/ToModel из 0.x в нём нет. Модель TomlTable осталась и
            // подходит как нельзя лучше: она сохраняет порядок ключей, а нам нужен
            // именно словарь, чтобы самим проверять состав ключей и отвергать
            // опечатки — привязка к классу просто проигнорировала бы лишний ключ.
            root = TomlSerializer.Deserialize<TomlTable>(tomlText, TomlSerializerOptions.Default);
        }
        catch (TomlException exception)
        {
            throw new ConfigurationException($"не удалось разобрать конфигурацию{where}: {exception.Message}", exception);
        }

        var core = Section(root, "core", sourcePath);
        RejectUnknownKeys(core, "core", CoreKeys, sourcePath);

        var agent = Section(root, "agent", sourcePath);
        RejectUnknownKeys(agent, "agent", AgentKeys, sourcePath);

        var process = Section(root, "collectors.process", sourcePath);
        RejectUnknownKeys(process, "collectors.process", ProcessKeys, sourcePath);

        var privacy = Section(root, "privacy", sourcePath);
        RejectUnknownKeys(privacy, "privacy", PrivacyKeys, sourcePath);

        var host = StringValue(core, "host", "127.0.0.1", sourcePath);
        var port = IntValue(core, "port", CoreEndpoint.DefaultPort, sourcePath);

        // ADR-0007 / §33: Core слушает только loopback. Если бы Agent мог
        // отправлять события на внешний адрес, это была бы утечка собранных
        // данных о работе компьютера за пределы машины — то, чего продукт
        // обязан не делать. Поэтому адрес проверяется здесь, а не «на совесть».
        if (!LoopbackHosts.Contains(host))
        {
            throw new ConfigurationException(
                $"core.host='{host}' не является loopback-адресом. Agent доставляет события только на "
                + $"{string.Join(", ", LoopbackHosts.Order(StringComparer.Ordinal))} (§33, ADR-0007)");
        }

        if (port is < 1 or > 65535)
        {
            throw new ConfigurationException($"core.port={port} вне диапазона 1..65535");
        }

        var delivery = new DeliverySettings
        {
            BatchSize = PositiveIntValue(agent, "batch_size", 50, sourcePath),
            FlushIntervalMs = PositiveIntValue(agent, "flush_interval_ms", 500, sourcePath),
            BufferCapacity = PositiveIntValue(agent, "buffer_capacity", 10_000, sourcePath),
            RequestTimeoutMs = PositiveIntValue(agent, "request_timeout_ms", 10_000, sourcePath),
        };

        if (delivery.BatchSize > delivery.BufferCapacity)
        {
            // Не ошибка сама по себе, но означает, что пакет никогда не соберётся
            // полностью, и flush будет срабатывать только по таймеру. Сообщаем
            // явно, чтобы это не выглядело как «доставка тормозит».
            throw new ConfigurationException(
                $"agent.batch_size={delivery.BatchSize} больше agent.buffer_capacity={delivery.BufferCapacity}: "
                + "пакет такого размера не соберётся никогда");
        }

        var processSettings = new ProcessCollectorSettings
        {
            Enabled = BoolValue(process, "enabled", true, sourcePath),
            CaptureCommandLine = BoolValue(process, "capture_command_line", false, sourcePath),
            CaptureUser = BoolValue(process, "capture_user", false, sourcePath),
        };

        var patterns = StringArrayValue(privacy, "redact_command_line_patterns", sourcePath);
        foreach (var pattern in patterns)
        {
            try
            {
                _ = new Regex(pattern);
            }
            catch (ArgumentException exception)
            {
                throw new ConfigurationException(
                    $"privacy.redact_command_line_patterns: '{pattern}' не является корректным регулярным выражением: {exception.Message}",
                    exception);
            }
        }

        return new AgentSettings
        {
            Core = new CoreEndpoint { Host = host, Port = port },
            Delivery = delivery,
            Process = processSettings,
            Privacy = new PrivacySettings { RedactCommandLinePatterns = patterns },
            DataDirectory = StringValue(agent, "data_directory", DefaultDataDirectory(), sourcePath),
        };
    }

    /// <summary>Настройки по умолчанию — на случай, когда файла конфигурации нет вовсе (§36).</summary>
    public static AgentSettings Defaults() => new()
    {
        Core = new CoreEndpoint { Host = "127.0.0.1", Port = CoreEndpoint.DefaultPort },
        DataDirectory = DefaultDataDirectory(),
    };

    /// <summary>
    /// Каталог локальных данных Agent.
    ///
    /// <c>%LOCALAPPDATA%\Chronoscope</c>, а не каталог запуска: <c>host_id</c>
    /// обязан переживать перезапуск и не зависеть от того, откуда запущен Agent.
    /// </summary>
    public static string DefaultDataDirectory() => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Chronoscope");

    private static TomlTable Section(TomlTable root, string name, string? sourcePath)
    {
        if (!TryGetPath(root, name, out var value))
        {
            return new TomlTable();
        }

        return value as TomlTable
            ?? throw new ConfigurationException($"{Where(sourcePath)}секция [{name}] должна быть таблицей");
    }

    /// <summary>Достать вложенную таблицу по пути вида <c>collectors.process</c>.</summary>
    private static bool TryGetPath(TomlTable root, string dottedPath, out object? value)
    {
        value = root;
        foreach (var segment in dottedPath.Split('.'))
        {
            if (value is not TomlTable table || !table.TryGetValue(segment, out value))
            {
                value = null;
                return false;
            }
        }

        return true;
    }

    private static void RejectUnknownKeys(TomlTable section, string name, string[] known, string? sourcePath)
    {
        var unknown = section.Keys.Where(key => !known.Contains(key, StringComparer.Ordinal)).Order(StringComparer.Ordinal).ToArray();
        if (unknown.Length > 0)
        {
            throw new ConfigurationException(
                $"{Where(sourcePath)}неизвестные ключи в секции [{name}]: {string.Join(", ", unknown)}. "
                + $"Известные: {string.Join(", ", known.Order(StringComparer.Ordinal))}");
        }
    }

    private static string Where(string? sourcePath) => sourcePath is null ? string.Empty : $"{sourcePath}: ";

    private static string StringValue(TomlTable section, string key, string fallback, string? sourcePath)
    {
        if (!section.TryGetValue(key, out var value))
        {
            return fallback;
        }

        return value as string
            ?? throw new ConfigurationException($"{Where(sourcePath)}{key}: ожидалась строка, получено {value?.GetType().Name ?? "null"}");
    }

    private static bool BoolValue(TomlTable section, string key, bool fallback, string? sourcePath)
    {
        if (!section.TryGetValue(key, out var value))
        {
            return fallback;
        }

        return value is bool flag
            ? flag
            : throw new ConfigurationException($"{Where(sourcePath)}{key}: ожидалось true или false, получено {value?.GetType().Name ?? "null"}");
    }

    private static int IntValue(TomlTable section, string key, int fallback, string? sourcePath)
    {
        if (!section.TryGetValue(key, out var value))
        {
            return fallback;
        }

        return value is long number
            ? checked((int)number)
            : throw new ConfigurationException($"{Where(sourcePath)}{key}: ожидалось целое число, получено {value?.GetType().Name ?? "null"}");
    }

    private static int PositiveIntValue(TomlTable section, string key, int fallback, string? sourcePath)
    {
        var value = IntValue(section, key, fallback, sourcePath);
        return value > 0
            ? value
            : throw new ConfigurationException($"{Where(sourcePath)}{key}={value}: должно быть положительным");
    }

    private static IReadOnlyList<string> StringArrayValue(TomlTable section, string key, string? sourcePath)
    {
        if (!section.TryGetValue(key, out var value))
        {
            return [];
        }

        if (value is not TomlArray array)
        {
            throw new ConfigurationException($"{Where(sourcePath)}{key}: ожидался массив строк");
        }

        var result = new List<string>(array.Count);
        foreach (var item in array)
        {
            result.Add(item as string
                ?? throw new ConfigurationException($"{Where(sourcePath)}{key}: ожидался массив строк, элемент {item?.GetType().Name ?? "null"}"));
        }

        return result;
    }
}
