"""ChaCha20-Poly1305 AEAD (RFC 8439) на чистом Python — без внешних зависимостей.

Нужен для расшифровки happ://crypt5/… (RSA отдаёт 32-байтный ChaCha-ключ, тело
подписки зашифровано ChaCha20-Poly1305). Как и happ_decode, реализация «pure
Python», чтобы работать в т.ч. на роутере (OpenWrt/Entware), где нативных крипто-
библиотек обычно нет. Скорость не критична — расшифровываем короткую подписку.

Проверено против официальных тест-векторов RFC 8439 (см. tests).
"""

from __future__ import annotations

import struct

_MASK32 = 0xFFFFFFFF


def _rotl32(v: int, c: int) -> int:
    v &= _MASK32
    return ((v << c) | (v >> (32 - c))) & _MASK32


def _quarter_round(x: list, a: int, b: int, c: int, d: int) -> None:
    x[a] = (x[a] + x[b]) & _MASK32
    x[d] = _rotl32(x[d] ^ x[a], 16)
    x[c] = (x[c] + x[d]) & _MASK32
    x[b] = _rotl32(x[b] ^ x[c], 12)
    x[a] = (x[a] + x[b]) & _MASK32
    x[d] = _rotl32(x[d] ^ x[a], 8)
    x[c] = (x[c] + x[d]) & _MASK32
    x[b] = _rotl32(x[b] ^ x[c], 7)


_CONSTANTS = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)  # "expand 32-byte k"


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    """Один 64-байтный ChaCha20-блок (20 раундов) для (key, counter, nonce)."""
    state = list(_CONSTANTS)
    state += list(struct.unpack("<8I", key))
    state.append(counter & _MASK32)
    state += list(struct.unpack("<3I", nonce))

    working = state[:]
    for _ in range(10):  # 10 двойных раундов = 20 раундов
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)
    out = [(working[i] + state[i]) & _MASK32 for i in range(16)]
    return struct.pack("<16I", *out)


def chacha20(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    """ChaCha20-шифр/расшифровка (симметрично) с заданным стартовым счётчиком."""
    out = bytearray(len(data))
    for off in range(0, len(data), 64):
        block = off // 64
        ks = _chacha20_block(key, counter + block, nonce)
        chunk = data[off:off + 64]
        for i, b in enumerate(chunk):
            out[off + i] = b ^ ks[i]
    return bytes(out)


_P = (1 << 130) - 5


def _poly1305_mac(msg: bytes, key: bytes) -> bytes:
    """Poly1305 (RFC 8439 §2.5). key — 32 байта (r || s)."""
    r = int.from_bytes(key[0:16], "little")
    r &= 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], "little")
    acc = 0
    for off in range(0, len(msg), 16):
        chunk = msg[off:off + 16]
        n = int.from_bytes(chunk + b"\x01", "little")  # добавляем старший 1-бит
        acc = (acc + n) % _P
        acc = (acc * r) % _P
    acc = (acc + s) & ((1 << 128) - 1)
    return acc.to_bytes(16, "little")


def _pad16(data: bytes) -> bytes:
    rem = len(data) % 16
    return b"\x00" * (16 - rem) if rem else b""


def _poly1305_key_gen(key: bytes, nonce: bytes) -> bytes:
    """Одноразовый ключ Poly1305 = первые 32 байта ChaCha20-блока (counter=0)."""
    return _chacha20_block(key, 0, nonce)[:32]


def _tag(aad: bytes, ciphertext: bytes, otk: bytes) -> bytes:
    mac_data = aad + _pad16(aad) + ciphertext + _pad16(ciphertext)
    mac_data += struct.pack("<Q", len(aad)) + struct.pack("<Q", len(ciphertext))
    return _poly1305_mac(mac_data, otk)


def _ct_eq(a: bytes, b: bytes) -> bool:
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= x ^ y
    return diff == 0


class InvalidTag(Exception):
    """Poly1305-тег не сошёлся — данные повреждены или ключ/nonce неверны."""


def decrypt(key: bytes, nonce: bytes, ct_and_tag: bytes, aad: bytes = b"") -> bytes:
    """AEAD-расшифровка. ct_and_tag = шифртекст || 16-байтный тег (RustCrypto layout).

    key — 32 байта, nonce — 12 байт. При несовпадении тега — InvalidTag.
    """
    if len(key) != 32:
        raise ValueError(f"ChaCha20-Poly1305: ключ должен быть 32 байта, дано {len(key)}")
    if len(nonce) != 12:
        raise ValueError(f"ChaCha20-Poly1305: nonce должен быть 12 байт, дано {len(nonce)}")
    if len(ct_and_tag) < 16:
        raise InvalidTag("шифртекст короче 16-байтного тега")
    ciphertext = ct_and_tag[:-16]
    tag = ct_and_tag[-16:]
    otk = _poly1305_key_gen(key, nonce)
    if not _ct_eq(_tag(aad, ciphertext, otk), tag):
        raise InvalidTag("Poly1305-тег не совпал")
    return chacha20(key, 1, nonce, ciphertext)  # payload шифруется с counter=1


def encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """AEAD-шифрование. Возвращает шифртекст || 16-байтный тег. Нужно только для тестов."""
    if len(key) != 32:
        raise ValueError(f"ChaCha20-Poly1305: ключ должен быть 32 байта, дано {len(key)}")
    if len(nonce) != 12:
        raise ValueError(f"ChaCha20-Poly1305: nonce должен быть 12 байт, дано {len(nonce)}")
    otk = _poly1305_key_gen(key, nonce)
    ciphertext = chacha20(key, 1, nonce, plaintext)
    return ciphertext + _tag(aad, ciphertext, otk)
