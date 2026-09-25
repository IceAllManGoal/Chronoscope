using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Collectors;
using Chronoscope.Agent.Contract;
using Chronoscope.Agent.Logging;
using Chronoscope.Agent.Transport;

namespace Chronoscope.Agent.Tests;

/// <summary>
/// Наблюдение несколькими коллекторами: изоляция отказов и общий приёмник (§8.5, §77.2).
///
/// Проверяется то, что нельзя увидеть при обычном запуске: чтобы отказ одного
/// источника случился на глазах, нужен сломанный источник, а ломать настоящий
/// незачем. Поэтому здесь стоят подставные коллекторы — они решают, что делать:
/// публиковать события, падать объявленным отказом, падать неизвестным
/// исключением или прекращать наблюдение молча.
/// </summary>
public class CollectorSupervisorTests
{
    [Fact]
    public async Task PublishesEventsFromSeveralCollectorsIntoOneSink()
    {
        var buffer = new BoundedEventBuffer(capacity: 16);
        using var cancellation = new CancellationTokenSource();

        var first = new FakeCollector("fake.first", async (sink, token) =>
        {
            sink.Publish(SampleEvent("fake.first", 1));
            sink.Publish(SampleEvent("fake.first", 2));
            await FakeCollector.WaitForCancellationAsync(sink, token);
        });

        var second = new FakeCollector("fake.second", async (sink, token) =>
        {
            sink.Publish(SampleEvent("fake.second", 3));
            sink.Publish(SampleEvent("fake.second", 4));
            await FakeCollector.WaitForCancellationAsync(sink, token);
        });

        var supervisor = new CollectorSupervisor([first, second], buffer);
        var run = supervisor.RunAsync(cancellation.Token);

        await WaitUntil(() => buffer.AcceptedCount == 4, "события обоих коллекторов в общем приёмнике");

        Assert.Equal(1, first.StartedCount);
        Assert.Equal(1, second.StartedCount);

        cancellation.Cancel();
        await run.WaitAsync(TimeSpan.FromSeconds(5));

        // Один приёмник, одна очередь: второй коллектор не завёл собственную
        // доставку, иначе события разошлись бы по двум буферам.
        Assert.Equal(4, buffer.AcceptedCount);
        Assert.Equal(0, buffer.DroppedCount);
        Assert.Equal(0, supervisor.LostCount);
        Assert.All(
            supervisor.Statuses,
            status => Assert.Equal(CollectorOutcome.StoppedByRequest, status.Outcome));
    }

    [Fact]
    public async Task FailedCollectorDoesNotStopTheOthers()
    {
        var buffer = new BoundedEventBuffer(capacity: 16);
        using var cancellation = new CancellationTokenSource();

        var failing = new FakeCollector(
            "fake.failing",
            (_, _) => Task.FromException(new CollectorException("канал источника недоступен")));

        var working = new FakeCollector("fake.working", async (sink, token) =>
        {
            sink.Publish(SampleEvent("fake.working", 1));
            await Task.Delay(20, token);
            sink.Publish(SampleEvent("fake.working", 2));
            await FakeCollector.WaitForCancellationAsync(sink, token);
        });

        var supervisor = new CollectorSupervisor([failing, working], buffer);
        var run = supervisor.RunAsync(cancellation.Token);

        await WaitUntil(() => buffer.AcceptedCount == 2, "работающий коллектор продолжил публиковать события");

        // Agent жив: отказ одного источника не завершает наблюдение за другим.
        Assert.False(run.IsCompleted);

        var statuses = supervisor.Statuses;
        Assert.Equal(CollectorOutcome.Failed, statuses[0].Outcome);
        Assert.Contains("канал источника недоступен", statuses[0].Detail);
        Assert.Equal(CollectorOutcome.Observing, statuses[1].Outcome);
        Assert.Equal(1, supervisor.LostCount);

        cancellation.Cancel();
        await run.WaitAsync(TimeSpan.FromSeconds(5));
        Assert.Equal(CollectorOutcome.StoppedByRequest, supervisor.Statuses[1].Outcome);
    }

    [Fact]
    public async Task RuntimeFailureIsReportedLikeStartFailure()
    {
        var buffer = new BoundedEventBuffer(capacity: 16);
        using var cancellation = new CancellationTokenSource();

        var later = new FakeCollector("fake.later", async (sink, token) =>
        {
            sink.Publish(SampleEvent("fake.later", 1));
            await Task.Delay(10, token);
            throw new CollectorException("подписка на источник оборвалась");
        });

        var supervisor = new CollectorSupervisor([later], buffer);

        // Коллектор один, и он потерян: ждать больше нечего, RunAsync возвращается
        // сам — без отмены. Процесс, который ничего не наблюдает, живым выглядеть
        // не должен.
        await supervisor.RunAsync(cancellation.Token).WaitAsync(TimeSpan.FromSeconds(5));

        Assert.Equal(1, buffer.AcceptedCount);
        Assert.Equal(CollectorOutcome.Failed, supervisor.Statuses[0].Outcome);
        Assert.Contains("подписка на источник оборвалась", supervisor.Statuses[0].Detail);
        Assert.Equal(1, supervisor.LostCount);
    }

