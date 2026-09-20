using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Configuration;
using Chronoscope.Agent.Identity;

namespace Chronoscope.Agent.Tests;

/// <summary>
/// Идентичность машины и загрузки (§15, §16).
///
/// Проверяется главным образом <b>устойчивость</b>: <c>host_id</c> обязан
/// переживать перезапуск, а <c>boot_id</c> — совпадать при каждом запуске
/// внутри одной загрузки и различаться между загрузками. Оба свойства
/// незаметны при обычном запуске и проявляются только на истории данных, когда
/// исправлять уже поздно.
/// </summary>
public class AgentIdentityTests : IDisposable
{
    private readonly string _dataDirectory = Path.Combine(
        Path.GetTempPath(),
        $"chronoscope-agent-tests-{Guid.NewGuid():N}");

    public void Dispose()
    {
        if (Directory.Exists(_dataDirectory))
        {
            Directory.Delete(_dataDirectory, recursive: true);
        }

        GC.SuppressFinalize(this);
    }

    private sealed class FixedBootSession(DateTimeOffset? bootTime) : IBootSessionProvider
    {
        public DateTimeOffset? GetBootTimeUtc() => bootTime;
    }

    private sealed class FixedTimeProvider(DateTimeOffset now) : TimeProvider
    {
        public override DateTimeOffset GetUtcNow() => now;
    }

    [Fact]
    public void CreatesHostIdOnFirstRun()
    {
        var hostId = AgentIdentity.LoadOrCreateHostId(_dataDirectory, new FixedTimeProvider(DateTimeOffset.UnixEpoch));

        Assert.StartsWith("host_", hostId, StringComparison.Ordinal);
        Assert.True(Ulid.IsValid(hostId["host_".Length..]));
        Assert.True(File.Exists(Path.Combine(_dataDirectory, AgentIdentity.HostIdFileName)));
    }

    [Fact]
    public void ReusesHostIdAcrossRuns()
    {
        var first = AgentIdentity.LoadOrCreateHostId(_dataDirectory, new FixedTimeProvider(DateTimeOffset.UnixEpoch));
        var second = AgentIdentity.LoadOrCreateHostId(_dataDirectory, new FixedTimeProvider(DateTimeOffset.Parse("2030-01-01T00:00:00Z")));

        Assert.Equal(first, second);
    }

    /// <summary>
    /// Смена host_id означает, что вся дальнейшая история относится к «другой
    /// машине», и заметить это по данным невозможно. Поэтому повреждённый файл —
    /// явная ошибка, а не повод молча выдать новый идентификатор.
    /// </summary>
    [Fact]
    public void RefusesToSilentlyReplaceCorruptHostIdFile()
    {
        Directory.CreateDirectory(_dataDirectory);
        File.WriteAllText(Path.Combine(_dataDirectory, AgentIdentity.HostIdFileName), "не идентификатор");

        var exception = Assert.Throws<ConfigurationException>(() =>
            AgentIdentity.LoadOrCreateHostId(_dataDirectory, new FixedTimeProvider(DateTimeOffset.UnixEpoch)));

        Assert.Contains("host_id", exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public void CreatesDirectoryIfMissing()
    {
        Assert.False(Directory.Exists(_dataDirectory));

        AgentIdentity.LoadOrCreateHostId(_dataDirectory, new FixedTimeProvider(DateTimeOffset.UnixEpoch));

        Assert.True(Directory.Exists(_dataDirectory));
    }

    [Fact]
    public void BootIdIsStableForTheSameBoot()
    {
        var bootTime = DateTimeOffset.Parse("2026-09-18T05:00:00Z");

        var first = AgentIdentity.FromBootTime(bootTime);
        var second = AgentIdentity.FromBootTime(bootTime);

        Assert.Equal(first, second);
    }

    [Fact]
    public void BootIdDiffersBetweenBoots()
    {
        var first = AgentIdentity.FromBootTime(DateTimeOffset.Parse("2026-09-18T05:00:00Z"));
        var second = AgentIdentity.FromBootTime(DateTimeOffset.Parse("2026-09-19T05:00:00Z"));

        Assert.NotEqual(first, second);
    }

    [Fact]
    public void BootIdHasContractForm()
    {
        var bootId = AgentIdentity.FromBootTime(DateTimeOffset.Parse("2026-09-18T05:00:00Z"));

        Assert.NotNull(bootId);
        Assert.StartsWith("boot_", bootId, StringComparison.Ordinal);
        Assert.True(Ulid.IsValid(bootId!["boot_".Length..]));
        Assert.Matches("^[a-z][a-z0-9_]*_[0-9A-HJKMNP-TV-Z]{26}$", bootId!);
    }

    /// <summary>
    /// §16 прямо допускает отсутствие boot_id. Подставлять вместо него догадку
    /// нельзя: выдуманный идентификатор склеил бы события разных загрузок.
    /// </summary>
    [Fact]
    public void BootIdIsNullWhenBootSessionIsUnknown()
    {
        Assert.Null(AgentIdentity.FromBootTime(null));
    }

    [Fact]
    public void ResolveCombinesHostAndBoot()
    {
        var identity = AgentIdentity.Resolve(
            _dataDirectory,
            new FixedBootSession(DateTimeOffset.Parse("2026-09-18T05:00:00Z")),
            new FixedTimeProvider(DateTimeOffset.Parse("2026-09-18T10:00:00Z")));

        Assert.StartsWith("host_", identity.HostId, StringComparison.Ordinal);
        Assert.StartsWith("boot_", identity.BootId, StringComparison.Ordinal);
    }

    [Fact]
    public void ResolveStillWorksWithoutBootSession()
    {
        var identity = AgentIdentity.Resolve(
            _dataDirectory,
            new FixedBootSession(null),
            new FixedTimeProvider(DateTimeOffset.Parse("2026-09-18T10:00:00Z")));

        Assert.Null(identity.BootId);
    }

    /// <summary>Время загрузки может прийти с локальным смещением — оно приводится к UTC без потери информации.</summary>
    [Fact]
    public void BootIdIgnoresOffsetRepresentation()
    {
        var utc = DateTimeOffset.Parse("2026-09-18T05:00:00Z");
        var sameMoment = DateTimeOffset.Parse("2026-09-18T10:00:00+05:00");

        Assert.Equal(AgentIdentity.FromBootTime(utc), AgentIdentity.FromBootTime(sameMoment));
    }
}

/// <summary>ULID должен сортироваться по времени: временная часть идёт первой.</summary>
public class AgentIdentityOrderingTests
{
    [Fact]
    public void EarlierBootSortsBeforeLaterBoot()
    {
        var earlier = AgentIdentity.FromBootTime(DateTimeOffset.Parse("2026-09-18T05:00:00Z"))!;
        var later = AgentIdentity.FromBootTime(DateTimeOffset.Parse("2026-09-19T05:00:00Z"))!;

        Assert.True(string.CompareOrdinal(earlier, later) < 0, $"'{earlier}' должен быть меньше '{later}'");
    }
}
