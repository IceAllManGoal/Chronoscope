using Chronoscope.Agent.Abstractions;
using Chronoscope.Agent.Logging;

namespace Chronoscope.Agent.Collectors;

/// <summary>Чем закончилось наблюдение одного коллектора к моменту снимка (§8.5).</summary>
public enum CollectorOutcome
{
    /// <summary>Наблюдение идёт.</summary>
    Observing,

    /// <summary>Наблюдение прекращено отменой — так и было задумано.</summary>
    StoppedByRequest,

    /// <summary>Коллектор вернул управление, хотя остановки не просили: наблюдение прекратилось молча.</summary>
    StoppedUnexpectedly,

    /// <summary>Отказ коллектора: источник недоступен, подписка оборвалась или код коллектора упал.</summary>
    Failed,
}

/// <summary>Состояние одного коллектора.</summary>
public sealed record CollectorStatus(string Name, CollectorOutcome Outcome, string? Detail)
{
    /// <summary>
    /// Потеряно ли наблюдение за этим источником.
    ///
    /// Молчаливая остановка считается потерей наравне с отказом: наблюдения нет
    /// в обоих случаях, а разница только в том, кто о ней сообщил.
    /// </summary>
    public bool IsLost => Outcome is CollectorOutcome.Failed or CollectorOutcome.StoppedUnexpectedly;
}

/// <summary>
/// Несколько коллекторов в одном Agent (§8.3, §8.5, §77.2).
///
/// Один коллектор — один источник; источников у Chronoscope со временем будет
/// несколько, и наблюдать их обязан один процесс Agent: иначе каждый источник
/// завёл бы собственную доставку, собственный буфер и собственную недоступность
/// Core. Приёмник и буфер здесь общие, а коллектор остаётся таким, каким описан
/// в §8.3, — он не знает, что работает не один.
///
/// Главное свойство — <b>изоляция отказов</b>. Источник может быть недоступен,
/// подписка может оборваться, код коллектора может упасть; во всех случаях
/// теряется наблюдение только за этим источником, а остальные продолжают
/// работать. Ради этого свойства и появился этот класс: до него первое же
/// исключение коллектора завершало Agent целиком, то есть отказ одного источника
/// означал потерю наблюдения за всеми.
///
/// Agent живёт, пока жив хотя бы один коллектор. Если потеряны все, ждать
/// больше нечего: <see cref="RunAsync"/> возвращает управление, точка входа
/// завершает работу и сообщает об этом кодом возврата. Процесс, который ничего
/// не наблюдает, но выглядит живым, — худший из возможных исходов.
///
/// Чего здесь намеренно нет: автоматического перезапуска упавшего коллектора.
/// Политика повторов — отдельное решение с отдельным вопросом «сколько ждать и
/// что делать с событиями, пропущенными за время попыток», и принимать его
/// раньше измерения незачем (§77.2).
/// </summary>
public sealed class CollectorSupervisor
{
    private readonly IReadOnlyList<IEventCollector> _collectors;
    private readonly IRawEventSink _sink;
    private readonly CollectorStatus[] _statuses;

    /// <param name="collectors">
    /// Коллекторы в том порядке, в котором о них нужно сообщать. Пустой список
    /// допустим: «ни один источник не включён» — это состояние конфигурации, а не
    /// ошибка вызова, и точка входа не обязана его разбирать.
    /// </param>
    /// <param name="sink">Общий приёмник сырых событий для всех коллекторов.</param>
    public CollectorSupervisor(IReadOnlyList<IEventCollector> collectors, IRawEventSink sink)
    {
        ArgumentNullException.ThrowIfNull(collectors);
        ArgumentNullException.ThrowIfNull(sink);

        // Имена обязаны различаться: имя коллектора попадает в поле `collector`
        // сырого события, и два одинаковых имени сделали бы неразличимым, откуда
        // пришло событие и какой из коллекторов отказал.
        var duplicates = collectors
            .GroupBy(collector => collector.Name, StringComparer.Ordinal)
            .Where(group => group.Count() > 1)
            .Select(group => group.Key)
            .ToArray();

        if (duplicates.Length > 0)
        {
            throw new ArgumentException(
                $"имена коллекторов должны различаться, повторяются: {string.Join(", ", duplicates)}",
                nameof(collectors));
        }

        _collectors = collectors;
        _sink = sink;
        _statuses = collectors
            .Select(collector => new CollectorStatus(collector.Name, CollectorOutcome.Observing, null))
            .ToArray();
    }

