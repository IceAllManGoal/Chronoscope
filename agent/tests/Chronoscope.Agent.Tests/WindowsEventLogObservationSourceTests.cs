using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Collectors;
using Chronoscope.Agent.Collectors.Windows;
using Chronoscope.Agent.Configuration;

namespace Chronoscope.Agent.Tests;

/// <summary>
/// Настоящая подписка на журнал Windows (§77.2, пункты 3 и 4).
///
/// Подмены здесь нет: проверяется поведение <c>EventLogWatcher</c> — что
/// недоступный канал обнаруживается отказом с именем канала, что отказ одного
/// канала не оставляет наблюдение за другим работающим втихую, и что подписка
/// снимается по отмене.
///
/// Доставку настоящей записи эти тесты не проверяют и проверить честно не могут:
/// единственный способ создать запись по требованию — писать в системный журнал,
/// а тест не имеет права менять систему. Это проверяется живым прогоном
/// (см. `docs/DEVELOPMENT.md`, раздел «Измерения», и отчёт по 0.0.4).
/// </summary>
public class WindowsEventLogObservationSourceTests
{
    private const string MissingChannel = "Chronoscope-No-Such-Channel";

    private static EventLogCollectorSettings Settings(params string[] channels)
        => new() { Enabled = true, Channels = channels };

    [Fact]
    public async Task UnknownChannelFailsWithChannelName()
    {
        var source = new WindowsEventLogObservationSource(Settings(MissingChannel));

        var exception = await Assert.ThrowsAsync<CollectorException>(() =>
            source.WatchAsync(_ => { }, CancellationToken.None));

        Assert.Contains(MissingChannel, exception.Message, StringComparison.Ordinal);
    }

    /// <summary>
    /// Отказ второго канала завершает наблюдение целиком. Продолжить с одним
    /// каналом из двух значило бы показывать частичное наблюдение как полное —
    /// именно тот случай, который §77.2 запрещает маскировать.
    /// </summary>
    [Fact]
    public async Task UnavailableChannelStopsTheWholeWatch()
    {
        var source = new WindowsEventLogObservationSource(Settings("System", MissingChannel));

        var exception = await Assert.ThrowsAsync<CollectorException>(() =>
            source.WatchAsync(_ => { }, CancellationToken.None));

        Assert.Contains(MissingChannel, exception.Message, StringComparison.Ordinal);
    }

    [Fact]
    public async Task SubscriptionStopsOnCancellation()
    {
        var source = new WindowsEventLogObservationSource(Settings("Application"));
        using var cancellation = new CancellationTokenSource();

        var watch = source.WatchAsync(_ => { }, cancellation.Token);

        // Подписка успевает состояться: отмена раньше означала бы проверку отмены,
        // а не подписки.
        await Task.Delay(300);
        cancellation.Cancel();

        await watch.WaitAsync(TimeSpan.FromSeconds(10));
    }

    [Fact]
    public async Task CancellingBeforeSubscriptionDoesNotThrow()
    {
        var source = new WindowsEventLogObservationSource(Settings("Application"));
        using var cancellation = new CancellationTokenSource();
        await cancellation.CancelAsync();

        await source.WatchAsync(_ => { }, cancellation.Token).WaitAsync(TimeSpan.FromSeconds(10));
    }
}
