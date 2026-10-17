"""AES-256-CBC с дополнением PKCS#7 (FIPS-197). Чистый Python — работает на роутере.

Объёмы шлюза Amnezia — килобайты, скорости хватает.
"""

from __future__ import annotations


def _xtime(b: int) -> int:
    b <<= 1
    return b ^ 0x11B if b & 0x100 else b


def _mul(a: int, b: int) -> int:
    out = 0
    while b:
        if b & 1:
            out ^= a
        a, b = _xtime(a), b >> 1
    return out


def _sbox() -> list[int]:
    inv = [0] * 256
    for a in range(1, 256):
        inv[a] = next(b for b in range(1, 256) if _mul(a, b) == 1)
    box = []
    for a in range(256):
        x = inv[a]
        box.append(x ^ (((x << 1) | (x >> 7)) & 0xFF) ^ (((x << 2) | (x >> 6)) & 0xFF)
                   ^ (((x << 3) | (x >> 5)) & 0xFF) ^ (((x << 4) | (x >> 4)) & 0xFF) ^ 0x63)
    return box


SBOX = _sbox()
INV_SBOX = [0] * 256
for _i, _v in enumerate(SBOX):
    INV_SBOX[_v] = _i
# Строки ShiftRows: байт i состояния (по столбцам) берётся из позиции _SHIFT[i].
_SHIFT = [(i + 4 * (i % 4)) % 16 for i in range(16)]
_UNSHIFT = [_SHIFT.index(i) for i in range(16)]
# Таблицы умножения на коэффициенты MixColumns и обратного к нему.
_MUL = {m: [_mul(m, b) for b in range(256)] for m in (1, 2, 3, 9, 11, 13, 14)}


def _expand(key: bytes) -> list[list[int]]:
    """Ключи раундов AES-256: 15 по 16 байт."""
    words = [list(key[i:i + 4]) for i in range(0, 32, 4)]
    rcon = 1
    for i in range(8, 60):
        w = list(words[-1])
        if i % 8 == 0:
            w = [SBOX[b] for b in w[1:] + w[:1]]
            w[0] ^= rcon
            rcon = _xtime(rcon)
        elif i % 8 == 4:
            w = [SBOX[b] for b in w]
        words.append([a ^ b for a, b in zip(words[i - 8], w)])
    return [sum(words[r * 4:r * 4 + 4], []) for r in range(15)]


def _mix(s: list[int], m: tuple) -> list[int]:
    t0, t1, t2, t3 = (_MUL[x] for x in m)
    out = []
    for c in range(0, 16, 4):
        a, b, d, e = s[c:c + 4]
        out += [t0[a] ^ t1[b] ^ t2[d] ^ t3[e], t0[b] ^ t1[d] ^ t2[e] ^ t3[a],
                t0[d] ^ t1[e] ^ t2[a] ^ t3[b], t0[e] ^ t1[a] ^ t2[b] ^ t3[d]]
    return out


def _encrypt_block(rk, block: bytes) -> bytes:
    s = [b ^ k for b, k in zip(block, rk[0])]
    for r in range(1, 15):
        s = [SBOX[s[_SHIFT[i]]] for i in range(16)]
        if r < 14:
            s = _mix(s, (2, 3, 1, 1))
        s = [b ^ k for b, k in zip(s, rk[r])]
    return bytes(s)


def _decrypt_block(rk, block: bytes) -> bytes:
    s = [b ^ k for b, k in zip(block, rk[14])]
    for r in range(13, -1, -1):
        s = [INV_SBOX[s[_UNSHIFT[i]]] for i in range(16)]
        s = [b ^ k for b, k in zip(s, rk[r])]
        if r:
            s = _mix(s, (14, 11, 13, 9))
    return bytes(s)


def encrypt_cbc(data: bytes, key: bytes, iv: bytes) -> bytes:
    rk, prev, out = _expand(key), iv[:16], []
    pad = 16 - len(data) % 16
    data += bytes([pad]) * pad
    for i in range(0, len(data), 16):
        prev = _encrypt_block(rk, bytes(a ^ b for a, b in zip(data[i:i + 16], prev)))
        out.append(prev)
    return b"".join(out)


def decrypt_cbc(data: bytes, key: bytes, iv: bytes) -> bytes:
    if not data or len(data) % 16:
        raise ValueError("длина шифртекста не кратна блоку AES")
    rk, prev, out = _expand(key), iv[:16], []
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        out.append(bytes(a ^ b for a, b in zip(_decrypt_block(rk, block), prev)))
        prev = block
    plain = b"".join(out)
    pad = plain[-1]
    if not 0 < pad <= 16 or plain[-pad:] != bytes([pad]) * pad:
        raise ValueError("неверное дополнение PKCS#7")
    return plain[:-pad]
