using System.Diagnostics;
using System.Threading.Channels;
using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Contract;

namespace Chronoscope.Agent.Transport;

/// <summary>
/// Ограниченная очередь между коллектором и отправкой (§34).
///
/// Буфер обязан быть ограниченным: без этого недоступность Core означала бы
/// неограниченный рост памяти Agent, то есть отказ доставки превратился бы в
/// отказ всего процесса. Канал выбран с <c>FullMode.Wait</c> специально: только
/// в этом режиме <see cref="ChannelWriter{T}.TryWrite"/> сообщает «не поместилось»,
/// а остальные режимы молча выбрасывают событие и не дают его сосчитать.
///
/// Что происходит при переполнении: событие <b>не принимается</b>, а факт
/// потери увеличивает <see cref="DroppedCount"/> и попадает в лог. Блокировать
/// коллектор нельзя — он держит поток событий от Windows, и ожидание в нём
/// означало бы потерю уже произошедших событий вместо одного лишнего.
/// </summary>
public sealed class BoundedEventBuffer : IRawEventSink
{
    private readonly Channel<RawEvent> _channel;
    private long _dropped;
    private long _accepted;

    public BoundedEventBuffer(int capacity)
    {
        if (capacity <= 0)
        {
            throw new ArgumentOutOfRangeException(nameof(capacity), capacity, "ёмкость буфера должна быть положительной");
        }

        Capacity = capacity;
        _channel = Channel.CreateBounded<RawEvent>(new BoundedChannelOptions(capacity)
        {
            // Один читатель — отправляющий цикл; писателей может быть несколько,
            // если когда-нибудь появятся параллельные коллекторы.
            SingleReader = true,
            SingleWriter = false,
            FullMode = BoundedChannelFullMode.Wait,
        });
    }

    public int Capacity { get; }

    /// <summary>Сколько событий потеряно из-за переполнения. §61 называет это в списке метрик.</summary>
    public long DroppedCount => Interlocked.Read(ref _dropped);

    /// <summary>Сколько событий принято в буфер.</summary>
    public long AcceptedCount => Interlocked.Read(ref _accepted);

    /// <summary>Сколько событий сейчас ждёт отправки.</summary>
    public int PendingCount => _channel.Reader.Count;

    internal ChannelReader<RawEvent> Reader => _channel.Reader;

    public void Publish(RawEvent rawEvent)
    {
        ArgumentNullException.ThrowIfNull(rawEvent);

        if (_channel.Writer.TryWrite(rawEvent))
        {
            Interlocked.Increment(ref _accepted);
            return;
        }

        // Не «молча потеряли»: потерянное событие обязано быть видно и в
        // счётчике, и в логе, иначе история будет выглядеть полной, не будучи ей.
        Interlocked.Increment(ref _dropped);
        Trace.TraceWarning(
            $"буфер переполнен (ёмкость {Capacity}): событие {rawEvent.PayloadType} {rawEvent.RawEventId} потеряно");
    }

    /// <summary>Закрыть запись. Отправляющий цикл дочитает остаток и завершится.</summary>
    internal void Complete() => _channel.Writer.TryComplete();
}
