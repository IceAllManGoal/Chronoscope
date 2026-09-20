using System.Net;
using System.Text;
using Chronoscope.Agent.Configuration;
using Chronoscope.Agent.Contract;
using Chronoscope.Agent.Transport;

namespace Chronoscope.Agent.Tests;

/// <summary>
/// Транспорт: ограниченный буфер и отправка пакетами (§34).
///
/// Здесь проверяется то, что нельзя увидеть глазами при обычном запуске:
/// поведение при переполнении, при недоступном Core и при завершении. Каждое из
/// этих состояний редкое, и каждое из них иначе проявилось бы уже на истории
/// данных — то есть когда исправлять поздно.
/// </summary>
public class BoundedEventBufferTests
{
    [Fact]
    public void AcceptsUpToCapacity()
    {
        var buffer = new BoundedEventBuffer(capacity: 3);

        for (var index = 0; index < 3; index++)
        {
            buffer.Publish(SampleEvent(index));
        }

        Assert.Equal(3, buffer.AcceptedCount);
        Assert.Equal(0, buffer.DroppedCount);
        Assert.Equal(3, buffer.PendingCount);
    }

    /// <summary>
    /// Переполнение обязано быть видно в счётчике. Молчаливая потеря сделала бы
    /// историю похожей на полную, не будучи ей, — а Chronoscope существует ровно
    /// для того, чтобы истории можно было доверять.
    /// </summary>
    [Fact]
    public void CountsDroppedEventsInsteadOfGrowingWithoutBound()
    {
        var buffer = new BoundedEventBuffer(capacity: 2);

        for (var index = 0; index < 5; index++)
        {
            buffer.Publish(SampleEvent(index));
        }

        Assert.Equal(2, buffer.AcceptedCount);
        Assert.Equal(3, buffer.DroppedCount);
        Assert.Equal(2, buffer.PendingCount);
    }

    [Fact]
    public void RejectsNonPositiveCapacity()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => new BoundedEventBuffer(0));
    }

    private static RawEvent SampleEvent(int index) => new()
    {
        SchemaVersion = RawEventJson.SupportedSchemaVersion,
        RawEventId = $"raw_01K5R8Z9M5P8W3X7Y2C4E6G8J{index}",
        Collector = "windows.process",
        CollectorVersion = "0.0.1",
        ObservedAt = DateTimeOffset.UnixEpoch,
        HostId = "host_01K5R8Z9M4Q7T2V6X1B3D5F7H9",
        PayloadType = "process_start",
        Payload = new Dictionary<string, object?> { ["pid"] = 1000 + index },
    };
}

public class BatchSenderTests
{
    private static RawEvent Event(int index) => new()
    {
        SchemaVersion = RawEventJson.SupportedSchemaVersion,
        RawEventId = $"raw_01K5R8Z9M5P8W3X7Y2C4E6G8J{index}",
        Collector = "windows.process",
        CollectorVersion = "0.0.1",
        ObservedAt = DateTimeOffset.UnixEpoch,
        HostId = "host_01K5R8Z9M4Q7T2V6X1B3D5F7H9",
        PayloadType = "process_start",
        Payload = new Dictionary<string, object?> { ["pid"] = 1000 + index },
    };

    private sealed class FakeIngestClient : IIngestClient
    {
        private readonly Queue<Func<IReadOnlyList<RawEvent>, IngestOutcome>> _scripted = new();

        public List<IReadOnlyList<RawEvent>> Received { get; } = [];

        public int Calls { get; private set; }

        /// <summary>Очередь ответов: исключение — повторяемый отказ, функция — результат.</summary>
        public void EnqueueFailure(Exception exception) => _scripted.Enqueue(_ => throw exception);

        public void EnqueueSuccess(int accepted) => _scripted.Enqueue(events => new IngestOutcome(accepted, 0, 0, 0));

        public Task<IngestOutcome> SendAsync(IReadOnlyList<RawEvent> events, CancellationToken cancellationToken)
        {
            Calls++;
            Received.Add(events);

            if (_scripted.Count == 0)
            {
                return Task.FromResult(new IngestOutcome(events.Count, 0, 0, 0));
            }

            var next = _scripted.Dequeue();
            return Task.FromResult(next(events));
        }
    }

    private static async Task WaitUntilAsync(Func<bool> condition, int timeoutMs = 5000)
    {
        var deadline = Environment.TickCount64 + timeoutMs;
        while (Environment.TickCount64 < deadline)
        {
            if (condition())
            {
                return;
            }

            await Task.Delay(10);
        }

        Assert.Fail("условие не выполнено за отведённое время");
    }

    [Fact]
    public async Task SendsBatchWhenItReachesConfiguredSize()
    {
        var buffer = new BoundedEventBuffer(capacity: 100);
        var client = new FakeIngestClient();
        var sender = new BatchSender(buffer, client, new DeliverySettings { BatchSize = 3, FlushIntervalMs = 60_000 });
        using var cancellation = new CancellationTokenSource();

        for (var index = 0; index < 3; index++)
        {
            buffer.Publish(Event(index));
        }

        var running = sender.RunAsync(cancellation.Token);
        await WaitUntilAsync(() => client.Received.Count >= 1);

        Assert.Equal(3, client.Received[0].Count);

        await cancellation.CancelAsync();
        await running;
    }

