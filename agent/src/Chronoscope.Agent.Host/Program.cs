using Chronoscope.Agent;
using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Collectors;
using Chronoscope.Agent.Collectors.Windows;
using Chronoscope.Agent.Configuration;
using Chronoscope.Agent.Identity;
using Chronoscope.Agent.Logging;
using Chronoscope.Agent.Transport;

// Точка входа Agent (§49, §63).
//
// Два процесса в двух терминалах:
//   Terminal 1:  cd core && uv run python -m chronoscope
//   Terminal 2:  dotnet run --project agent/src/Chronoscope.Agent.Host
//
// Точка входа — Host: композиционный корень обязан знать и ядро, и платформенные
// коллекторы ([ADR-0011](../../docs/decisions/0011-agent-project-structure.md)).

AgentSettings settings;
try
{
    settings = SettingsLoader.Load();
}
catch (ConfigurationException exception)
{
    JsonLog.Error("configuration_invalid", exception.Message);
    return 1;
}

var identity = AgentIdentity.Resolve(settings.DataDirectory, new WindowsBootSessionProvider());

using var shutdown = new CancellationTokenSource();
Console.CancelKeyPress += (_, arguments) =>
{
    // Отменяем сами, чтобы успеть доставить накопленное: немедленная смерть
    // процесса означала бы молчаливую потерю уже принятых событий.
    arguments.Cancel = true;
    shutdown.Cancel();
};

var buffer = new BoundedEventBuffer(settings.Delivery.BufferCapacity);
using var client = new HttpIngestClient(
    settings.Core.IngestUri,
    TimeSpan.FromMilliseconds(settings.Delivery.RequestTimeoutMs));
var sender = new BatchSender(buffer, client, settings.Delivery);

JsonLog.Info(
    "agent_started",
    "Chronoscope Agent запущен",
    ("version", AgentInfo.Version),
    ("core", settings.Core.IngestUri.ToString()),
    ("host_id", identity.HostId),
    ("boot_id", identity.BootId),
    ("batch_size", settings.Delivery.BatchSize),
    ("flush_interval_ms", settings.Delivery.FlushIntervalMs),
    ("buffer_capacity", settings.Delivery.BufferCapacity),
    ("capture_path", settings.Process.CapturePath),
    ("capture_command_line", settings.Process.CaptureCommandLine),
    ("capture_user", settings.Process.CaptureUser));

var senderTask = sender.RunAsync(shutdown.Token);

// Список коллекторов собирается здесь и только здесь: композиционный корень —
// единственное место, знающее и о конфигурации, и о платформенных реализациях.
// Коллекторы друг о друге не знают; за то, чтобы они жили одновременно и не
// роняли друг друга, отвечает CollectorSupervisor (§8.3, §8.5).
var collectors = new List<IEventCollector>();

if (settings.Process.Enabled)
{
    var source = new WindowsProcessObservationSource(settings.Process);
    collectors.Add(ProcessCollector.Create(source, settings, identity, AgentInfo.Version));
}
else
{
    JsonLog.Warning(
        "collector_disabled",
        "коллектор процессов выключен: события процессов не собираются",
        ("collector", ProcessCollector.CollectorName));
}

// Здесь же появится windows.eventlog (§77.2), и ему не понадобится ни свой
// буфер, ни свой транспорт: приёмник у коллекторов общий.

var supervisor = new CollectorSupervisor(collectors, buffer);

try
{
    await supervisor.RunAsync(shutdown.Token);
}
catch (OperationCanceledException)
{
    // Штатная остановка по Ctrl+C: коллекторы уже остановлены отменой.
}

// Закрываем запись, чтобы отправитель дочитал буфер, отправил остаток и завершился.
buffer.Complete();
await senderTask;

var statistics = sender.Snapshot();
JsonLog.Info(
    "agent_stopped",
    "Chronoscope Agent остановлен",
    ("events_accepted", statistics.EventsAccepted),
    ("events_sent", statistics.EventsSent),
    ("events_duplicate", statistics.EventsDuplicate),
    ("events_rejected", statistics.EventsRejected),
    ("events_dropped", statistics.EventsDropped),
    ("batches_sent", statistics.BatchesSent),
    ("batches_rejected", statistics.BatchesRejected),
    ("send_failures", statistics.SendFailures),
    ("collectors", collectors.Count),
    ("collectors_lost", supervisor.LostCount));

var lost = supervisor.Statuses.Where(status => status.IsLost).ToArray();
if (lost.Length > 0)
{
    // Итог одной строкой: причины уже были сказаны в момент отказа, но в длинном
    // логе их к моменту остановки не видно, а знать, чем кончился прогон, нужно.
    JsonLog.Error(
        "collectors_lost_summary",
        "часть источников осталась без наблюдения",
        ("lost", string.Join(", ", lost.Select(status => $"{status.Name} ({status.Outcome}: {status.Detail})"))),
        ("total", collectors.Count));
}

// Ненулевой код возврата, если хотя бы один источник остался без наблюдения: то
// же сообщение, что было у отказа коллектора раньше, — но теперь Agent доживает
// до штатного завершения, а не падает на первом отказе.
return supervisor.LostCount > 0 ? 1 : 0;
