"""Расшифровка ссылок happ://crypt…/ в URL подписки. Чистый Python — работает на роутере.

Ключи в код не входят: их передаёт вызывающий (см. happ_keys) в виде
{"crypt": [base64 DER для crypt, crypt2, crypt3, crypt4], "crypt5": {маркер: base64 DER}}.

Форматы:
- crypt, crypt2…crypt4 — base64 от последовательности RSA-блоков (PKCS#1 v1.5, тип 2);
  блоки расшифровываются по очереди закрытым ключом своей версии и склеиваются.
- crypt5 — в каждой четвёрке байт полезной нагрузки половинки переставлены местами;
  после обратной перестановки первые и последние 4 байта образуют маркер ключа, между
  ними — тело: nonce (12 байт), [метка 2 байта + соль 8 байт], длина сегмента цифрами,
  разделитель, сегмент с шифртекстом URL (base64), затем RSA-блок (base64). RSA-блок
  даёт ключ ChaCha20-Poly1305 (base64 с попарно переставленными байтами; при наличии
  соли — XOR с ней), ChaCha20-Poly1305 даёт URL — тоже base64 с перестановкой пар.
"""

from __future__ import annotations

import base64
import binascii

from . import _chacha

VERSIONS = {"crypt": 0, "crypt2": 1, "crypt3": 2, "crypt4": 3}


class HappError(ValueError):
    """Ссылка не расшифровывается."""


class UnknownKey(HappError):
    """Для ссылки нет ключа — набор ключей устарел или неполон."""


# --- DER и RSA ------------------------------------------------------------------------

def _tlv(der: bytes, pos: int):
    """(тег, начало значения, конец значения) элемента DER с позиции pos."""
    tag, size = der[pos], der[pos + 1]
    pos += 2
    if size & 0x80:
        count = size & 0x7F
        size = int.from_bytes(der[pos:pos + count], "big")
        pos += count
    return tag, pos, pos + size


def _children(der: bytes, start: int, end: int):
    while start < end:
        tag, value, stop = _tlv(der, start)
        yield tag, value, stop
        start = stop


def rsa_private_key(der: bytes):
    """(n, d) из RSAPrivateKey (PKCS#1) или PrivateKeyInfo (PKCS#8)."""
    try:
        tag, start, end = _tlv(der, 0)
        items = list(_children(der, start, end)) if tag == 0x30 else []
        if len(items) == 3 and items[2][0] == 0x04:       # PKCS#8: version, algid, key
            return rsa_private_key(der[items[2][1]:items[2][2]])
        ints = [int.from_bytes(der[s:e], "big") for t, s, e in items if t == 0x02]
        if len(ints) < 4:
            raise HappError("ключ не RSA")
        return ints[1], ints[3]                           # version, n, e, d, …
    except IndexError as exc:
        raise HappError("испорченный ключ") from exc


def _unpad(block: bytes) -> bytes:
    """PKCS#1 v1.5, тип 2: 00 02 PS(≥8 ненулевых) 00 сообщение."""
    sep = block.find(b"\x00", 2)
    if block[:2] != b"\x00\x02" or sep < 10:
        raise HappError("неверная набивка RSA")
    return block[sep + 1:]


class RsaKey:
    def __init__(self, b64_der: str):
        self.n, self.d = rsa_private_key(base64.b64decode(b64_der))
        self.size = (self.n.bit_length() + 7) // 8

    def decrypt(self, data: bytes) -> bytes:
        if not data or len(data) % self.size:
            raise HappError("длина шифртекста не кратна блоку RSA")
        out = b""
        for i in range(0, len(data), self.size):
            value = pow(int.from_bytes(data[i:i + self.size], "big"), self.d, self.n)
            out += _unpad(value.to_bytes(self.size, "big"))
        return out


# --- Вспомогательные преобразования ----------------------------------------------------

