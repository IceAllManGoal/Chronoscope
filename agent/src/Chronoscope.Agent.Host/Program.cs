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
//   Terminal 2:  dotnet run --project agent/src/Chronoscope.Agent

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
var exitCode = 0;

try
{
    if (!settings.Process.Enabled)
    {
        JsonLog.Warning("collector_disabled", "коллектор процессов выключен: события не собираются");
        await Task.Delay(Timeout.Infinite, shutdown.Token);
    }
    else
    {
        var source = new WindowsProcessObservationSource(settings.Process);
        var collector = ProcessCollector.Create(source, settings, identity, AgentInfo.Version);

        JsonLog.Info("collector_started", "наблюдение за процессами начато", ("collector", collector.Name));
        await collector.StartAsync(buffer, shutdown.Token);
    }
}
catch (OperationCanceledException)
{
    // Штатная остановка по Ctrl+C.
}
catch (CollectorException exception)
{
    JsonLog.Error("collector_failed", exception.Message);
    exitCode = 1;
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
    ("send_failures", statistics.SendFailures));

return exitCode;
