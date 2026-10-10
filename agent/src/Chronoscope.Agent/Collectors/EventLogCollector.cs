using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Identity;

namespace Chronoscope.Agent.Collectors;

/// <summary>
/// Коллектор событий журнала Windows (§8.4, §77.2).
///
/// Тонкий, как и коллектор процессов: он получает наблюдения от источника,
/// превращает их в контрактные события и отдаёт в приёмник. Вся работа с Windows
/// Event Log API — в реализации <see cref="IEventLogObservationSource"/>.
///
/// Отличие от процессов стоит держать в голове: журнал уведомляет о записях сам,
/// и обработчик вызывается потоком Windows. Поэтому коллектор не владеет циклом
/// ожидания, а сбой подписки приходит к нему завершением <see cref="StartAsync"/> с
/// ошибкой, а не тишиной в истории.
/// </summary>
public sealed class EventLogCollector : IEventCollector
{
    /// <summary>Имя коллектора. Совпадает с тем, что знает Core (<c>COLLECTOR_WINDOWS_EVENTLOG</c>).</summary>
    public const string CollectorName = "windows.eventlog";

    private readonly IEventLogObservationSource _source;
    private readonly EventLogRecordMapper _mapper;

    public EventLogCollector(IEventLogObservationSource source, EventLogRecordMapper mapper)
    {
        ArgumentNullException.ThrowIfNull(source);
        ArgumentNullException.ThrowIfNull(mapper);

        _source = source;
        _mapper = mapper;
    }

    public string Name => CollectorName;

    /// <summary>Собрать коллектор по идентичности и версии.</summary>
    public static EventLogCollector Create(
        IEventLogObservationSource source,
        AgentIdentity identity,
        string collectorVersion,
        TimeProvider? timeProvider = null)
        => new(source, new EventLogRecordMapper(identity, collectorVersion, timeProvider));

    public Task StartAsync(IRawEventSink sink, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(sink);

        // Публикация идёт прямо из обработчика подписки: приёмник неблокирующий
        // (§8.3), поэтому очередь между Windows и буфером не нужна. Своя очередь
        // здесь была бы вторым буфером — с собственным переполнением, которое
        // никто не считает.
        return _source.WatchAsync(observation => sink.Publish(_mapper.Map(observation)), cancellationToken);
    }
}
