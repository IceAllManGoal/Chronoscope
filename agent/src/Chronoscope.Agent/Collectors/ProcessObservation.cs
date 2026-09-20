namespace Chronoscope.Agent.Collectors;

/// <summary>
/// Наблюдение за процессом, уже приведённое к платформенно-нейтральному виду.
///
/// Это не событие и не payload: здесь нет ни идентификаторов Chronoscope, ни
/// времени наблюдения. Такое разделение позволяет проверять преобразование
/// наблюдения в сырое событие обычными тестами, без Windows и без WMI.
/// </summary>
public sealed record ProcessObservation
{
    /// <summary>PID процесса. Переиспользуется ОС, поэтому идентичностью не является (§14).</summary>
    public required int ProcessId { get; init; }

    public int? ParentProcessId { get; init; }

    /// <summary>Имя образа, например <c>notepad.exe</c>.</summary>
    public required string Name { get; init; }

    public string? ExecutablePath { get; init; }

    public string? CommandLine { get; init; }

    public string? UserSid { get; init; }

    /// <summary>
    /// Время старта экземпляра процесса.
    ///
    /// Нужно и для события запуска, и для события выхода: Core выводит из него
    /// <c>process_instance_id</c> (§14), и если у выхода времени старта не будет,
    /// он выведет другой идентификатор — связь «процесс запустился → процесс
    /// завершился» потеряется. Источник наблюдений обязан помнить время старта,
    /// чтобы сообщить его при завершении.
    /// </summary>
    public required DateTimeOffset StartedAt { get; init; }

    /// <summary>Завершение процесса, а не запуск.</summary>
    public bool IsExit { get; init; }

    public int? ExitCode { get; init; }

    /// <summary>Когда завершение было замечено. Используется как <c>exited_at</c>.</summary>
    public DateTimeOffset? ExitedAt { get; init; }
}

/// <summary>
/// Источник наблюдений за процессами.
///
/// Порт отделяет платформенный механизм наблюдения (WMI в
/// <c>Chronoscope.Agent.Collectors.Windows</c>) от преобразования наблюдения в
/// контрактное событие, которое платформенно-нейтрально. Благодаря этому
/// единственная часть Agent, требующая Windows, — реализация этого интерфейса.
/// </summary>
public interface IProcessObservationSource
{
    /// <summary>
    /// Наблюдать до отмены, вызывая <paramref name="onObservation"/> на каждое
    /// замеченное событие процесса.
    ///
    /// Обработчик обязан быть быстрым: он вызывается в потоке источника, и его
    /// задержка означала бы пропуск событий, которые в этот момент происходят.
    /// </summary>
    Task WatchAsync(Action<ProcessObservation> onObservation, CancellationToken cancellationToken);
}
