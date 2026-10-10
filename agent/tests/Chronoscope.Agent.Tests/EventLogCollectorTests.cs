using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Collectors;
using Chronoscope.Agent.Contract;
using Chronoscope.Agent.Identity;

namespace Chronoscope.Agent.Tests;

/// <summary>
/// Коллектор журнала: наблюдение → приёмник и поведение при отказе подписки.
///
/// Источник здесь подменён. Настоящая подписка Windows проверяется отдельно
/// (<see cref="WindowsEventLogObservationSourceTests"/> и живой прогон), а тут
/// важно другое: коллектор ничего не теряет по дороге, не глотает отказ источника
/// и не предполагает, что обработчик вызван из его собственного потока.
/// </summary>
public class EventLogCollectorTests : IDisposable
{
    private readonly string _dataDirectory = Path.Combine(Path.GetTempPath(), $"chronoscope-eventlog-collector-{Guid.NewGuid():N}");

    public void Dispose()
    {
        if (Directory.Exists(_dataDirectory))
        {
            Directory.Delete(_dataDirectory, recursive: true);
        }

        GC.SuppressFinalize(this);
    }

    private sealed class FixedBootSession : IBootSessionProvider
    {
        public DateTimeOffset? GetBootTimeUtc() => DateTimeOffset.Parse("2026-10-09T05:00:00Z");
    }

    /// <summary>Приёмник, запоминающий опубликованное: события коллектора — это ровно то, что он отдал.</summary>
    private sealed class RecordingSink : IRawEventSink
    {
        private readonly List<RawEvent> _events = [];

        public IReadOnlyList<RawEvent> Events
        {
            get
            {
                lock (_events)
                {
                    return _events.ToArray();
                }
            }
        }

        public void Publish(RawEvent rawEvent)
        {
            lock (_events)
            {
                _events.Add(rawEvent);
            }
        }
    }

    private sealed class FakeSource(Func<Action<EventLogObservation>, CancellationToken, Task> watch) : IEventLogObservationSource
    {
        public Task WatchAsync(Action<EventLogObservation> onObservation, CancellationToken cancellationToken)
            => watch(onObservation, cancellationToken);
    }

    private EventLogCollector Collector(IEventLogObservationSource source)
    {
        var identity = AgentIdentity.Resolve(_dataDirectory, new FixedBootSession());
        return EventLogCollector.Create(source, identity, "0.0.4");
    }

    private static EventLogObservation Observation(int eventId) => new()
    {
        Channel = "System",
        Provider = "Microsoft-Windows-Kernel-General",
        EventId = eventId,
        RecordId = 123_456,
        Level = 4,
        Task = 0,
        Opcode = 0,
        RecordedAt = DateTimeOffset.Parse("2026-10-09T10:42:15.281Z"),
    };

    /// <summary>
    /// Наблюдение «до отмены» заканчивается возвратом, а не исключением: отмена —
    /// это способ остановить наблюдение, а не сбой. Настоящие источники ведут себя
    /// так же, и подставной обязан, иначе он проверял бы не тот контракт.
    /// </summary>
    private static async Task WaitForCancellationAsync(CancellationToken cancellationToken)
    {
        try
        {
            await Task.Delay(Timeout.Infinite, cancellationToken);
        }
        catch (OperationCanceledException)
        {
            // Штатная остановка.
        }
    }

    [Fact]
    public async Task PublishesEveryObservationToTheSink()
    {
        var sink = new RecordingSink();
        var source = new FakeSource(async (onObservation, cancellationToken) =>
        {
            onObservation(Observation(1));
            onObservation(Observation(2));
            onObservation(Observation(3));
            await WaitForCancellationAsync(cancellationToken);
        });

        using var cancellation = new CancellationTokenSource();
        var watch = Collector(source).StartAsync(sink, cancellation.Token);

        await WaitUntil(() => sink.Events.Count == 3);
        cancellation.Cancel();
        await watch.WaitAsync(TimeSpan.FromSeconds(5));

        var events = sink.Events;
        Assert.Equal(3, events.Count);
        Assert.All(events, rawEvent => Assert.Equal("windows.eventlog", rawEvent.Collector));
        Assert.All(events, rawEvent => Assert.Equal("event_log_record", rawEvent.PayloadType));
        Assert.All(events, rawEvent => Assert.Equal(DateTimeOffset.Parse("2026-10-09T10:42:15.281Z"), rawEvent.SourceTimestamp));
        Assert.Equal([1, 2, 3], events.Select(rawEvent => (int)rawEvent.Payload["event_id"]!).ToArray());
    }

