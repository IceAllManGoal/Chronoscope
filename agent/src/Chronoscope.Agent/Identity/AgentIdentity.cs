using System.Security.Cryptography;
using System.Text;
using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Configuration;

namespace Chronoscope.Agent.Identity;

/// <summary>
/// Идентичность машины и загрузки ОС (§15, §16).
///
/// <b>host_id</b> — случайный локальный идентификатор, созданный один раз и
/// сохранённый рядом с данными Agent. Он не выводится ни из имени компьютера,
/// ни из серийного номера, ни из характеристик железа: §15 требует именно
/// случайности, потому что идентификатор, выведенный из железа, превратил бы
/// Chronoscope в инструмент идентификации устройства, чем он быть не должен.
///
/// <b>boot_id</b> — выводится из времени загрузки ОС, а не хранится: одна и та
/// же загрузка обязана давать одно и то же значение при каждом запуске Agent,
/// а разные загрузки — разные. Времени загрузки для этого достаточно, и оно не
/// является идентифицирующей информацией о машине.
/// </summary>
public sealed class AgentIdentity
{
    /// <summary>Имя файла с сохранённым <c>host_id</c>.</summary>
    public const string HostIdFileName = "host_id";

    private const string BootIdHashLabel = "chronoscope.boot";

    private AgentIdentity(string hostId, string? bootId)
    {
        HostId = hostId;
        BootId = bootId;
    }

    public string HostId { get; }

    /// <summary><c>null</c>, если источник не позволил определить загрузку (§16).</summary>
    public string? BootId { get; }

    /// <summary>Загрузить сохранённый <c>host_id</c> или создать новый, а затем вывести <c>boot_id</c>.</summary>
    public static AgentIdentity Resolve(
        string dataDirectory,
        IBootSessionProvider bootSessionProvider,
        TimeProvider? timeProvider = null)
    {
        var hostId = LoadOrCreateHostId(dataDirectory, timeProvider ?? TimeProvider.System);
        var bootId = FromBootTime(bootSessionProvider.GetBootTimeUtc());

        return new AgentIdentity(hostId, bootId);
    }

    /// <summary>
    /// Прочитать <c>host_id</c> из файла, а при его отсутствии — создать.
    ///
    /// Повреждённый файл — явная ошибка, а не повод молча выдать новый
    /// идентификатор: смена <c>host_id</c> означает, что вся дальнейшая история
    /// будет считаться относящейся к другой машине, и заметить это по данным
    /// невозможно (§15 защищает именно от случайного смешивания). Пользователю
    /// достаточно удалить файл, чтобы получить новый идентификатор осознанно.
    /// </summary>
    public static string LoadOrCreateHostId(string dataDirectory, TimeProvider timeProvider)
    {
        var path = Path.Combine(dataDirectory, HostIdFileName);

        if (File.Exists(path))
        {
            var stored = File.ReadAllText(path).Trim();
            if (stored.StartsWith("host_", StringComparison.Ordinal) && Ulid.IsValid(stored["host_".Length..]))
            {
                return stored;
            }

            throw new ConfigurationException(
                $"{path} не содержит корректный host_id. Ожидается строка вида host_<ULID>. "
                + "Удали файл, чтобы Agent создал новый идентификатор — но учти, что после этого "
                + "события до и после будут относиться к разным машинам (§15)");
        }

        var hostId = Ulid.NewPrefixed("host", timeProvider.GetUtcNow());
        WriteAtomically(path, hostId);

        return hostId;
    }

    /// <summary>
    /// Вывести <c>boot_id</c> из времени загрузки ОС.
    ///
    /// Формула детерминирована: временная часть ULID — само время загрузки,
    /// случайная — первые 80 бит SHA-256 от метки и этого времени. Поэтому
    /// значение воспроизводимо внутри одной загрузки и не может совпасть между
    /// разными: время загрузки у них разное.
    /// </summary>
    public static string? FromBootTime(DateTimeOffset? bootTimeUtc)
    {
        if (bootTimeUtc is null)
        {
            return null;
        }

        var utc = bootTimeUtc.Value.ToUniversalTime();
        var material = Encoding.UTF8.GetBytes($"{BootIdHashLabel}|{utc:O}");
        var digest = SHA256.HashData(material);

        return $"boot_{Ulid.FromParts(Ulid.ToMilliseconds(utc), digest.AsSpan(0, 10))}";
    }

    /// <summary>
    /// Записать файл через промежуточный и переименование.
    ///
    /// Прямая запись оставила бы частично записанный файл при сбое, а он на
    /// следующем запуске был бы прочитан как повреждённый — то есть
    /// неаккуратность превратилась бы в отказ старта.
    /// </summary>
    private static void WriteAtomically(string path, string contents)
    {
        var directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var temporary = path + ".tmp";
        File.WriteAllText(temporary, contents + Environment.NewLine);
        File.Move(temporary, path, overwrite: true);
    }
}
