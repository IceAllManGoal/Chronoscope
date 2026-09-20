using Chronoscope.Agent.Configuration;
using Chronoscope.Agent.Contract;
using Chronoscope.Agent.Logging;

namespace Chronoscope.Agent.Transport;

/// <summary>Счётчики работы Agent. §61 требует измерять, а не верить на слово.</summary>
public sealed record AgentStatistics(
    long EventsAccepted,
    long EventsDropped,
    long EventsSent,
    long EventsDuplicate,
    long EventsRejected,
    long BatchesSent,
    long BatchesRejected,
    long SendFailures);

/// <summary>
/// Отправка пакетами (§34).
///
/// Модель доставки — <b>at-least-once</b>: пакет повторяется, пока Core не
/// подтвердит приём. Дубликатов при этом не возникает, потому что
/// <c>raw_event_id</c> стабилен, а ingest в Core идемпотентен по этому ключу
/// (§34). Именно поэтому повтор после «отправили, но не дождались ответа» — не
/// ошибка, а штатное поведение. А вот необратимый отказ повторять нельзя: пакет
/// не станет валидным, а очередь встала бы навсегда, потому что отправитель
/// держит пакет до успеха.
/// </summary>
public sealed class BatchSender
{
    private static readonly TimeSpan FirstRetryDelay = TimeSpan.FromMilliseconds(500);
    private static readonly TimeSpan MaxRetryDelay = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan ShutdownFlushTimeout = TimeSpan.FromSeconds(5);

    private readonly BoundedEventBuffer _buffer;
    private readonly IIngestClient _client;
    private readonly DeliverySettings _settings;
    private readonly TimeProvider _timeProvider;

    private long _eventsSent;
    private long _eventsDuplicate;
    private long _eventsRejected;
    private long _batchesSent;
    private long _batchesRejected;
    private long _sendFailures;

    /// <summary>
    /// Накопленный пакет. Хранится полем, а не локальной переменной, по важной
    /// причине: события, уже вынутые из буфера, обязаны быть доступны финальной
    /// отправке при завершении. Локальная переменная потеряла бы их ровно в тот
    /// момент, когда отправка нужнее всего.
    /// </summary>
    private List<RawEvent> _pending = [];

    public BatchSender(
        BoundedEventBuffer buffer,
        IIngestClient client,
        DeliverySettings settings,
        TimeProvider? timeProvider = null)
    {
        _buffer = buffer;
        _client = client;
        _settings = settings;
        _timeProvider = timeProvider ?? TimeProvider.System;
    }

    /// <summary>Размер пакета, ожидающего подтверждения.</summary>
    public int PendingBatchSize => _pending.Count;

    public AgentStatistics Snapshot() => new(
        EventsAccepted: _buffer.AcceptedCount,
        EventsDropped: _buffer.DroppedCount,
        EventsSent: Interlocked.Read(ref _eventsSent),
        EventsDuplicate: Interlocked.Read(ref _eventsDuplicate),
        EventsRejected: Interlocked.Read(ref _eventsRejected),
        BatchesSent: Interlocked.Read(ref _batchesSent),
        BatchesRejected: Interlocked.Read(ref _batchesRejected),
        SendFailures: Interlocked.Read(ref _sendFailures));

    /// <summary>
    /// Цикл доставки. Возвращает управление после отмены, успев предпринять
    /// последнюю попытку отправить накопленный пакет (§62).
    /// </summary>
    public async Task RunAsync(CancellationToken cancellationToken)
    {
        try
        {
            while (!cancellationToken.IsCancellationRequested)
            {
                if (await CollectBatchAsync(cancellationToken).ConfigureAwait(false) == 0)
                {
                    continue;
                }

                if (await DeliverAsync(_pending, cancellationToken).ConfigureAwait(false))
                {
                    _pending = [];
                }
            }
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            // Штатное завершение: накопленное отправит FlushOnShutdownAsync.
        }

        await FlushOnShutdownAsync().ConfigureAwait(false);
    }

