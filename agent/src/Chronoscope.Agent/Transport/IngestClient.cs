using System.Net;
using System.Text;
using System.Text.Json;
using Chronoscope.Agent.Contract;

namespace Chronoscope.Agent.Transport;

/// <summary>Итог приёма одного пакета, как его сообщает Core (§31).</summary>
public sealed record IngestOutcome(int Accepted, int Duplicates, int NormalizationFailed, int Rejected)
{
    public static readonly IngestOutcome Empty = new(0, 0, 0, 0);
}

/// <summary>
/// Доставка пакета в Core.
///
/// Отдельный порт нужен не ради абстракции: логика накопления пакета, повторов и
/// учёта потерь — самая ответственная часть Agent, и она обязана проверяться без
/// запуска HTTP-сервера. Реализация для настоящего Core — <see cref="HttpIngestClient"/>.
/// </summary>
public interface IIngestClient
{
    /// <summary>
    /// Отправить пакет. Исключение означает <b>повторяемый</b> отказ (сеть, 5xx,
    /// таймаут): отправитель повторит попытку. Необратимый отказ возвращается как
    /// результат, а не как исключение, потому что повторять его бессмысленно.
    /// </summary>
    Task<IngestOutcome> SendAsync(IReadOnlyList<RawEvent> events, CancellationToken cancellationToken);
}

/// <summary>Пакет отвергнут Core по причине, которая не изменится при повторе.</summary>
public sealed class IngestRejectedException(string message) : Exception(message);

/// <summary>Доставка пакетов в Core по HTTP (§31, §34).</summary>
public sealed class HttpIngestClient : IIngestClient, IDisposable
{
    private readonly HttpClient _httpClient;

    public HttpIngestClient(Uri ingestUri, TimeSpan timeout, HttpMessageHandler? handler = null)
    {
        IngestUri = ingestUri;
        _httpClient = handler is null ? new HttpClient() : new HttpClient(handler, disposeHandler: false);
        _httpClient.Timeout = timeout;
    }

    public Uri IngestUri { get; }

    public async Task<IngestOutcome> SendAsync(IReadOnlyList<RawEvent> events, CancellationToken cancellationToken)
    {
        ArgumentNullException.ThrowIfNull(events);

        var payload = RawEventJson.SerializeBatch(events);
        using var content = new StringContent(payload, Encoding.UTF8, "application/json");

        using var response = await _httpClient.PostAsync(IngestUri, content, cancellationToken).ConfigureAwait(false);

        if (!response.IsSuccessStatusCode)
        {
            var body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);

            // 5xx, 408 и 429 — состояние Core или транспорта, которое может
            // измениться: повторяем. Остальные 4xx означают, что пакет не будет
            // принят никогда: повторять его — значит навсегда застопорить
            // очередь, потому что отправитель держит пакет до успеха.
            var retryable = (int)response.StatusCode >= 500
                || response.StatusCode is HttpStatusCode.RequestTimeout or HttpStatusCode.TooManyRequests;

            var message = $"Core ответил {(int)response.StatusCode} {response.StatusCode} на пакет из {events.Count} событий: {Trim(body)}";

            if (retryable)
            {
                throw new HttpRequestException(message);
            }

            throw new IngestRejectedException(message);
        }

        var json = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
        return ParseOutcome(json);
    }

    public void Dispose() => _httpClient.Dispose();

    /// <summary>
    /// Разобрать ответ Core. Неизвестная форма ответа не считается успехом:
    /// «отправили и не поняли ответ» — это не подтверждённая доставка.
    /// </summary>
    private static IngestOutcome ParseOutcome(string json)
    {
        try
        {
            using var document = JsonDocument.Parse(json);
            var root = document.RootElement;

            return new IngestOutcome(
                Accepted: GetInt(root, "accepted"),
                Duplicates: GetInt(root, "duplicates"),
                NormalizationFailed: GetInt(root, "normalization_failed"),
                Rejected: root.TryGetProperty("rejected", out var rejected) && rejected.ValueKind == JsonValueKind.Array
                    ? rejected.GetArrayLength()
                    : 0);
        }
        catch (JsonException exception)
        {
            throw new HttpRequestException($"ответ Core не разобран как JSON: {exception.Message}", exception);
        }
    }

    private static int GetInt(JsonElement root, string name)
        => root.TryGetProperty(name, out var value) && value.TryGetInt32(out var number) ? number : 0;

    private static string Trim(string body) => body.Length <= 300 ? body : body[..300] + "…";
}