    /// <summary>
    /// Одинаковый <c>record_id</c> у разных записей не превращает их в одну:
    /// дедупликация ingest идёт по <c>raw_event_id</c> (§34), и «одна запись
    /// журнала» — не идентичность Chronoscope.
    /// </summary>
    [Fact]
    public async Task ObservationsWithEqualRecordIdsStayDistinctEvents()
    {
        var sink = new RecordingSink();
        var source = new FakeSource(async (onObservation, cancellationToken) =>
        {
            onObservation(Observation(1));
            onObservation(Observation(1));
            await WaitForCancellationAsync(cancellationToken);
        });

        using var cancellation = new CancellationTokenSource();
        var watch = Collector(source).StartAsync(sink, cancellation.Token);

        await WaitUntil(() => sink.Events.Count == 2);
        cancellation.Cancel();
        await watch.WaitAsync(TimeSpan.FromSeconds(5));

        var ids = sink.Events.Select(rawEvent => rawEvent.RawEventId).ToArray();
        Assert.Equal(2, ids.Distinct(StringComparer.Ordinal).Count());
    }

    /// <summary>
    /// Обработчик вызывается потоком подписки Windows, а не потоком коллектора.
    /// Коллектор обязан публиковать и в этом случае — иначе события журнала
    /// терялись бы все разом, и заметить это было бы нечем.
    /// </summary>
    [Fact]
    public async Task PublishesObservationsDeliveredFromAnotherThread()
    {
        var sink = new RecordingSink();
        var source = new FakeSource(async (onObservation, cancellationToken) =>
        {
            await Task.Run(() => onObservation(Observation(7)), cancellationToken);
            await WaitForCancellationAsync(cancellationToken);
        });

        using var cancellation = new CancellationTokenSource();
        var watch = Collector(source).StartAsync(sink, cancellation.Token);

        await WaitUntil(() => sink.Events.Count == 1);
        cancellation.Cancel();
        await watch.WaitAsync(TimeSpan.FromSeconds(5));

        Assert.Equal(7, sink.Events[0].Payload["event_id"]);
    }

    /// <summary>
    /// Отказ подписки не остаётся в логе: он доходит до вызывающего, то есть до
    /// надзирателя, который пометит коллектор потерянным. Проглоченное исключение
    /// означало бы, что Agent наблюдает за источником, которого больше нет, и
    /// потеря наблюдения выглядела бы как тишина в журнале.
    /// </summary>
    [Fact]
    public async Task SourceFailureReachesTheCaller()
    {
        var sink = new RecordingSink();
        var source = new FakeSource((_, _) =>
            Task.FromException(new CollectorException("канал System недоступен: журнал не найден")));

        var exception = await Assert.ThrowsAsync<CollectorException>(() =>
            Collector(source).StartAsync(sink, CancellationToken.None));

        Assert.Contains("канал System", exception.Message, StringComparison.Ordinal);
        Assert.Empty(sink.Events);
    }

    [Fact]
    public async Task StopsWhenCancelled()
    {
        var sink = new RecordingSink();
        var source = new FakeSource((_, cancellationToken) => WaitForCancellationAsync(cancellationToken));

        using var cancellation = new CancellationTokenSource();
        var watch = Collector(source).StartAsync(sink, cancellation.Token);

        await Task.Delay(50);
        cancellation.Cancel();

        await watch.WaitAsync(TimeSpan.FromSeconds(5));
        Assert.Empty(sink.Events);
    }

    private static async Task WaitUntil(Func<bool> condition)
    {
        var deadline = DateTime.UtcNow.AddSeconds(5);

        while (DateTime.UtcNow < deadline)
        {
            if (condition())
            {
                return;
            }

            await Task.Delay(10);
        }

        Assert.Fail("не дождался публикации события");
    }
}
