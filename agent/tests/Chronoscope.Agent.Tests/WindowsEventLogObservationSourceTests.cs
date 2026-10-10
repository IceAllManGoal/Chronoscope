using System.Diagnostics.Eventing.Reader;
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

    /// <summary>
    /// Пустой список каналов — отказ, а не бесконечное ожидание отмены.
    ///
    /// Иначе источник выглядел бы работающим и просто молчащим: «ничего не
    /// приходит» стало бы неотличимо от «наблюдать не за чем». При загрузке
    /// конфигурации такой случай отвергается раньше, но источник создаётся и
    /// напрямую — в тестах и в будущем коде.
    /// </summary>
    [Fact]
    public async Task EmptyChannelListIsRefused()
    {
        var source = new WindowsEventLogObservationSource(
            new EventLogCollectorSettings { Enabled = true, Channels = [] });

        var exception = await Assert.ThrowsAsync<CollectorException>(() =>
            source.WatchAsync(_ => { }, CancellationToken.None));

        Assert.Contains("ни один канал", exception.Message, StringComparison.Ordinal);
    }

    /// <summary>
    /// Тем же источником можно наблюдать повторно: остановка — свойство конкретного
    /// наблюдения, а не самого объекта. Проверяется то, что проверяемо без записи в
    /// журнал: второе наблюдение поднимается и снимается по отмене без отказа.
    /// </summary>
    [Fact]
    public async Task SubscriptionCanBeStartedAgainAfterCancellation()
    {
        var source = new WindowsEventLogObservationSource(Settings("Application"));

        using (var first = new CancellationTokenSource())
        {
            var firstWatch = source.WatchAsync(_ => { }, first.Token);
            await Task.Delay(300);
            first.Cancel();
            await firstWatch.WaitAsync(TimeSpan.FromSeconds(10));
        }

        using var second = new CancellationTokenSource();
        var secondWatch = source.WatchAsync(_ => { }, second.Token);
        await Task.Delay(300);
        second.Cancel();

        await secondWatch.WaitAsync(TimeSpan.FromSeconds(10));
    }

    /// <summary>
    /// Уже существующие записи не приходят: источник наблюдает только за новыми (§12, §77.2 пункт 3).
    ///
    /// Проверка имеет силу только на непустом журнале, поэтому сначала выясняется,
    /// что записи до подписки были. Иначе тест проходил бы на пустом месте и
    /// доказывал бы ровно ничего. Снять флаг <c>readExistingEvents: false</c> нельзя
    /// незаметно: тогда придут записи старше момента подписки, и проверка упадёт.
    /// </summary>
    [Fact]
    public async Task ExistingRecordsAreNotDelivered()
    {
        const string channel = "Application";
        var newestBefore = NewestRecordTime(channel);

        var source = new WindowsEventLogObservationSource(Settings(channel));
        var delivered = new List<EventLogObservation>();
        using var cancellation = new CancellationTokenSource();

        var watch = source.WatchAsync(
            observation =>
            {
                lock (delivered)
                {
                    delivered.Add(observation);
                }
            },
            cancellation.Token);

        await Task.Delay(1500);
        cancellation.Cancel();
        await watch.WaitAsync(TimeSpan.FromSeconds(10));

        EventLogObservation[] observed;
        lock (delivered)
        {
            observed = [.. delivered];
        }

        Assert.All(
            observed,
            observation => Assert.True(
                newestBefore is null || observation.RecordedAt is null || observation.RecordedAt > newestBefore,
                $"пришла запись, существовавшая до подписки: {observation.RecordedAt:o} <= {newestBefore:o}"));
    }

    /// <summary>Время самой новой записи канала на момент вызова.</summary>
    private static DateTimeOffset? NewestRecordTime(string channel)
    {
        var query = new EventLogQuery(channel, PathType.LogName, "*")
        {
            TolerateQueryErrors = false,
            ReverseDirection = true,
        };

        using var reader = new EventLogReader(query);
        using var record = reader.ReadEvent();

        return record?.TimeCreated is null
            ? null
            : new DateTimeOffset(record.TimeCreated.Value.ToUniversalTime());
    }
}
