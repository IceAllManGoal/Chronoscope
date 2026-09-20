using Chronoscope.Agent.Collectors;
using Chronoscope.Agent.Configuration;
using Chronoscope.Agent.Identity;

namespace Chronoscope.Agent.Tests;

/// <summary>
/// Преобразование наблюдения за процессом в контрактное событие.
///
/// Здесь проверяется главное обещание приватности: при выключенной настройке
/// чувствительное поле <b>отсутствует</b> в событии, а не приходит пустым.
/// Разница существенная — пустое поле выглядело бы как «источник не сообщил», и
/// отличить «не собирали» от «не удалось получить» стало бы невозможно.
/// </summary>
public class ProcessEventMapperTests : IDisposable
{
    private readonly string _dataDirectory = Path.Combine(Path.GetTempPath(), $"chronoscope-mapper-{Guid.NewGuid():N}");

    public void Dispose()
    {
        if (Directory.Exists(_dataDirectory))
        {
            Directory.Delete(_dataDirectory, recursive: true);
        }

        GC.SuppressFinalize(this);
    }

    private sealed class FixedBootSession : Chronoscope.Agent.Abstractions.IBootSessionProvider
    {
        public DateTimeOffset? GetBootTimeUtc() => DateTimeOffset.Parse("2026-09-18T05:00:00Z");
    }

    private static ProcessObservation StartObservation() => new()
    {
        ProcessId = 9812,
        ParentProcessId = 4312,
        Name = "notepad.exe",
        ExecutablePath = @"C:\Windows\System32\notepad.exe",
        CommandLine = "notepad.exe --token=SECRET --password=hunter2",
        UserSid = "S-1-5-21-1000000000-2000000000-3000000000-1001",
        StartedAt = DateTimeOffset.Parse("2026-09-18T10:42:15.220Z"),
    };

    private ProcessEventMapper Mapper(ProcessCollectorSettings settings, PrivacySettings? privacy = null)
    {
        var identity = AgentIdentity.Resolve(_dataDirectory, new FixedBootSession());
        return new ProcessEventMapper(identity, settings, privacy ?? new PrivacySettings(), "0.0.1");
    }

    [Fact]
    public void MapsStartToContractEvent()
    {
        var rawEvent = Mapper(new ProcessCollectorSettings()).Map(StartObservation());

        Assert.Equal(1, rawEvent.SchemaVersion);
        Assert.Equal("windows.process", rawEvent.Collector);
        Assert.Equal("process_start", rawEvent.PayloadType);
        Assert.Equal("0.0.1", rawEvent.CollectorVersion);
        Assert.StartsWith("raw_", rawEvent.RawEventId, StringComparison.Ordinal);
        Assert.StartsWith("host_", rawEvent.HostId, StringComparison.Ordinal);
        Assert.StartsWith("boot_", rawEvent.BootId, StringComparison.Ordinal);
    }

    [Fact]
    public void MapsExitToProcessExitPayloadType()
    {
        var observation = StartObservation() with { IsExit = true, ExitedAt = DateTimeOffset.Parse("2026-09-18T10:44:03.114Z") };

        var rawEvent = Mapper(new ProcessCollectorSettings()).Map(observation);

        Assert.Equal("process_exit", rawEvent.PayloadType);
        Assert.Equal("2026-09-18T10:44:03.114Z", rawEvent.Payload["exited_at"]);
    }

    /// <summary>
    /// §14: время старта обязано быть и в событии выхода, иначе Core выведет по
    /// нему другой process_instance_id и связь «запустился → завершился» пропадёт.
    /// </summary>
    [Fact]
    public void CarriesProcessStartTimeForBothStartAndExit()
    {
        var settings = new ProcessCollectorSettings();
        var mapper = Mapper(settings);

        var start = mapper.Map(StartObservation());
        var exit = mapper.Map(StartObservation() with { IsExit = true, ExitedAt = DateTimeOffset.Parse("2026-09-18T10:44:03.114Z") });

        Assert.Equal("2026-09-18T10:42:15.220Z", start.Payload["process_started_at"]);
        Assert.Equal("2026-09-18T10:42:15.220Z", exit.Payload["process_started_at"]);
    }

    [Fact]
    public void OmitsSensitiveFieldsByDefault()
    {
        var rawEvent = Mapper(new ProcessCollectorSettings()).Map(StartObservation());

        Assert.Equal(9812, rawEvent.Payload["pid"]);
        Assert.Equal(4312, rawEvent.Payload["parent_pid"]);
        Assert.Equal("notepad.exe", rawEvent.Payload["name"]);

        Assert.False(rawEvent.Payload.ContainsKey("path"));
        Assert.False(rawEvent.Payload.ContainsKey("command_line"));
        Assert.False(rawEvent.Payload.ContainsKey("user_sid"));
    }

    [Fact]
    public void IncludesSensitiveFieldsWhenExplicitlyEnabled()
    {
        var settings = new ProcessCollectorSettings
        {
            CapturePath = true,
            CaptureCommandLine = true,
            CaptureUser = true,
        };

        var rawEvent = Mapper(settings).Map(StartObservation());

        Assert.Equal(@"C:\Windows\System32\notepad.exe", rawEvent.Payload["path"]);
        Assert.Equal("notepad.exe --token=SECRET --password=hunter2", rawEvent.Payload["command_line"]);
        Assert.Equal("S-1-5-21-1000000000-2000000000-3000000000-1001", rawEvent.Payload["user_sid"]);
    }

    [Fact]
    public void RedactsCommandLineMatches()
    {
        var settings = new ProcessCollectorSettings { CaptureCommandLine = true };
        var privacy = new PrivacySettings
        {
            RedactCommandLinePatterns = [@"--token=\S+", @"--password=\S+"],
        };

        var rawEvent = Mapper(settings, privacy).Map(StartObservation());

        Assert.Equal("notepad.exe *** ***", rawEvent.Payload["command_line"]);
    }

    [Fact]
    public void OmitsFieldsTheSourceDidNotProvide()
    {
        var observation = new ProcessObservation
        {
            ProcessId = 42,
            Name = "svchost.exe",
            StartedAt = DateTimeOffset.Parse("2026-09-18T10:42:15.220Z"),
        };

        var rawEvent = Mapper(new ProcessCollectorSettings
        {
            CapturePath = true,
            CaptureCommandLine = true,
            CaptureUser = true,
        }).Map(observation);

        Assert.False(rawEvent.Payload.ContainsKey("parent_pid"));
        Assert.False(rawEvent.Payload.ContainsKey("path"));
        Assert.False(rawEvent.Payload.ContainsKey("command_line"));
        Assert.False(rawEvent.Payload.ContainsKey("user_sid"));
    }

    [Fact]
    public void EveryEventGetsItsOwnIdentity()
    {
        var mapper = Mapper(new ProcessCollectorSettings());

        var first = mapper.Map(StartObservation());
        var second = mapper.Map(StartObservation());

        Assert.NotEqual(first.RawEventId, second.RawEventId);
    }

    /// <summary>
    /// Наблюдение не несёт идентификаторов Chronoscope — их выдаёт маппер. Это
    /// разделение и позволяет проверять преобразование без Windows и без WMI.
    /// </summary>
    [Fact]
    public void CollectorMapsPayloadTypeFromObservation()
    {
        Assert.Equal("process_start", ProcessCollector.PayloadTypeFor(isExit: false));
        Assert.Equal("process_exit", ProcessCollector.PayloadTypeFor(isExit: true));
    }
}
