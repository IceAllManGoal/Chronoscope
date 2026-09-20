using System.Buffers.Binary;
using System.Security.Cryptography;

namespace Chronoscope.Agent.Identity;

/// <summary>
/// ULID в канонической форме (§17, docs/EVENT_MODEL.md §2.1).
///
/// Реализован здесь, а не взят зависимостью, по той же причине, что и в Core:
/// формат прост и стабилен (48 бит времени + 80 бит случайности в Crockford
/// base32), а нужен он на обеих сторонах контракта. Core валидирует
/// идентификаторы строго как ULID — <c>PREFIXED_ID_RE</c> в его
/// <c>domain/validation.py</c> принимает только 26 заглавных символов
/// Crockford base32, — поэтому UUIDv7, который схема тоже допускает, здесь не
/// подойдёт: Core его отвергнет.
///
/// Каноническая форма означает: 26 символов, только заглавные, алфавит без
/// <c>I</c>, <c>L</c>, <c>O</c> и <c>U</c>. Кодирование — big-endian по 5 бит,
/// то есть последний символ несёт младшие 5 бит.
/// </summary>
public static class Ulid
{
    /// <summary>Crockford base32 без I, L, O и U: их слишком легко спутать с 1 и 0.</summary>
    public const string Alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";

    public const int Length = 26;

    private const int TimeChars = 10;
    private const int RandomChars = 16;
    private const int RandomBytes = 10; // 80 бит
    private const long MaxTimestampMs = (1L << 48) - 1;

    /// <summary>ULID для указанного момента со случайной частью из CSPRNG.</summary>
    public static string New(DateTimeOffset at)
    {
        Span<byte> random = stackalloc byte[RandomBytes];
        RandomNumberGenerator.Fill(random);
        return FromParts(ToMilliseconds(at), random);
    }

    /// <summary>Новый идентификатор вида <c>&lt;prefix&gt;_&lt;ULID&gt;</c> (§17).</summary>
    public static string NewPrefixed(string prefix, DateTimeOffset at) => $"{prefix}_{New(at)}";

    /// <summary>ULID из готовых частей. Одни и те же входные данные дают один и тот же результат.</summary>
    public static string FromParts(long timestampMs, ReadOnlySpan<byte> random80Bits)
    {
        if (timestampMs < 0 || timestampMs > MaxTimestampMs)
        {
            throw new ArgumentOutOfRangeException(
                nameof(timestampMs), timestampMs, $"timestamp вне диапазона ULID: 0..{MaxTimestampMs}");
        }

        if (random80Bits.Length != RandomBytes)
        {
            throw new ArgumentException(
                $"случайная часть ULID — ровно {RandomBytes} байт (80 бит), получено {random80Bits.Length}",
                nameof(random80Bits));
        }

        // 80 бит = старшие 64 бита из первых 8 байт и младшие 16 из последних 2.
        //
        // Склейка идёт сдвигом, а не `new UInt128(high, low)`: вторым аргументом
        // конструктор ждёт младшие 64 бита, а не 16, поэтому такая запись
        // оставила бы биты 16..63 нулями — случайная часть превратилась бы в
        // «несколько значащих символов, дыра, несколько значащих символов».
        // Ошибка не ломает форму идентификатора, поэтому её ловит только сверка
        // с реализацией Core на конкретных значениях (см. UlidTests).
        var high = BinaryPrimitives.ReadUInt64BigEndian(random80Bits[..8]);
        var low = BinaryPrimitives.ReadUInt16BigEndian(random80Bits[8..]);
        var random = ((UInt128)high << 16) | low;

        return Encode((ulong)timestampMs, TimeChars) + Encode(random, RandomChars);
    }

    /// <summary>Проверить каноническую форму: длина, алфавит, заглавные.</summary>
    public static bool IsValid(string? value)
    {
        if (value is null || value.Length != Length)
        {
            return false;
        }

        foreach (var symbol in value)
        {
            if (!Alphabet.Contains(symbol, StringComparison.Ordinal))
            {
                return false;
            }
        }

        return true;
    }

    /// <summary>Миллисекунды эпохи. UTC-смещение учитывается, локальное время — нет.</summary>
    public static long ToMilliseconds(DateTimeOffset at) => at.ToUnixTimeMilliseconds();

    private static string Encode(ulong value, int length)
    {
        Span<char> buffer = stackalloc char[length];
        for (var index = length - 1; index >= 0; index--)
        {
            buffer[index] = Alphabet[(int)(value & 0x1F)];
            value >>= 5;
        }

        return new string(buffer);
    }

    private static string Encode(UInt128 value, int length)
    {
        Span<char> buffer = stackalloc char[length];
        for (var index = length - 1; index >= 0; index--)
        {
            buffer[index] = Alphabet[(int)(value & 0x1F)];
            value >>= 5;
        }

        return new string(buffer);
    }
}
