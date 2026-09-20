using System.Management;
using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Logging;

namespace Chronoscope.Agent.Collectors.Windows;

/// <summary>
/// Время загрузки ОС из WMI (§16).
///
/// Берётся <c>LastBootUpTime</c>, а не аптайм процесса: аптайм зависит от того,
/// когда запущен Agent, и не переживает спящий режим одинаковым образом, а
/// <c>boot_id</c> обязан быть одинаковым при каждом запуске внутри одной
/// загрузки — иначе события одной загрузки распадутся на разные сессии.
///
/// Сведения о загрузке не являются идентифицирующей информацией о машине, в
/// отличие от серийного номера или имени компьютера, поэтому §15 не нарушается.
/// </summary>
public sealed class WindowsBootSessionProvider : IBootSessionProvider
{
    private const string CimV2Scope = @"\\.\root\cimv2";
    private const string Query = "SELECT LastBootUpTime FROM Win32_OperatingSystem";

    public DateTimeOffset? GetBootTimeUtc()
    {
        try
        {
            using var searcher = new ManagementObjectSearcher(CimV2Scope, Query);
            foreach (var operatingSystem in searcher.Get().Cast<ManagementBaseObject>())
            {
                using (operatingSystem)
                {
                    if (operatingSystem["LastBootUpTime"] is not string raw || string.IsNullOrEmpty(raw))
                    {
                        continue;
                    }

                    // WMI отдаёт DMTF-дату, конвертер возвращает локальное время:
                    // приводим к UTC, потому что в событиях хранится только UTC (§24).
                    var local = ManagementDateTimeConverter.ToDateTime(raw);
                    return new DateTimeOffset(DateTime.SpecifyKind(local, DateTimeKind.Local)).ToUniversalTime();
                }
            }
        }
        catch (ManagementException exception)
        {
            JsonLog.Warning("boot_time_unavailable", "не удалось определить время загрузки ОС", ("error", exception.Message));
        }
        catch (UnauthorizedAccessException exception)
        {
            JsonLog.Warning("boot_time_unavailable", "доступ к WMI запрещён", ("error", exception.Message));
        }

        // §16 допускает отсутствие boot_id: подставлять догадку нельзя, потому
        // что выдуманный идентификатор склеил бы события разных загрузок.
        return null;
    }
}
