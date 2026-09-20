using Chronoscope.Agent.Configuration;

namespace Chronoscope.Agent.Tests;

/// <summary>
/// Конфигурация Agent (§36).
///
/// Главное, что здесь проверяется, — поведение при общем файле проекта:
/// <c>[core]</c> и <c>[storage]</c> принадлежат Core, и Agent обязан читать
/// свои ключи из того же файла, не спотыкаясь о чужие. При этом опечатка в
/// собственной секции должна приводить к явной ошибке, иначе она выглядит как
/// «настройка не применилась».
/// </summary>
public class SettingsLoaderTests
{
    [Fact]
    public void DefaultsAreUsedWhenNoFileExists()
    {
        var settings = SettingsLoader.Defaults();

        Assert.Equal("127.0.0.1", settings.Core.Host);
        Assert.Equal(7342, settings.Core.Port);
        Assert.Equal(50, settings.Delivery.BatchSize);
        Assert.Equal(500, settings.Delivery.FlushIntervalMs);
        Assert.Equal(10_000, settings.Delivery.BufferCapacity);
        Assert.True(settings.Process.Enabled);
    }

    /// <summary>
    /// Уровень сбора по умолчанию — Minimal: командная строка и пользователь не
    /// собираются. §36 показывает в примере <c>true</c>, но §37 прямо говорит,
    /// что командная строка может содержать токены и пароли, а §6.2 объявляет
    /// privacy by default. Поля контракта необязательные, поэтому собирать
    /// меньше всегда допустимо, а собирать больше — осознанный выбор.
    /// </summary>
    [Fact]
    public void PrivacyPreservingDefaults()
    {
        var settings = SettingsLoader.Defaults();

        Assert.False(settings.Process.CaptureCommandLine);
        Assert.False(settings.Process.CaptureUser);
        Assert.Empty(settings.Privacy.RedactCommandLinePatterns);
    }

    [Fact]
    public void ReadsEndpointFromCoreSection()
    {
        var settings = SettingsLoader.Parse("""
            [core]
            host = "localhost"
            port = 7442
            """);

        Assert.Equal("localhost", settings.Core.Host);
        Assert.Equal(7442, settings.Core.Port);
        Assert.Equal("http://localhost:7442/api/v1/ingest/raw-events", settings.Core.IngestUri.ToString());
    }

