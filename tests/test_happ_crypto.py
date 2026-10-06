"""Расшифровка happ://crypt… и ключи Happ — на собственных тестовых RSA-ключах.

Ссылки собираются здесь же, обратными преобразованиями формата (см. happ_crypto).
Настоящие ключи Happ в репозиторий не входят.
"""

import base64
import json
import os
import random
import sys
import unittest
from unittest.mock import Mock, patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from nodes_fetch import _chacha, happ, happ_crypto, happ_keys  # noqa: E402
from tests.helpers import temp_dir  # noqa: E402

RNG = random.Random(20261006)


def _prime(bits):
    while True:
        n = RNG.getrandbits(bits) | (1 << bits - 1) | 1
        if all(pow(a, n - 1, n) == 1 for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)):
            return n


def _der_int(value):
    body = value.to_bytes(value.bit_length() // 8 + 1, "big")
    return _der(0x02, body)


def _der(tag, body):
    size = len(body)
    head = bytes([size]) if size < 0x80 else bytes([0x80 | 2]) + size.to_bytes(2, "big")
    return bytes([tag]) + head + body


def make_key(bits=768):
    """(n, e, base64 PKCS#1, base64 PKCS#8)."""
    e = 65537
    while True:
        p, q = _prime(bits // 2), _prime(bits // 2)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            break
    n, d = p * q, pow(e, -1, phi)
    pkcs1 = _der(0x30, b"".join(_der_int(x) for x in (0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p))))
    algid = _der(0x30, _der(0x06, bytes.fromhex("2a864886f70d010101")) + b"\x05\x00")
    pkcs8 = _der(0x30, _der_int(0) + algid + _der(0x04, pkcs1))
    return n, e, base64.b64encode(pkcs1).decode(), base64.b64encode(pkcs8).decode()


def rsa_encrypt(n, e, message):
    size = (n.bit_length() + 7) // 8
    pad = bytes(RNG.randrange(1, 256) for _ in range(size - 3 - len(message)))
    block = b"\x00\x02" + pad + b"\x00" + message
    return pow(int.from_bytes(block, "big"), e, n).to_bytes(size, "big")


def swap_pairs(data):
    return happ_crypto._swap_pairs(data)


def crypt5_link(n, e, marker, url, salted):
    secret = bytes(RNG.randrange(256) for _ in range(32))
    nonce = bytes(RNG.randrange(48, 58) for _ in range(12))        # цифры: проверка раскладки
    salt = b"S4lt8byt" if salted else None
    chacha_key = bytes(b ^ salt[i % 8] for i, b in enumerate(secret)) if salted else secret
    inner = swap_pairs(base64.b64encode(url.encode()))
    segment = base64.b64encode(_chacha.encrypt(chacha_key, nonce, inner))
    rsa_block = base64.b64encode(rsa_encrypt(n, e, swap_pairs(base64.b64encode(secret))))
    head = nonce + (b"t1" + salt if salted else b"")
    body = head + str(len(segment)).encode() + b"|" + segment + rsa_block
    data = marker[:4].encode() + body + marker[4:].encode()
    return "happ://crypt5/" + happ_crypto._swap_halves(data).decode()


class HappCryptoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.k1 = make_key(1024)
        cls.k5 = make_key(1024)
        cls.keys = {"crypt": ["", "", "", cls.k1[2]], "crypt5": {"abcdwxyz": cls.k5[3]}}

    def test_crypt4_blocks(self):
        n, e = self.k1[:2]
        url = "https://panel.example/sub/" + "x" * 150             # два RSA-блока
        chunks = [url[:100].encode(), url[100:].encode()]
        payload = base64.urlsafe_b64encode(b"".join(rsa_encrypt(n, e, c) for c in chunks)).decode()
        self.assertEqual(happ_crypto.decode("happ://crypt4/" + payload.rstrip("="), self.keys), url)

    def test_crypt5_both_layouts(self):
        n, e = self.k5[:2]
        for salted in (False, True):
            with self.subTest(salted=salted):
                link = crypt5_link(n, e, "abcdwxyz", "https://panel.example/sub/token", salted)
                self.assertEqual(happ_crypto.decode(link, self.keys), "https://panel.example/sub/token")

    def test_unknown_key(self):
        n, e = self.k5[:2]
        with self.assertRaises(happ_crypto.UnknownKey):
            happ_crypto.decode(crypt5_link(n, e, "zzzzzzzz", "https://x.example/", False), self.keys)
        with self.assertRaises(happ_crypto.UnknownKey):
            happ_crypto.decode("happ://crypt2/AAAA", self.keys)

    def test_tampered(self):
        n, e = self.k5[:2]
        link = crypt5_link(n, e, "abcdwxyz", "https://panel.example/sub/token", True)
        payload = link[len("happ://crypt5/"):]
        broken = payload[:40] + ("A" if payload[40] != "A" else "B") + payload[41:]
        with self.assertRaises(happ_crypto.HappError):
            happ_crypto.decode("happ://crypt5/" + broken, self.keys)


class HappKeysTest(unittest.TestCase):
    KEYS_RS = ('// crypt..crypt4\npub const PKCS1_KEYS: [&str; 4] = [\n'
               + "".join(f'    "{c * 120}",\n' for c in "ABCD") + "];\n"
               'pub const CRYPT5_PKCS8: &[(&str, &str)] = &[\n'
               f'    ("abcdwxyz", "{"E" * 120}"),\n    ("qwertyui", "{"F" * 120}"),\n];\n'
               'pub const V2_KEYS: &[(&str, &str)] = &[\n    ("x", "y"),\n];\n')

    def test_parse_keys_rs_and_json(self):
        keys = happ_keys.parse(self.KEYS_RS)
        self.assertEqual([k[0] for k in keys["crypt"]], list("ABCD"))
        self.assertEqual(sorted(keys["crypt5"]), ["abcdwxyz", "qwertyui"])
        self.assertEqual(happ_keys.parse(json.dumps(keys)), keys)
        with self.assertRaises(happ_keys.KeysError):
            happ_keys.parse("<html>not found</html>")

    def test_default_path_next_to_device(self):
        path = happ_keys.default_path(os.path.join("etc", "nodes-tester", "config-main", "providers.json"))
        self.assertEqual(path, os.path.abspath(os.path.join("etc", "nodes-tester", "happ-keys.json")))

    def test_download_and_refresh_policy(self):
        tmp = temp_dir()
        cache = os.path.join(tmp, "happ-keys.json")
        k5 = make_key(1024)
        rs = self.KEYS_RS.replace("E" * 120, k5[3])
        link = crypt5_link(k5[0], k5[1], "abcdwxyz", "https://panel.example/s", False)
        with patch("nodes_fetch.happ_keys.requests.get", return_value=Mock(text=rs)) as get:
            self.assertEqual(happ.resolve_url(link, cache, "https://keys.example/keys.rs"),
                             "https://panel.example/s")                  # кэша нет → скачать
            self.assertEqual(get.call_args.args[0], "https://keys.example/keys.rs")
            self.assertTrue(os.path.exists(cache))
            happ.resolve_url(link, cache, "https://keys.example/keys.rs")
            self.assertEqual(get.call_count, 1)                          # из кэша
            unknown = crypt5_link(k5[0], k5[1], "zzzzzzzz", "https://x.example/", False)
            with self.assertRaises(happ_crypto.UnknownKey):
                happ.resolve_url(unknown, cache, "https://keys.example/keys.rs")
            self.assertEqual(get.call_count, 1)                          # кэш свежий — не качаем
            os.utime(cache, (0, 0))
            with self.assertRaises(happ_crypto.UnknownKey):
                happ.resolve_url(unknown, cache, "https://keys.example/keys.rs")
            self.assertEqual(get.call_count, 2)                          # кэш старый — обновили


if __name__ == "__main__":
    unittest.main()