def _b64(data) -> bytes:
    """base64 обоих алфавитов, пробелы и отсутствие паддинга допустимы."""
    if isinstance(data, bytes):
        data = data.decode("latin-1")
    clean = "".join(data.split()).replace("-", "+").replace("_", "/").rstrip("=")
    try:
        return base64.b64decode(clean + "=" * (-len(clean) % 4), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HappError("не base64") from exc


def _swap_pairs(data: bytes) -> bytes:
    """ab cd ef → ba dc fe (непарный последний байт остаётся)."""
    out = bytearray(data)
    even = len(data) - len(data) % 2
    out[0:even:2], out[1:even:2] = data[1:even:2], data[0:even:2]
    return bytes(out)


def _swap_halves(data: bytes) -> bytes:
    """В каждой полной четвёрке: abcd → cdab."""
    out = bytearray()
    full = len(data) - len(data) % 4
    for i in range(0, full, 4):
        out += data[i + 2:i + 4] + data[i:i + 2]
    return bytes(out + data[full:])


# --- crypt5 ---------------------------------------------------------------------------

def _crypt5_layout(body: bytes, salted: bool):
    """(nonce, соль|None, шифртекст URL, RSA-блок) из тела crypt5."""
    nonce, pos = body[:12], 12
    salt = None
    if salted:
        salt, pos = body[14:22], 22
        if len(salt) != 8:
            raise HappError("crypt5: нет соли")
    digits = pos
    while digits < len(body) and 0x30 <= body[digits] <= 0x39:
        digits += 1
    if digits == pos:
        raise HappError("crypt5: нет длины сегмента")
    size = int(body[pos:digits])
    segment = body[digits + 1:digits + 1 + size]
    if len(segment) != size:
        raise HappError("crypt5: сегмент обрезан")
    return nonce, salt, segment, body[digits + 1 + size:]


def _crypt5(payload: str, keys: dict) -> str:
    data = _swap_halves(payload.encode("utf-8"))
    if len(data) < 21:
        raise HappError("crypt5: слишком короткая ссылка")
    marker = (data[:4] + data[-4:]).decode("latin-1")
    if marker not in keys:
        raise UnknownKey(f"crypt5: нет ключа {marker}")
    key, body = RsaKey(keys[marker]), data[4:-4]
    # В солёном варианте после nonce идёт метка (не цифра), в прежнем — сразу длина.
    order = (False, True) if 0x30 <= body[12] <= 0x39 else (True, False)
    error = None
    for salted in order:
        try:
            nonce, salt, segment, rsa_block = _crypt5_layout(body, salted)
            secret = _b64(_swap_pairs(key.decrypt(_b64(rsa_block))))
            if len(secret) != 32:
                raise HappError("crypt5: ключ ChaCha не 32 байта")
            if salt:
                secret = bytes(b ^ salt[i % 8] for i, b in enumerate(secret))
            plain = _chacha.decrypt(secret, nonce, _b64(segment))
            return _b64(_swap_pairs(plain)).decode("utf-8")
        except (HappError, ValueError, _chacha.InvalidTag) as exc:
            error = exc
    raise HappError(f"crypt5: не расшифровано ({error})")


# --- Вход -----------------------------------------------------------------------------

def version_of(link: str) -> str:
    """'crypt'…'crypt5' или ''."""
    rest = link.strip()
    if not rest.startswith("happ://"):
        return ""
    name = rest[len("happ://"):].split("/", 1)[0]
    return name if name in VERSIONS or name == "crypt5" else ""


def decode(link: str, keys: dict) -> str:
    """happ://cryptN/… → URL подписки. keys — {"crypt": [...], "crypt5": {...}}."""
    version = version_of(link)
    if not version:
        raise HappError("не ссылка happ://crypt…")
    payload = link.strip().split("/", 3)[3]
    if version == "crypt5":
        return _crypt5(payload, keys.get("crypt5") or {})
    index = VERSIONS[version]
    pkcs1 = keys.get("crypt") or []
    if index >= len(pkcs1) or not pkcs1[index]:
        raise UnknownKey(f"{version}: нет ключа")
    return RsaKey(pkcs1[index]).decrypt(_b64(payload)).decode("utf-8")
