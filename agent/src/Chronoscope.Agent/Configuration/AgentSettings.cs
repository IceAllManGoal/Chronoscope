namespace Chronoscope.Agent.Configuration;

/// <summary>Некорректная конфигурация Agent. Соответствует §36 и политике §60.</summary>
public sealed class ConfigurationException : Exception
{
    public ConfigurationException(string message) : base(message)
    {
    }

    public ConfigurationException(string message, Exception inner) : base(message, inner)
    {
    }
}

/// <summary>
/// Куда Agent доставляет события.
///
/// Адрес берётся из секции <c>[core]</c> того же файла (§36 предполагает единый
/// конфигурационный файл проекта), поэтому отдельного ключа для URL нет: два
/// независимых описания одного адреса рано или поздно разошлись бы.
/// </summary>
public sealed record CoreEndpoint
{
    public const int DefaultPort = 7342;

    /// <summary>Хост Core. Допустим только loopback — см. <see cref="SettingsLoader"/>.</summary>
    public required string Host { get; init; }

    public required int Port { get; init; }

    /// <summary>Путь эндпоинта приёма (§31).</summary>
    public string IngestPath { get; init; } = "/api/v1/ingest/raw-events";

    public Uri IngestUri => new($"http://{Host}:{Port}{IngestPath}");
}

/// <summary>Параметры доставки и буферизации (§34).</summary>
public sealed record DeliverySettings
{
    /// <summary>Сколько событий отправляется одним пакетом. Значение по умолчанию — из §36.</summary>
    public int BatchSize { get; init; } = 50;

    /// <summary>Как долго ждать накопления пакета, мс. Значение по умолчанию — из §36.</summary>
    public int FlushIntervalMs { get; init; } = 500;

    /// <summary>
    /// Ёмкость ограниченной очереди (§34). Ключа в §36 нет: там сказано только,
    /// что буфер обязан быть ограниченным, а конкретное число — вопрос ресурсов.
    /// </summary>
    public int BufferCapacity { get; init; } = 10_000;

    /// <summary>Таймаут одной попытки отправки пакета, мс.</summary>
    public int RequestTimeoutMs { get; init; } = 10_000;
}

/// <summary>Настройки коллектора процессов (§8.4, §37).</summary>
public sealed record ProcessCollectorSettings
{
    public bool Enabled { get; init; } = true;

    /// <summary>
    /// Собирать командную строку. По умолчанию <b>выключено</b>.
    ///
    /// §36 показывает в примере <c>true</c>, а §37 вводит уровни приватности и
    /// прямо говорит, что командная строка может содержать токены, пароли, ключи
    /// и приватные пути. Пример §36 иллюстрирует, где живёт конфигурация, а не
    /// какой уровень сбора обязателен; при расхождении выбран более осторожный
    /// вариант, потому что §6.2 объявляет privacy by default, а §37 требует не
    /// откладывать приватность «на потом». Поле в контракте необязательное, так
    /// что собирать меньше всегда допустимо.
    /// </summary>
    public bool CaptureCommandLine { get; init; }

    /// <summary>SID пользователя. По умолчанию выключено — по той же причине, что и выше.</summary>
    public bool CaptureUser { get; init; }
}

/// <summary>Приватность (§37, §38).</summary>
public sealed record PrivacySettings
{
    /// <summary>Регулярные выражения, совпадения которых вырезаются из командной строки (§38).</summary>
    public IReadOnlyList<string> RedactCommandLinePatterns { get; init; } = [];
}

/// <summary>Полная конфигурация Agent.</summary>
public sealed record AgentSettings
{
    public required CoreEndpoint Core { get; init; }

    public DeliverySettings Delivery { get; init; } = new();

    public ProcessCollectorSettings Process { get; init; } = new();

    public PrivacySettings Privacy { get; init; } = new();

    /// <summary>Каталог локальных данных Agent: там лежит <c>host_id</c>.</summary>
    public required string DataDirectory { get; init; }
}