    /// <summary>
    /// Собрать пакет: до <see cref="DeliverySettings.BatchSize"/> событий, но не
    /// дольше <see cref="DeliverySettings.FlushIntervalMs"/>.
    ///
    /// Ожидание ограничено, иначе одиночное событие висело бы в буфере до
    /// следующего, и «событие произошло» разошлось бы с «событие видно в истории»
    /// на неопределённое время.
    /// </summary>
    private async Task<int> CollectBatchAsync(CancellationToken cancellationToken)
    {
        _pending = new List<RawEvent>(_settings.BatchSize);

        // Первое событие ждём без ограничения по времени: пока событий нет,
        // просыпаться незачем.
        while (_pending.Count == 0)
        {
            if (!await _buffer.Reader.WaitToReadAsync(cancellationToken).ConfigureAwait(false))
            {
                return 0;
            }

            Drain();
        }

        var deadline = _timeProvider.GetUtcNow() + TimeSpan.FromMilliseconds(_settings.FlushIntervalMs);

        while (_pending.Count < _settings.BatchSize)
        {
            var remaining = deadline - _timeProvider.GetUtcNow();
            if (remaining <= TimeSpan.Zero)
            {
                break;
            }

            using var window = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            window.CancelAfter(remaining);

            try
            {
                if (!await _buffer.Reader.WaitToReadAsync(window.Token).ConfigureAwait(false))
                {
                    break;
                }
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                // Истёк интервал накопления — это и есть момент отправки.
                break;
            }

            Drain();
        }

        return _pending.Count;

        void Drain()
        {
            while (_pending.Count < _settings.BatchSize && _buffer.Reader.TryRead(out var rawEvent))
            {
                _pending.Add(rawEvent);
            }
        }
    }

    /// <summary>
    /// Доставить пакет. <c>true</c> означает, что с пакетом покончено — он либо
    /// принят, либо отвергнут необратимо. <c>false</c> — работа отменена, и пакет
    /// остаётся накопленным для финальной отправки.
    /// </summary>
    private async Task<bool> DeliverAsync(List<RawEvent> batch, CancellationToken cancellationToken)
    {
        var attempt = 0;

        while (!cancellationToken.IsCancellationRequested)
        {
            attempt++;

            try
            {
                var outcome = await _client.SendAsync(batch, cancellationToken).ConfigureAwait(false);
                RecordSuccess(batch.Count, outcome);
                return true;
            }
            catch (IngestRejectedException exception)
            {
                Interlocked.Increment(ref _batchesRejected);
                Interlocked.Add(ref _eventsRejected, batch.Count);
                Report($"[agent] пакет из {batch.Count} событий отвергнут Core и отброшен: {exception.Message}");
                return true;
            }
            catch (Exception exception) when (exception is not OperationCanceledException || !cancellationToken.IsCancellationRequested)
            {
                Interlocked.Increment(ref _sendFailures);
                Report($"[agent] попытка {attempt} отправить {batch.Count} событий не удалась: {exception.Message}");

                var delay = Backoff(attempt);
                await Task.Delay(delay, _timeProvider, cancellationToken).ConfigureAwait(false);
            }
        }

        return false;
    }

    /// <summary>
    /// Последняя попытка доставить всё, что осталось, при завершении (§62).
    ///
    /// Из буфера добирается остаток: чистая остановка не должна съедать события,
    /// которые уже приняты, но ещё не отправлены. Ограничение по времени здесь
    /// своё, а не признак отмены — завершение это ровно тот момент, когда отправка
    /// нужнее всего.
    /// </summary>
    private async Task FlushOnShutdownAsync()
    {
        using var timeout = new CancellationTokenSource(ShutdownFlushTimeout);

        while (true)
        {
            while (_pending.Count < _settings.BatchSize && _buffer.Reader.TryRead(out var rawEvent))
            {
                _pending.Add(rawEvent);
            }

            if (_pending.Count == 0)
            {
                return;
            }

            var batch = _pending;
            _pending = [];

            try
            {
                var outcome = await _client.SendAsync(batch, timeout.Token).ConfigureAwait(false);
                RecordSuccess(batch.Count, outcome);
            }
            catch (Exception exception)
            {
                Interlocked.Increment(ref _sendFailures);
                JsonLog.Error(
                    "shutdown_flush_failed",
                    "не удалось отправить события при завершении",
                    ("events", batch.Count),
                    ("error", exception.Message));
                return;
            }
        }
    }

    private void RecordSuccess(int count, IngestOutcome outcome)
    {
        Interlocked.Increment(ref _batchesSent);
        Interlocked.Add(ref _eventsSent, outcome.Accepted);
        Interlocked.Add(ref _eventsDuplicate, outcome.Duplicates);
        Interlocked.Add(ref _eventsRejected, outcome.Rejected);

        if (outcome.NormalizationFailed > 0)
        {
            Report($"[agent] Core сохранил, но не нормализовал {outcome.NormalizationFailed} событий из {count}");
        }
    }

    /// <summary>Экспоненциальная задержка с потолком: недоступный Core не должен превращаться в поток запросов.</summary>
    private static TimeSpan Backoff(int attempt)
    {
        var scaled = FirstRetryDelay * Math.Pow(2, Math.Min(attempt - 1, 10));
        return scaled > MaxRetryDelay ? MaxRetryDelay : scaled;
    }

    private static void Report(string message) => JsonLog.Error("sender", message);
}