    /// <summary>
    /// Одиночное событие не должно ждать следующего: «событие произошло» и
    /// «событие видно в истории» не могут расходиться на неопределённое время.
    /// </summary>
    [Fact]
    public async Task FlushesOnIntervalWhenBatchIsNotFull()
    {
        var buffer = new BoundedEventBuffer(capacity: 100);
        var client = new FakeIngestClient();
        var sender = new BatchSender(buffer, client, new DeliverySettings { BatchSize = 100, FlushIntervalMs = 50 });
        using var cancellation = new CancellationTokenSource();

        buffer.Publish(Event(0));

        var running = sender.RunAsync(cancellation.Token);
        await WaitUntilAsync(() => client.Received.Count >= 1);

        var delivered = Assert.Single(client.Received);
        Assert.Equal("raw_01K5R8Z9M5P8W3X7Y2C4E6G8J0", Assert.Single(delivered).RawEventId);

        await cancellation.CancelAsync();
        await running;
    }

    [Fact]
    public async Task RetriesAfterTransportFailureAndThenSucceeds()
    {
        var buffer = new BoundedEventBuffer(capacity: 100);
        var client = new FakeIngestClient();
        client.EnqueueFailure(new HttpRequestException("Core недоступен"));
        client.EnqueueFailure(new HttpRequestException("Core недоступен"));
        client.EnqueueSuccess(accepted: 1);

        var sender = new BatchSender(buffer, client, new DeliverySettings { BatchSize = 1, FlushIntervalMs = 60_000 });
        using var cancellation = new CancellationTokenSource();

        buffer.Publish(Event(0));

        var running = sender.RunAsync(cancellation.Token);
        await WaitUntilAsync(() => sender.Snapshot().BatchesSent == 1);

        var statistics = sender.Snapshot();
        Assert.Equal(2, statistics.SendFailures);
        Assert.Equal(1, statistics.EventsSent);

        await cancellation.CancelAsync();
        await running;
    }

    /// <summary>
    /// Необратимый отказ повторять нельзя: пакет не станет валидным, а очередь
    /// встала бы навсегда — отправитель держит пакет до успеха. Поэтому
    /// отвергнутый пакет отбрасывается, и следующие за ним доставляются.
    /// </summary>
    [Fact]
    public async Task PermanentlyRejectedBatchDoesNotBlockTheQueue()
    {
        var buffer = new BoundedEventBuffer(capacity: 100);
        var client = new FakeIngestClient();
        client.EnqueueFailure(new IngestRejectedException("422 unsupported_schema_version"));
        client.EnqueueSuccess(accepted: 1);

        var sender = new BatchSender(buffer, client, new DeliverySettings { BatchSize = 1, FlushIntervalMs = 60_000 });
        using var cancellation = new CancellationTokenSource();

        buffer.Publish(Event(0));

        var running = sender.RunAsync(cancellation.Token);
        await WaitUntilAsync(() => sender.Snapshot().BatchesRejected == 1);

        // Следующее событие обязано дойти: очередь не заблокирована.
        buffer.Publish(Event(1));
        await WaitUntilAsync(() => sender.Snapshot().BatchesSent == 1);

        var statistics = sender.Snapshot();
        Assert.Equal(0, statistics.SendFailures);
        Assert.Equal(1, statistics.EventsRejected);
        Assert.Equal(1, statistics.EventsSent);

        await cancellation.CancelAsync();
        await running;
    }

    /// <summary>
    /// Завершение — момент, когда отправка нужнее всего: накопленный пакет
    /// обязан быть отправлен, иначе остановка Agent молча съедает события (§62).
    /// </summary>
    [Fact]
    public async Task SendsPendingBatchOnShutdown()
    {
        var buffer = new BoundedEventBuffer(capacity: 100);
        var client = new FakeIngestClient();
        // Большой интервал накопления: пакет заведомо неполный в момент остановки.
        var sender = new BatchSender(buffer, client, new DeliverySettings { BatchSize = 100, FlushIntervalMs = 60_000 });
        using var cancellation = new CancellationTokenSource();

        buffer.Publish(Event(0));
        buffer.Publish(Event(1));

        var running = sender.RunAsync(cancellation.Token);
        await WaitUntilAsync(() => sender.PendingBatchSize == 2);

        await cancellation.CancelAsync();
        await running;

        Assert.Single(client.Received);
        Assert.Equal(2, client.Received[0].Count);
        Assert.Equal(2, sender.Snapshot().EventsSent);
    }

    [Fact]
    public void StatisticsStartAtZero()
    {
        var sender = new BatchSender(new BoundedEventBuffer(10), new FakeIngestClient(), new DeliverySettings());

        var statistics = sender.Snapshot();

        Assert.Equal(0, statistics.BatchesSent);
        Assert.Equal(0, statistics.EventsDropped);
        Assert.Equal(0, statistics.SendFailures);
    }
}

