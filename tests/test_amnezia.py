"""Amnezia Premium: шифрование шлюза на чистом Python, клиент шлюза и замена стран по рейтингу."""

import base64
import json
import os
import shutil
import tempfile
import unittest
import zlib
from unittest import mock

from nodes_fetch import _aes, amnezia, sources
from nodes_tester import amnezia_slots
from nodes_tester.config import ConfigError, parse_config

# Тестовый ключ RSA-2048 (только для тестов).
PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAqbUU3r+xSQAZBuM0sOSW
AcSdLR5YtMPZQGl/akilivi1xkib0gpIUVSmXsaQOHtkfkvMRFoVyBHZUX96OI3V
ilwqy8xbZSKrWws4RkQrf/nc0UOcfRhpRLj7Mpr6gX7G82p8lmGMOPjrJ8WkplTU
va6VTrPVAlzCkIbDYUy0COVoaZL/amjokp9XwDHvfibK2aNLwizds6fYOfCzmgP3
gs8UmtB7+5nX8obt5bNis79/L+6AwvEDqNd4U+PEPwZY4UhLUxwdwpaRfZeouCuC
bFL0EfyqplGTCdmd4f7CqDeG74rvn3/SZBtiaD1JbsGm3YvlwWMlGzmD/fM7tb5O
GQIDAQAB
-----END PUBLIC KEY-----
"""
D = int("2df7a20d09c04405364232d5a95a69355663ba7e5f49ab48d3d51f9192d6e03328cd6a0a66d4a92cfa704bf89dc4e6cb5c18"
        "3cde80c62898891435a2cde1c127067b0313b9e7c581c399e0e6cb7d3adf5b8c0533c48091b6ef21ff5d921ff44e804d662c"
        "b7846b442531f32b7b703be59afdcf410f9fe1af43c574f38c7ab173bc9e5f636d64d604f56542829ae730e2b0173ca7977f"
        "8152608b4ba02185b928aa82c77e99b5a2ff393d3c7c3b9562a82c3b352d1acd0c4f4cdb1b07575142255e1b001995945682"
        "b5dd2d98e13ce964cdfde896107a64c5145b0073be7467736f44b9b3949a69f96a3987164b1815c5475bd7d52ece396af638"
        "a00d7487f3af", 16)
N, E = amnezia.rsa_public_key(PEM)


def tmp(test):
    path = tempfile.mkdtemp(prefix="ntest-")
    test.addCleanup(shutil.rmtree, path, True)
    return path


def link(api_key="secret-key"):
    body = json.dumps({"api_config": {"service_type": "amnezia-premium", "user_country_code": "ru"},
                       "auth_data": {"api_key": api_key}}).encode()
    packed = len(body).to_bytes(4, "big") + zlib.compress(body)
    return "vpn://" + base64.urlsafe_b64encode(packed).decode().rstrip("=")


def rsa_decrypt(data):
    block = pow(int.from_bytes(data, "big"), D, N).to_bytes(256, "big")
    assert block[:2] == b"\x00\x02" and 0 not in block[2:10]
    return block[block.index(0, 2) + 1:]


class CryptoTest(unittest.TestCase):
    def test_aes_256_cbc_nist_vector(self):
        # NIST SP 800-38A, F.2.5 (CBC-AES256), первый блок; второй — дополнение PKCS#7.
        key = bytes.fromhex("603deb1015ca71be2b73aef0857d77811f352c073b6108d72d9810a30914dff4")
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plain = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        out = _aes.encrypt_cbc(plain, key, iv + bytes(16))
        self.assertEqual(out[:16].hex(), "f58c4c04d6e5f1ba779eabfb5f7bfbd6")
        self.assertEqual(_aes.decrypt_cbc(out, key, iv), plain)

    def test_aes_round_trip_and_bad_padding(self):
        key, iv = os.urandom(32), os.urandom(32)
        for size in (0, 1, 15, 16, 17, 1000):
            data = os.urandom(size)
            self.assertEqual(_aes.decrypt_cbc(_aes.encrypt_cbc(data, key, iv), key, iv), data)
        with self.assertRaises(ValueError):
            _aes.decrypt_cbc(_aes.encrypt_cbc(b"x", key, iv), os.urandom(32), iv)

    def test_x25519_rfc7748(self):
        alice = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
        self.assertEqual(amnezia.x25519(alice, (9).to_bytes(32, "little")).hex(),
                         "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
        private, public = amnezia.wireguard_keypair()
        self.assertEqual(amnezia.x25519(base64.b64decode(private), (9).to_bytes(32, "little")),
                         base64.b64decode(public))

    def test_rsa_pkcs1_v15(self):
        self.assertEqual(E, 65537)
        self.assertEqual(rsa_decrypt(amnezia.rsa_encrypt((N, E), b"hello")), b"hello")
        with self.assertRaises(amnezia.AmneziaError):
            amnezia.rsa_public_key("-----BEGIN PUBLIC KEY-----\nAAAA\n-----END PUBLIC KEY-----")

    def test_decode_link(self):
        self.assertEqual(amnezia.decode_link(link())["auth_data"]["api_key"], "secret-key")
        with self.assertRaises(amnezia.AmneziaError):
            amnezia.decode_link("vpn://bm90LWEtbGluaw")


class FakeServer:
    """Шлюз: расшифровывает запрос тестовым закрытым ключом и отвечает шифром запроса."""

    def __init__(self, replies):
        self.replies = replies          # метод → dict ответа
        self.calls = []

    def __call__(self, url, **kwargs):
        body = kwargs["json"]
        keys = json.loads(rsa_decrypt(base64.b64decode(body["key_payload"])))
        key, iv = base64.b64decode(keys["aes_key"]), base64.b64decode(keys["aes_iv"])
        payload = json.loads(_aes.decrypt_cbc(base64.b64decode(body["api_payload"]), key, iv))
        method = url.rsplit("/", 1)[1]
        self.calls.append((method, payload))
        reply = self.replies[method]
        reply = reply(payload) if callable(reply) else reply
        return mock.Mock(status_code=200,
                         content=_aes.encrypt_cbc(json.dumps(reply).encode(), key, iv))


class GatewayTest(unittest.TestCase):
    def test_requests_and_replies(self):
        conf = "[Interface]\nPrivateKey = $WIREGUARD_CLIENT_PRIVATE_KEY\n"
        server = FakeServer({"account_info": {"http_status": 200, "max_device_count": 7},
                             "native_config": {"http_status": 200, "config": conf},
                             "revoke_native_config": {"http_status": 404, "message": "not found"}})
        with mock.patch("requests.post", server):
            gateway = amnezia.Gateway(link(), PEM)
            self.assertEqual(gateway.account_info()["max_device_count"], 7)
            issued = gateway.issue("de")
            gateway.revoke("de")                       # 404 — файла уже нет, не ошибка
        method, payload = server.calls[0]
        self.assertEqual(payload["auth_data"], {"api_key": "secret-key"})
        self.assertEqual((payload["service_type"], payload["user_country_code"]), ("amnezia-premium", "ru"))
        method, payload = server.calls[1]
        self.assertEqual((payload["server_country_code"], payload["service_protocol"]), ("de", "awg"))
        private = issued.split("= ", 1)[1].strip()
        self.assertEqual(amnezia.x25519(base64.b64decode(private), (9).to_bytes(32, "little")),
                         base64.b64decode(payload["public_key"]))

    def test_gateway_error(self):
        server = FakeServer({"account_info": {"http_status": 409, "message": "limit"}})
        with mock.patch("requests.post", server), self.assertRaises(amnezia.AmneziaError) as err:
            amnezia.Gateway(link(), PEM).account_info()
        self.assertEqual(err.exception.status, 409)


def conf(server):
    return (f"[Interface]\nPrivateKey = cHJpdmF0ZQ==\nAddress = 10.8.1.2/32\n"
            f"[Peer]\nPublicKey = cHVibGlj\nEndpoint = {server}:51820\n")


class FakeGateway:
    countries = ["ru", "de", "nl", "fi", "se", "dk", "at"]

    def __init__(self, issued=(), max_devices=4, active=None, worker=None):
        self.issued = {cc: {"server_country_code": cc, "source_type": "country_config",
                            "last_downloaded": "2026-10-06T00:00:00Z",
                            "worker_last_updated": (worker or {}).get(cc, "2026-10-01T00:00:00Z")}
                       for cc in issued}
        self.max_devices = max_devices
        self.active = len(self.issued) if active is None else active
        self.log = []

    def __call__(self, url, key, proxies=None):
        return self

    def account_info(self):
        return {"max_device_count": self.max_devices, "active_device_count": self.active,
                "issued_configs": list(self.issued.values()),
                "available_countries": [{"server_country_code": cc, "available_protocols": ["awg", "vless"]}
                                        for cc in self.countries]}

    def issue(self, cc):
        self.log.append(("issue", cc))
        return conf(f"{cc}.example")

    def revoke(self, cc):
        self.log.append(("revoke", cc))


class SlotsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tmp(self)
        with open(os.path.join(self.dir, "gw.pem"), "w") as fh:
            fh.write(PEM)
        self.now = 1_000_000.0

    def sub(self, gateway, files=(), **extra):
        folder = os.path.join(self.dir, "awg")
        os.makedirs(folder, exist_ok=True)
        for cc in files:
            with open(os.path.join(folder, f"{cc}.conf"), "w") as fh:
                fh.write(conf(f"{cc}.example"))
        cfg = {"tag": "AWG", "url": link(), "path": "awg", "gateway_key": "gw.pem", **extra}
        patch = mock.patch.object(amnezia_slots, "Gateway", gateway)
        patch.start()
        self.addCleanup(patch.stop)
        return amnezia_slots.Subscription(cfg, self.dir, log=lambda m: None, clock=lambda: self.now)

    def files(self):
        return sorted(n[:-5] for n in os.listdir(os.path.join(self.dir, "awg")) if n.endswith(".conf"))

    def state(self):
        with open(os.path.join(self.dir, "awg", "state.json")) as fh:
            return json.load(fh)

    def test_fills_free_slots_with_random_new_countries(self):
        gateway = FakeGateway(issued=["de"], max_devices=4)
        sub = self.sub(gateway, files=["de"], slots=3)
        sub.run({})
        files = self.files()
        self.assertEqual(len(files), 3)                  # slots=3 из 4 слотов подписки
        self.assertIn("de", files)
        self.assertNotIn("ru", files)                    # exclude_countries по умолчанию
        self.assertEqual(sorted(self.state()["issued"]), sorted(set(files) - {"de"}))
        self.assertEqual(oct(os.stat(os.path.join(self.dir, "awg", files[-1] + ".conf")).st_mode & 0o777), "0o600")

    def test_does_not_take_countries_issued_elsewhere(self):
        gateway = FakeGateway(issued=["de", "nl", "fi", "se", "dk"], max_devices=7)
        self.sub(gateway, files=["de"]).run({})
        self.assertEqual(self.files(), ["at", "de"])     # nl, fi, se, dk выпущены не здесь

    def test_low_rating_blocks_country_after_hold(self):
        gateway = FakeGateway(issued=["de", "nl"], max_devices=2)
        sub = self.sub(gateway, files=["de", "nl"])
        ratings = {"de.example": 10.0, "nl.example": 80.0}
        sub.run(ratings)
        self.assertEqual(gateway.log, [])                # ниже порога, но меньше HOLD
        self.now += amnezia_slots.HOLD
        sub.run(ratings)
        self.assertEqual(gateway.log[0], ("revoke", "de"))
        self.assertEqual(gateway.log[1][0], "issue")
        files = self.files()
        self.assertNotIn("de", files)
        self.assertEqual(len(files), 2)
        self.assertIn("de", self.state()["blocked"])

    def test_recovered_rating_resets_hold(self):
        gateway = FakeGateway(issued=["de", "nl"], max_devices=2)
        sub = self.sub(gateway, files=["de", "nl"])
        sub.run({"de.example": 10.0, "nl.example": 80.0})
        self.now += amnezia_slots.HOLD / 2
        sub.run({"de.example": 60.0, "nl.example": 80.0})
        self.now += amnezia_slots.HOLD
        sub.run({"de.example": 10.0, "nl.example": 80.0})
        self.assertEqual(gateway.log, [])

    def test_all_countries_low_means_not_the_countries(self):
        gateway = FakeGateway(issued=["de", "nl"], max_devices=2)
        sub = self.sub(gateway, files=["de", "nl"])
        sub.run({"de.example": 0.0, "nl.example": 5.0})
        self.now += amnezia_slots.HOLD
        sub.run({"de.example": 0.0, "nl.example": 5.0})
        self.assertEqual(gateway.log, [])
        self.assertEqual(self.files(), ["de", "nl"])

    def write_state(self, state):
        os.makedirs(os.path.join(self.dir, "awg"), exist_ok=True)
        with open(os.path.join(self.dir, "awg", "state.json"), "w") as fh:
            json.dump(state, fh)

    def test_blocked_countries_wait_while_others_remain(self):
        gateway = FakeGateway(max_devices=1)
        gateway.countries = ["de", "nl"]
        sub = self.sub(gateway)
        self.write_state({"blocked": {"nl": "2026-10-01"}})
        sub.run({})
        self.assertEqual(self.files(), ["de"])

    def test_no_countries_left_retries_the_oldest_blocked(self):
        gateway = FakeGateway(max_devices=2)
        gateway.countries = ["de", "nl", "fi"]
        sub = self.sub(gateway)
        self.write_state({"blocked": {"de": "2026-10-05", "nl": "2026-10-01", "fi": "2026-10-03"}})
        sub.run({})
        self.assertEqual(self.files(), ["fi", "nl"])
        self.assertEqual(self.state()["blocked"], {"de": "2026-10-05"})

    def test_country_blocked_now_is_not_retried_at_once(self):
        gateway = FakeGateway(issued=["nl"], max_devices=2)   # de сервер уже не числит
        gateway.countries = ["de", "nl"]
        sub = self.sub(gateway, files=["de", "nl"])
        ratings = {"de.example": 10.0, "nl.example": 80.0}
        sub.run(ratings)
        self.now += amnezia_slots.HOLD
        sub.run(ratings)
        self.assertEqual(gateway.log, [("revoke", "de")])
        self.assertEqual(self.files(), ["nl"])

    def test_reissues_when_server_was_updated(self):
        gateway = FakeGateway(issued=["de"], max_devices=1, worker={"de": "2026-10-10T00:00:00Z"})
        self.sub(gateway, files=["de"]).run({})
        self.assertEqual(gateway.log, [("issue", "de")])


class ConfigTest(unittest.TestCase):
    def test_amnezia_subscription_fields(self):
        good = {"tag": "AWG", "type": "amnezia", "url": "vpn://x", "path": "awg", "gateway_key": "gw.pem",
                "slots": 5, "min_rating": 40, "exclude_countries": ["ru"]}
        parse_config({"subscriptions": [good]})
        for broken in ({"url": "https://x"}, {"path": ""}, {"gateway_key": None}, {"slots": 0},
                       {"min_rating": 101}, {"exclude_countries": "ru"}):
            with self.subTest(broken=broken), self.assertRaises(ConfigError):
                parse_config({"subscriptions": [{**good, **broken}]})

    def test_fetch_reads_the_folder(self):
        folder = tmp(self)
        with open(os.path.join(folder, "de.conf"), "w") as fh:
            fh.write(conf("de.example"))
        with open(os.path.join(folder, "state.json"), "w") as fh:
            fh.write("{}")
        sub = {"tag": "AWG", "type": "amnezia", "path": folder}
        self.assertEqual(sources.kind_of(sub), "amnezia")
        nodes = sources.fetch_subscription(sub, sources.Context(base_dir=folder, log=lambda m: None))
        self.assertEqual([n["peers"][0]["address"] for n in nodes], ["de.example"])


if __name__ == "__main__":
    unittest.main()