    /// <summary>Сколько коллекторов передано в наблюдение.</summary>
    public int Count => _collectors.Count;

    /// <summary>
    /// Снимок состояния. Копия, а не живой массив: читатель не должен видеть
    /// половину обновления, а состояние меняется из задач коллекторов.
    /// </summary>
    public IReadOnlyList<CollectorStatus> Statuses => _statuses.ToArray();

    /// <summary>Сколько источников потеряно: отказ или молчаливая остановка.</summary>
    public int LostCount => Statuses.Count(status => status.IsLost);

    /// <summary>
    /// Запустить все коллекторы и ждать, пока их не остановит отмена.
    ///
    /// Возвращает управление и раньше — если потеряны все коллекторы: наблюдать
    /// больше нечего.
    /// </summary>
    public async Task RunAsync(CancellationToken cancellationToken)
    {
        if (_collectors.Count == 0)
        {
            JsonLog.Warning("no_collectors", "не включён ни один коллектор: наблюдение не ведётся");
            await WaitForCancellationAsync(cancellationToken).ConfigureAwait(false);
            return;
        }

        var tasks = new Task[_collectors.Count];
        for (var index = 0; index < _collectors.Count; index++)
        {
            tasks[index] = ObserveAsync(index, cancellationToken);
        }

        // Ждём все коллекторы, а не первый завершившийся: отказ одного не должен
        // оставлять остальные без присмотра и без доставки.
        await Task.WhenAll(tasks).ConfigureAwait(false);

        if (LostCount == _collectors.Count)
        {
            JsonLog.Error(
                "all_collectors_lost",
                "наблюдение прекращено полностью: ни один коллектор не работает",
                ("collectors", _collectors.Count));
        }
    }

    /// <summary>
    /// Наблюдение одним коллектором. Исключения здесь не покидают метод ни при
    /// каком исходе — кроме отмены, которая исходом не является: она и есть способ
    /// остановить наблюдение.
    /// </summary>
    private async Task ObserveAsync(int index, CancellationToken cancellationToken)
    {
        var collector = _collectors[index];
        JsonLog.Info("collector_started", "наблюдение начато", ("collector", collector.Name));

        try
        {
            await collector.StartAsync(_sink, cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            SetStatus(index, CollectorOutcome.StoppedByRequest, null);
            return;
        }
        catch (CollectorException exception)
        {
            Fail(index, "collector_failed", exception.Message);
            return;
        }
        catch (Exception exception)
        {
            // Неожиданное исключение изолируется так же, как объявленный отказ.
            //
            // Это решение, а не небрежность: обещание версии в том, что
            // наблюдение за одним источником не зависит от здоровья чужого кода,
            // и ошибка в коде нового коллектора не должна ослеплять Chronoscope
            // целиком. Отличие от CollectorException остаётся видимым: другой
            // идентификатор события в логе (`collector_crashed`, а не
            // `collector_failed`), тип исключения в причине и ненулевой код
            // возврата при завершении. Основание — ADR-0016.
            Fail(index, "collector_crashed", $"{exception.GetType().Name}: {exception.Message}");
            return;
        }

        if (cancellationToken.IsCancellationRequested)
        {
            SetStatus(index, CollectorOutcome.StoppedByRequest, null);
            return;
        }

        SetStatus(index, CollectorOutcome.StoppedUnexpectedly, "наблюдение прекратилось без отмены");
        JsonLog.Warning(
            "collector_stopped",
            "коллектор прекратил наблюдение, хотя остановки не было",
            ("collector", collector.Name));
    }

    private void Fail(int index, string eventName, string reason)
    {
        SetStatus(index, CollectorOutcome.Failed, reason);
        JsonLog.Error(eventName, reason, ("collector", _collectors[index].Name));
    }

    private void SetStatus(int index, CollectorOutcome outcome, string? detail)
        => Interlocked.Exchange(ref _statuses[index], new CollectorStatus(_collectors[index].Name, outcome, detail));

    private static async Task WaitForCancellationAsync(CancellationToken cancellationToken)
    {
        try
        {
            await Task.Delay(Timeout.Infinite, cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            // Штатная остановка.
        }
    }
}