    /// <summary>
    /// Неожиданное исключение остаётся внутри своего коллектора.
    ///
    /// Это решение, а не побочный эффект: обещание версии — наблюдение за одним
    /// источником не зависит от здоровья чужого кода. Ошибка изолируется, но не
    /// прячется: причина называет тип исключения, а код возврата при завершении
    /// будет ненулевым (ADR-0016).
    /// </summary>
    [Fact]
    public async Task UnexpectedExceptionStaysInsideItsCollector()
    {
        var buffer = new BoundedEventBuffer(capacity: 16);
        using var cancellation = new CancellationTokenSource();

        var buggy = new FakeCollector(
            "fake.buggy",
            (_, _) => Task.FromException(new InvalidOperationException("сломанный код коллектора")));

        var healthy = FakeCollector.Idle("fake.healthy");

        var supervisor = new CollectorSupervisor([buggy, healthy], buffer);
        var run = supervisor.RunAsync(cancellation.Token);

        await WaitUntil(
            () => supervisor.Statuses[0].Outcome == CollectorOutcome.Failed,
            "неизвестное исключение зарегистрировано как отказ");

        Assert.Contains("InvalidOperationException", supervisor.Statuses[0].Detail);
        Assert.Equal(CollectorOutcome.Observing, supervisor.Statuses[1].Outcome);
        Assert.False(run.IsCompleted);

        cancellation.Cancel();
        await run.WaitAsync(TimeSpan.FromSeconds(5));
        Assert.Equal(1, supervisor.LostCount);
    }

    [Fact]
    public async Task CollectorThatStopsSilentlyCountsAsLostObservation()
    {
        var buffer = new BoundedEventBuffer(capacity: 4);
        var silent = new FakeCollector("fake.silent", (_, _) => Task.CompletedTask);
        var supervisor = new CollectorSupervisor([silent], buffer);

        await supervisor.RunAsync(CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(5));

        Assert.Equal(CollectorOutcome.StoppedUnexpectedly, supervisor.Statuses[0].Outcome);
        Assert.Equal(1, supervisor.LostCount);
    }

    [Fact]
    public void RejectsCollectorsWithTheSameName()
    {
        var buffer = new BoundedEventBuffer(capacity: 4);

        var exception = Assert.Throws<ArgumentException>(() =>
            new CollectorSupervisor([FakeCollector.Idle("windows.process"), FakeCollector.Idle("windows.process")], buffer));

        Assert.Contains("windows.process", exception.Message);
    }

    [Fact]
    public async Task WithoutCollectorsWaitsForCancellation()
    {
        var buffer = new BoundedEventBuffer(capacity: 4);
        var supervisor = new CollectorSupervisor([], buffer);
        using var cancellation = new CancellationTokenSource();

        var run = supervisor.RunAsync(cancellation.Token);

        await Task.Delay(50);
        Assert.False(run.IsCompleted);
        Assert.Equal(0, supervisor.Count);

        cancellation.Cancel();
        await run.WaitAsync(TimeSpan.FromSeconds(5));
        Assert.Equal(0, supervisor.LostCount);
    }

    /// <summary>
    /// Объявленный отказ и упавший код различимы в логе, хотя оба изолированы.
    ///
    /// Различие — единственное, что остаётся от «неизвестное исключение это не то
    /// же самое, что недоступный источник»: у них разные идентификаторы событий,
    /// и по ним отказ источника отличается от ошибки в коде коллектора.
    /// </summary>
    [Fact]
    public async Task DeclaredFailureAndUnexpectedExceptionAreDistinguishableInTheLog()
    {
        var buffer = new BoundedEventBuffer(capacity: 4);
        var original = JsonLog.Output;
        var log = new StringWriter();

        try
        {
            JsonLog.Output = log;

            var declared = new FakeCollector(
                "fake.declared",
                (_, _) => Task.FromException(new CollectorException("канал источника недоступен")));
            var unexpected = new FakeCollector(
                "fake.unexpected",
                (_, _) => Task.FromException(new InvalidOperationException("сломанный код коллектора")));

            var supervisor = new CollectorSupervisor([declared, unexpected], buffer);
            await supervisor.RunAsync(CancellationToken.None).WaitAsync(TimeSpan.FromSeconds(5));
        }
        finally
        {
            JsonLog.Output = original;
        }

        var written = log.ToString();
        Assert.Contains("collector_failed", written);
        Assert.Contains("collector_crashed", written);
        Assert.Contains("InvalidOperationException", written);
    }

    private static async Task WaitUntil(Func<bool> condition, string what)
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

        Assert.Fail($"не дождался: {what}");
    }

    private static RawEvent SampleEvent(string collector, int index) => new()
    {
        SchemaVersion = RawEventJson.SupportedSchemaVersion,
        RawEventId = $"raw_01K5R8Z9M5P8W3X7Y2C4E6G8J{index}",
        Collector = collector,
        CollectorVersion = "0.0.4",
        ObservedAt = DateTimeOffset.UnixEpoch,
        HostId = "host_01K5R8Z9M4Q7T2V6X1B3D5F7H9",
        PayloadType = "process_start",
        Payload = new Dictionary<string, object?> { ["pid"] = 1000 + index },
    };
}

/// <summary>Подставной коллектор: наблюдение описывается делегатом.</summary>
internal sealed class FakeCollector : IEventCollector
{
    private readonly Func<IRawEventSink, CancellationToken, Task> _watch;
    private int _started;

    public FakeCollector(string name, Func<IRawEventSink, CancellationToken, Task> watch)
    {
        Name = name;
        _watch = watch;
    }

    public string Name { get; }

    /// <summary>Сколько раз коллектор просили начать наблюдение.</summary>
    public int StartedCount => Volatile.Read(ref _started);

    public Task StartAsync(IRawEventSink sink, CancellationToken cancellationToken)
    {
        Interlocked.Increment(ref _started);
        return _watch(sink, cancellationToken);
    }

    /// <summary>Коллектор, который наблюдает до отмены и ничего не публикует.</summary>
    public static FakeCollector Idle(string name) => new(name, WaitForCancellationAsync);

    public static async Task WaitForCancellationAsync(IRawEventSink sink, CancellationToken cancellationToken)
    {
        await Task.Delay(Timeout.Infinite, cancellationToken);
    }
}