/// <summary>
/// Классификация отказов Core. Различие принципиальное: повторяемый отказ
/// отправитель повторит, необратимый — нет. Ошибка здесь означает либо вечно
/// заблокированную очередь, либо бессмысленный поток запросов к Core.
/// </summary>
public class HttpIngestClientTests
{
    private sealed class ScriptedHandler(HttpStatusCode status, string body) : HttpMessageHandler
    {
        public int Calls { get; private set; }

        public string? LastRequestBody { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            Calls++;
            if (request.Content is not null)
            {
                LastRequestBody = await request.Content.ReadAsStringAsync(cancellationToken);
            }

            return new HttpResponseMessage(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };
        }
    }

    private static RawEvent Event() => new()
    {
        SchemaVersion = RawEventJson.SupportedSchemaVersion,
        RawEventId = "raw_01K5R8Z9M5P8W3X7Y2C4E6G8J0",
        Collector = "windows.process",
        CollectorVersion = "0.0.1",
        ObservedAt = DateTimeOffset.UnixEpoch,
        HostId = "host_01K5R8Z9M4Q7T2V6X1B3D5F7H9",
        PayloadType = "process_start",
        Payload = new Dictionary<string, object?> { ["pid"] = 9812 },
    };

    [Fact]
    public async Task ParsesIngestOutcome()
    {
        const string body = """
            {"accepted":1,"duplicates":2,"normalization_failed":3,"rejected":[{"index":0,"code":"invalid_input","message":"x"}]}
            """;
        var handler = new ScriptedHandler(HttpStatusCode.OK, body);
        using var client = new HttpIngestClient(new Uri("http://127.0.0.1:7342/api/v1/ingest/raw-events"), TimeSpan.FromSeconds(5), handler);

        var outcome = await client.SendAsync([Event()], CancellationToken.None);

        Assert.Equal(1, outcome.Accepted);
        Assert.Equal(2, outcome.Duplicates);
        Assert.Equal(3, outcome.NormalizationFailed);
        Assert.Equal(1, outcome.Rejected);
    }

    [Fact]
    public async Task SendsBatchEnvelopeWithVersion()
    {
        var handler = new ScriptedHandler(HttpStatusCode.OK, """{"accepted":1,"duplicates":0,"normalization_failed":0}""");
        using var client = new HttpIngestClient(new Uri("http://127.0.0.1:7342/api/v1/ingest/raw-events"), TimeSpan.FromSeconds(5), handler);

        await client.SendAsync([Event(), Event()], CancellationToken.None);

        Assert.NotNull(handler.LastRequestBody);
        using var document = System.Text.Json.JsonDocument.Parse(handler.LastRequestBody!);
        Assert.Equal(1, document.RootElement.GetProperty("schema_version").GetInt32());
        Assert.Equal(2, document.RootElement.GetProperty("events").GetArrayLength());
    }

    [Theory]
    [InlineData(HttpStatusCode.InternalServerError)]
    [InlineData(HttpStatusCode.BadGateway)]
    [InlineData(HttpStatusCode.RequestTimeout)]
    [InlineData(HttpStatusCode.TooManyRequests)]
    public async Task TreatsServerSideProblemsAsRetryable(HttpStatusCode status)
    {
        var handler = new ScriptedHandler(status, """{"error":{"code":"storage_failure","message":"нет схемы"}}""");
        using var client = new HttpIngestClient(new Uri("http://127.0.0.1:7342/api/v1/ingest/raw-events"), TimeSpan.FromSeconds(5), handler);

        await Assert.ThrowsAsync<HttpRequestException>(() => client.SendAsync([Event()], CancellationToken.None));
    }

    [Theory]
    [InlineData(HttpStatusCode.UnprocessableEntity)]
    [InlineData(HttpStatusCode.RequestEntityTooLarge)]
    [InlineData(HttpStatusCode.NotFound)]
    public async Task TreatsContractViolationsAsPermanent(HttpStatusCode status)
    {
        var handler = new ScriptedHandler(status, """{"error":{"code":"invalid_input","message":"не соответствует контракту"}}""");
        using var client = new HttpIngestClient(new Uri("http://127.0.0.1:7342/api/v1/ingest/raw-events"), TimeSpan.FromSeconds(5), handler);

        await Assert.ThrowsAsync<IngestRejectedException>(() => client.SendAsync([Event()], CancellationToken.None));
    }

    /// <summary>«Отправили и не поняли ответ» — это не подтверждённая доставка.</summary>
    [Fact]
    public async Task UnparseableSuccessResponseIsTreatedAsFailure()
    {
        var handler = new ScriptedHandler(HttpStatusCode.OK, "не json");
        using var client = new HttpIngestClient(new Uri("http://127.0.0.1:7342/api/v1/ingest/raw-events"), TimeSpan.FromSeconds(5), handler);

        await Assert.ThrowsAsync<HttpRequestException>(() => client.SendAsync([Event()], CancellationToken.None));
    }
}
