using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Configuration;
using Chronoscope.Agent.Identity;

namespace Chronoscope.Agent.Collectors;

/// <summary>
/// Коллектор событий процессов (§8.4).
///
/// Задача 0.0.1 — проверить pipeline, а не собрать максимум данных, поэтому
/// коллектор намеренно тонкий: он получает наблюдения от источника, превращает
/// их в контрактные события и отдаёт в приёмник. Вся платформенная работа — в
/// реализации <see cref="IProcessObservationSource"/>.
/// </summary>
public sealed class ProcessCollector : IEventCollector
{
    /// <summary>Имя коллектора. Совпадает с тем, что знает Core (<c>COLLECTOR_WINDOWS_PROCESS</c>).</summary>
    public const string CollectorName = "windows.process";

    /// <summary>Дискриминатор payload для запуска процесса.</summary>
    public const string PayloadProcessStart = "process_start";

    /// <summary>Дискриминатор payload для завершения процесса.</summary>
    public const string PayloadProcessExit = "process_exit";

    private readonly IProcessObservationSource _source;
    private readonly ProcessEventMapper _mapper;

    public ProcessCollector(IProcessObservationSource source, ProcessEventMapper mapper)
    {
        _source = source;
        _mapper = mapper;
    }

    public string Name => CollectorName;

    /// <summary>Собрать коллектор по настройкам и идентичности.</summary>
    public static ProcessCollector Create(
        IProcessObservationSource source,
        AgentSettings settings,
        AgentIdentity identity,
        string collectorVersion,
        TimeProvider? timeProvider = null)
        => new(
            source,
            new ProcessEventMapper(identity, settings.Process, settings.Privacy, collectorVersion, timeProvider));

    public Task StartAsync(IRawEventSink sink, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(sink);

        return _source.WatchAsync(observation => sink.Publish(_mapper.Map(observation)), cancellationToken);
    }

    /// <summary>Дискриминатор payload по виду наблюдения.</summary>
    public static string PayloadTypeFor(bool isExit) => isExit ? PayloadProcessExit : PayloadProcessStart;
}