    /// <summary>
    /// ADR-0007 / §33. Если бы Agent мог отправлять события на внешний адрес, это
    /// была бы утечка собранных данных о работе компьютера за пределы машины.
    /// Проверка живёт в конфигурации, поэтому небезопасная настройка не может
    /// возникнуть вообще — ни через файл, ни через переменную окружения.
    /// </summary>
    [Theory]
    [InlineData("0.0.0.0")]
    [InlineData("192.168.1.10")]
    [InlineData("example.com")]
    [InlineData("10.0.0.5")]
    public void RejectsNonLoopbackCoreHost(string host)
    {
        var exception = Assert.Throws<ConfigurationException>(() =>
            SettingsLoader.Parse($"""
                [core]
                host = "{host}"
                """));

        Assert.Contains("loopback", exception.Message, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("127.0.0.1")]
    [InlineData("::1")]
    [InlineData("localhost")]
    [InlineData("LOCALHOST")]
    public void AcceptsLoopbackVariants(string host)
    {
        var settings = SettingsLoader.Parse($"""
            [core]
            host = "{host}"
            """);

        Assert.Equal(host, settings.Core.Host);
    }

    /// <summary>Общий файл проекта: секцию Core, которая Agent'у не нужна, он обязан пропустить.</summary>
    [Fact]
    public void IgnoresSectionsOfOtherComponents()
    {
        var settings = SettingsLoader.Parse("""
            [core]
            host = "127.0.0.1"
            port = 7342
            log_level = "DEBUG"
            max_request_bytes = 5242880

            [storage]
            database_path = "./data/chronoscope.db"
            busy_timeout_ms = 5000

            [agent]
            batch_size = 10
            """);

        Assert.Equal(10, settings.Delivery.BatchSize);
    }

    [Fact]
    public void RejectsUnknownKeyInOwnSection()
    {
        var exception = Assert.Throws<ConfigurationException>(() =>
            SettingsLoader.Parse("""
                [agent]
                batch_sise = 10
                """));

        Assert.Contains("batch_sise", exception.Message, StringComparison.Ordinal);
        Assert.Contains("неизвестные ключи", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void RejectsUnknownKeyInCoreSection()
    {
        Assert.Throws<ConfigurationException>(() =>
            SettingsLoader.Parse("""
                [core]
                nonsense = 1
                """));
    }

    [Fact]
    public void ReadsCollectorAndPrivacySections()
    {
        var settings = SettingsLoader.Parse("""
            [collectors.process]
            enabled = false
            capture_command_line = true
            capture_user = true

            [privacy]
            redact_command_line_patterns = ["--token=\\S+", "--password=\\S+"]
            """);

        Assert.False(settings.Process.Enabled);
        Assert.True(settings.Process.CaptureCommandLine);
        Assert.True(settings.Process.CaptureUser);
        Assert.Equal(["--token=\\S+", "--password=\\S+"], settings.Privacy.RedactCommandLinePatterns);
    }

    [Fact]
    public void RejectsInvalidRedactionPattern()
    {
        var exception = Assert.Throws<ConfigurationException>(() =>
            SettingsLoader.Parse("""
                [privacy]
                redact_command_line_patterns = ["([unclosed"]
                """));

        Assert.Contains("регулярным выражением", exception.Message, StringComparison.Ordinal);
    }

    /// <summary>
    /// Пакет больше буфера не соберётся никогда, и доставка начнёт работать
    /// только по таймеру. Это не разрушительно, но выглядит как «доставка
    /// тормозит», поэтому сообщается явно.
    /// </summary>
    [Fact]
    public void RejectsBatchSizeLargerThanBuffer()
    {
        var exception = Assert.Throws<ConfigurationException>(() =>
            SettingsLoader.Parse("""
                [agent]
                batch_size = 100
                buffer_capacity = 10
                """));

        Assert.Contains("не соберётся", exception.Message, StringComparison.Ordinal);
    }

    [Theory]
    [InlineData("batch_size = 0")]
    [InlineData("flush_interval_ms = -1")]
    [InlineData("buffer_capacity = 0")]
    public void RejectsNonPositiveNumbers(string line)
    {
        Assert.Throws<ConfigurationException>(() => SettingsLoader.Parse($"""
            [agent]
            {line}
            """));
    }

    [Fact]
    public void RejectsValueOfWrongType()
    {
        Assert.Throws<ConfigurationException>(() => SettingsLoader.Parse("""
            [agent]
            batch_size = "много"
            """));
    }

    [Fact]
    public void RejectsBrokenToml()
    {
        var exception = Assert.Throws<ConfigurationException>(() => SettingsLoader.Parse("[agent\nbatch_size = 1"));

        Assert.Contains("не удалось разобрать", exception.Message, StringComparison.Ordinal);
    }

    /// <summary>
    /// BOM допускается: Windows-first инструмент (ADR-0006), а «Блокнот» и
    /// PowerShell записывают его по умолчанию. Падение на BOM означало бы, что
    /// штатно сохранённый файл не читается.
    /// </summary>
    [Fact]
    public void ToleratesByteOrderMark()
    {
        var settings = SettingsLoader.Parse("\uFEFF[agent]\nbatch_size = 7\n");

        Assert.Equal(7, settings.Delivery.BatchSize);
    }

    [Fact]
    public void ReadsExplicitFilePathAndFailsWhenMissing()
    {
        var path = Path.Combine(Path.GetTempPath(), $"chronoscope-missing-{Guid.NewGuid():N}.toml");

        var exception = Assert.Throws<ConfigurationException>(() => SettingsLoader.Load(path));

        Assert.Contains("не найден", exception.Message, StringComparison.Ordinal);
    }
}
