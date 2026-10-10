namespace Chronoscope.Agent.Collectors;

/// <summary>
/// Наблюдение за записью журнала Windows, приведённое к платформенно-нейтральному виду.
///
/// Это не событие и не payload: здесь нет ни идентификаторов Chronoscope, ни
/// времени наблюдения. Разделение то же, что у <see cref="ProcessObservation"/>,
/// и нужно по той же причине — преобразование наблюдения в контрактное событие
/// проверяется обычными тестами, без Windows и без журнала.
///
/// Все поля, кроме канала, необязательные: Windows не для каждой записи сообщает
/// всё. Отсутствующее значение остаётся отсутствующим и в payload — подставлять
/// ноль вместо неизвестного идентификатора означало бы выдумать событие с
/// идентификатором 0.
/// </summary>
public sealed record EventLogObservation
{
    /// <summary>Канал журнала в канонической форме, например <c>System</c>.</summary>
    public required string Channel { get; init; }

    /// <summary>Поставщик записи, например <c>Microsoft-Windows-Kernel-General</c>.</summary>
    public string? Provider { get; init; }

    /// <summary>
    /// Идентификатор события внутри поставщика (<c>Event ID</c>).
    ///
    /// Это не идентификатор записи: один и тот же <c>Event ID</c> встречается
    /// тысячи раз, и идентичностью он не является.
    /// </summary>
    public int? EventId { get; init; }

    /// <summary>
    /// Номер записи внутри канала (<c>Record ID</c>).
    ///
    /// Данные источника, а не идентификатор Chronoscope и не ключ дедупликации:
    /// дедупликация в 0.0.4 идёт по <c>raw_event_id</c> (§34), как и раньше.
    /// </summary>
    public long? RecordId { get; init; }

    /// <summary>Уровень записи в терминах Windows: 1 — Critical, 2 — Error, 3 — Warning, 4 — Information, 5 — Verbose.</summary>
    public int? Level { get; init; }

    public int? Task { get; init; }

    public int? Opcode { get; init; }

    /// <summary>
    /// Время записи по данным журнала — то, что уходит в <c>source_timestamp</c>.
    ///
    /// Это время появления записи **в журнале**, а не обязательно время
    /// описанного ею события: часть записей System появляется позже того, о чём
    /// они сообщают (§77.2). Продукт не имеет права выдавать одно за другое, а
    /// потому разница видна и в модели: рядом с <c>source_timestamp</c> всегда
    /// есть <c>observed_at</c>.
    /// </summary>
    public DateTimeOffset? RecordedAt { get; init; }

    /// <summary>
    /// Форматированное сообщение записи. Заполняется только при включённом
    /// <c>capture_message</c>.
    ///
    /// Пока флаг выключен, это поле остаётся <c>null</c> не потому, что сообщение
    /// не удалось получить, а потому, что его не читали: сообщение может
    /// содержать пути, имена пользователей, аргументы и секреты (§37, §77.2
    /// пункт 10).
    /// </summary>
    public string? Message { get; init; }
}

/// <summary>
/// Источник наблюдений за журналом Windows.
///
/// Порт отделяет платформенный механизм подписки (<c>EventLogWatcher</c> в
/// <c>Chronoscope.Agent.Collectors.Windows</c>) от преобразования наблюдения в
/// контрактное событие, которое платформенно-нейтрально. Благодаря этому
/// единственная часть коллектора журнала, требующая Windows, — реализация этого
/// интерфейса, и её можно подменить в тестах.
/// </summary>
public interface IEventLogObservationSource
{
    /// <summary>
    /// Наблюдать до отмены, вызывая <paramref name="onObservation"/> на каждую
    /// появившуюся запись.
    ///
    /// Обработчик вызывается потоком подписки Windows, а не потоком вызова этого
    /// метода, поэтому он обязан быть быстрым и не бросать исключений. Отказ
    /// подписки — это не исключение внутри обработчика, а завершение метода с
    /// <see cref="Abstractions.CollectorException"/>: иначе сбой канала остался бы
    /// строкой в логе, а Agent продолжал бы считать источник живым, и потеря
    /// наблюдения выглядела бы как тишина в журнале.
    /// </summary>
    Task WatchAsync(Action<EventLogObservation> onObservation, CancellationToken cancellationToken);
}
