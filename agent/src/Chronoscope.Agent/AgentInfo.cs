namespace Chronoscope.Agent;

/// <summary>
/// Сведения о сборке Agent.
///
/// Версия задана константой, а не взята из версии сборки: в событиях есть поле
/// <c>collector_version</c> с ограничением длины (1..64), и значение вида
/// <c>0.0.1.0</c> из метаданных сборки выглядело бы там случайным.
/// </summary>
public static class AgentInfo
{
    public const string Version = "0.0.1";
}
