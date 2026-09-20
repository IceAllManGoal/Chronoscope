using Chronoscope.Agent.Identity;

namespace Chronoscope.Agent.Tests;

/// <summary>
/// ULID должен совпадать с реализацией Core символ в символ.
///
/// Эталонные значения ниже получены выполнением
/// <c>chronoscope.domain.ids.ulid_from</c> из Core — то есть проверяется не
/// «наш ULID похож на ULID», а совместимость двух независимых реализаций. Если
/// они разойдутся, Core отвергнет идентификаторы Agent: его
/// <c>domain/validation.py</c> принимает только 26 заглавных символов Crockford
/// base32, и UUIDv7, который схема тоже допускает, ему не подойдёт.
/// </summary>
public class UlidTests
{
    private static readonly byte[] Zeros = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
    private static readonly byte[] Range = [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09];
    private static readonly byte[] Hex = [0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88, 0x99];
    private static readonly byte[] Maximum = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF];

    [Theory]
    [InlineData(0L, "00000000000000000000000000")]
    [InlineData(1700000000000L, "01HF7YAT000000000000000000")]
    [InlineData(281474976710655L, "7ZZZZZZZZZ0000000000000000")]
    [InlineData(1787000000123L, "01M08R03KV0000000000000000")]
    public void MatchesPythonForZeroRandomPart(long timestampMs, string expected)
        => Assert.Equal(expected, Ulid.FromParts(timestampMs, Zeros));

    [Fact]
    public void MatchesPythonForByteRangeRandomPart()
    {
        Assert.Equal("0000000000000G40R40M30E209", Ulid.FromParts(0, Range));
        Assert.Equal("01HF7YAT00000G40R40M30E209", Ulid.FromParts(1700000000000L, Range));
    }

    [Fact]
    public void MatchesPythonForHexRandomPart()
    {
        Assert.Equal("01M08R03KV008J4CT4ANK7F24S", Ulid.FromParts(1787000000123L, Hex));
    }

    [Fact]
    public void MatchesPythonForMaximumParts()
    {
        Assert.Equal("7ZZZZZZZZZZZZZZZZZZZZZZZZZ", Ulid.FromParts(281474976710655L, Maximum));
    }

    [Fact]
    public void IsDeterministicForSameInput()
    {
        Assert.Equal(Ulid.FromParts(1787000000123L, Hex), Ulid.FromParts(1787000000123L, Hex));
    }

    [Fact]
    public void RejectsTimestampOutsideUlidRange()
    {
        Assert.Throws<ArgumentOutOfRangeException>(() => Ulid.FromParts(-1, Zeros));
        Assert.Throws<ArgumentOutOfRangeException>(() => Ulid.FromParts(1L << 48, Zeros));
    }

    [Fact]
    public void RejectsRandomPartOfWrongLength()
    {
        Assert.Throws<ArgumentException>(() => Ulid.FromParts(0, [1, 2, 3]));
        Assert.Throws<ArgumentException>(() => Ulid.FromParts(0, new byte[11]));
    }

    [Fact]
    public void NewProducesCanonicalForm()
    {
        var ulid = Ulid.New(DateTimeOffset.UnixEpoch);

        Assert.Equal(Ulid.Length, ulid.Length);
        Assert.True(Ulid.IsValid(ulid));
        Assert.Matches("^[0-9A-HJKMNP-TV-Z]{26}$", ulid);
    }

    [Fact]
    public void NewIsTimeSortable()
    {
        var earlier = Ulid.New(DateTimeOffset.Parse("2026-09-18T10:42:15.220Z"));
        var later = Ulid.New(DateTimeOffset.Parse("2026-09-18T10:44:03.114Z"));

        Assert.True(string.CompareOrdinal(earlier, later) < 0, $"'{earlier}' должен быть меньше '{later}'");
    }

    /// <summary>
    /// Алфавит Crockford исключает I, L, O и U — их путают с 1 и 0 при чтении
    /// идентификаторов глазами. Проверка ловит случайную подмену алфавита.
    /// </summary>
    [Theory]
    [InlineData("01HF7YAT000000000000000000", true)]
    [InlineData("01HF7YAT00000000000000000", false)]  // 25 символов
    [InlineData("01HF7YAT0000000000000000000", false)] // 27 символов
    [InlineData("01HF7YAT00000000000000000I", false)]  // I
    [InlineData("01HF7YAT00000000000000000L", false)]  // L
    [InlineData("01HF7YAT00000000000000000O", false)]  // O
    [InlineData("01HF7YAT00000000000000000U", false)]  // U
    [InlineData("01hf7yat000000000000000000", false)]  // строчные
    [InlineData("", false)]
    [InlineData(null, false)]
    public void IsValidEnforcesCanonicalForm(string? value, bool expected)
        => Assert.Equal(expected, Ulid.IsValid(value));

    [Fact]
    public void NewPrefixedProducesContractForm()
    {
        var id = Ulid.NewPrefixed("raw", DateTimeOffset.UnixEpoch);

        Assert.StartsWith("raw_", id, StringComparison.Ordinal);
        // Тот же шаблон, что в shared/schemas/raw-event.schema.json и в
        // domain/validation.py Core (PREFIXED_ID_RE).
        Assert.Matches("^[a-z][a-z0-9_]*_[0-9A-HJKMNP-TV-Z]{26}$", id);
    }
}
