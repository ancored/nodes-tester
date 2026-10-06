"""Клиент API-сервиса sing-box 1.14 (`services[].type = "api"`) без сторонних зависимостей.

Сервис говорит gRPC; тот же порт принимает gRPC-Web поверх HTTP/1.1, поэтому хватает
`http.client`: пакетов grpc/h2/protobuf в OpenWrt нет. Кадр gRPC-Web — флаг (1 байт) +
длина (4 байта, big-endian) + сообщение protobuf; флаг 0x80 — кадр трейлеров
(`grpc-status`, `grpc-message`). Кодек protobuf здесь минимальный: только поля
сообщений из `daemon/started_service.proto` (тег v1.14.0), которые нам нужны.

Командная строка (для скриптов роутера):

    python3 -m nodes_common.box_api health --config /etc/sing-box/config.json \
        --group global-auto-out [--wait 60]
"""

from __future__ import annotations

import http.client
import json
import socket
import struct
import sys
import time
from urllib.parse import unquote, urlsplit

SERVICE = "/daemon.StartedService/"

# ConnectionEvent.type
EVENT_NEW, EVENT_UPDATE, EVENT_CLOSED = 0, 1, 2


class BoxApiError(RuntimeError):
    """API недоступен, отказал или вернул ошибку gRPC."""


# --- protobuf ---------------------------------------------------------------


def _varint(value: int) -> bytes:
    value &= (1 << 64) - 1                 # int64 < 0 кодируется как 10-байтовый varint
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field(num: int, value) -> bytes:
    if isinstance(value, int):          # bool — тоже int
        return _varint(num << 3) + _varint(int(value))
    if isinstance(value, str):
        value = value.encode("utf-8")
    return _varint(num << 3 | 2) + _varint(len(value)) + value


def encode(fields: dict) -> bytes:
    """{номер поля: значение} → protobuf. Пустые значения по умолчанию не пишутся."""
    return b"".join(_field(n, v) for n, v in sorted(fields.items()) if v not in (None, "", 0, False))


def _read_varint(buf: bytes, i: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if i >= len(buf):
            raise BoxApiError("protobuf: обрыв varint")
        byte = buf[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return result, i


def decode(buf: bytes) -> dict[int, list]:
    """protobuf → {номер поля: [значения]}: int для varint/fixed, bytes для length-delimited."""
    out: dict[int, list] = {}
    i = 0
    while i < len(buf):
        key, i = _read_varint(buf, i)
        num, wire = key >> 3, key & 7
        if wire == 0:
            value, i = _read_varint(buf, i)
        elif wire == 2:
            size, i = _read_varint(buf, i)
            value = buf[i:i + size]
            i += size
        elif wire == 1:
            value = struct.unpack_from("<q", buf, i)[0]
            i += 8
        elif wire == 5:
            value = struct.unpack_from("<i", buf, i)[0]
            i += 4
        else:
            raise BoxApiError(f"protobuf: неподдерживаемый wire type {wire}")
        out.setdefault(num, []).append(value)
    return out


def _int(msg: dict, num: int) -> int:
    value = msg.get(num, [0])[-1]
    return value - (1 << 64) if value >= 1 << 63 else value


def _str(msg: dict, num: int) -> str:
    return msg.get(num, [b""])[-1].decode("utf-8", "replace")


def _strs(msg: dict, num: int) -> list[str]:
    return [v.decode("utf-8", "replace") for v in msg.get(num, [])]


def _msgs(msg: dict, num: int) -> list[dict]:
    return [decode(v) for v in msg.get(num, [])]


def _group_item(m: dict) -> dict:
    return {"tag": _str(m, 1), "type": _str(m, 2),
            "url_test_time": _int(m, 3), "url_test_delay": _int(m, 4)}


def _group(m: dict) -> dict:
    return {"tag": _str(m, 1), "type": _str(m, 2), "selectable": bool(_int(m, 3)),
            "selected": _str(m, 4), "items": [_group_item(x) for x in _msgs(m, 6)]}


def connection(m: dict) -> dict:
    return {"id": _str(m, 1), "inbound": _str(m, 2), "inbound_type": _str(m, 3),
            "network": _str(m, 5), "source": _str(m, 6), "destination": _str(m, 7),
            "domain": _str(m, 8), "protocol": _str(m, 9), "user": _str(m, 10),
            "created_at": _int(m, 12), "closed_at": _int(m, 13),
            "uplink_total": _int(m, 16), "downlink_total": _int(m, 17),
            "rule": _str(m, 18), "outbound": _str(m, 19), "outbound_type": _str(m, 20),
            "chain": _strs(m, 21)}


def connection_events(m: dict) -> dict:
    events = []
    for ev in _msgs(m, 1):
        conn = _msgs(ev, 3)
        events.append({"type": _int(ev, 1), "id": _str(ev, 2),
                       "connection": connection(conn[0]) if conn else None,
                       "uplink_delta": _int(ev, 4), "downlink_delta": _int(ev, 5),
                       "closed_at": _int(ev, 6)})
    return {"events": events, "reset": bool(_int(m, 2))}


# --- транспорт gRPC-Web ------------------------------------------------------


def _frame(payload: bytes) -> bytes:
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def _check_status(status, message, method: str) -> None:
    if status not in (None, "", "0"):
        raise BoxApiError(f"API {method}: grpc-status {status} {unquote(message or '')}".rstrip())


def _parse_trailers(raw: bytes) -> dict:
    out = {}
    for line in raw.decode("utf-8", "replace").split("\r\n"):
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.strip().lower()] = value.strip()
    return out


class _Call:
    """Один вызов gRPC-Web: итератор по кадрам-сообщениям ответа."""

    def __init__(self, conn: http.client.HTTPConnection, resp, method: str):
        self._conn, self._resp, self._method = conn, resp, method
        self._closed = False

    def _read(self, size: int) -> bytes:
        data = self._resp.read(size)
        if len(data) != size:
            raise BoxApiError(f"API {self._method}: поток оборвался")
        return data

    def __iter__(self):
        try:
            while True:
                head = self._resp.read(5)
                if not head:
                    raise BoxApiError(f"API {self._method}: ответ без трейлера")
                if len(head) < 5:
                    raise BoxApiError(f"API {self._method}: поток оборвался")
                flag, size = head[0], struct.unpack(">I", head[1:])[0]
                body = self._read(size)
                if flag & 0x80:
                    trailers = _parse_trailers(body)
                    _check_status(trailers.get("grpc-status"), trailers.get("grpc-message"),
                                  self._method)
                    return
                yield body
        except BoxApiError:
            if not self._closed:
                raise
        except Exception as exc:  # noqa: BLE001 — http.client после close() падает чем угодно
            if not self._closed:
                raise BoxApiError(f"API {self._method}: {exc}") from exc
        finally:
            self.close()

    def close(self) -> None:
        """Закрыть вызов; поток, читающий его в другом потоке, завершится без ошибки."""
        self._closed = True
        # shutdown будит поток, заблокированный в read() на другом потоке.
        sock = getattr(self._conn, "sock", None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._conn.close()


class BoxApi:
    """Вызовы StartedService. Все ошибки сети и gRPC — BoxApiError."""

    def __init__(self, url: str, secret: str = "", timeout: float = 5.0):
        parts = urlsplit(url if "://" in url else f"http://{url}")
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise BoxApiError(f"API: некорректный адрес {url!r}")
        self.url = f"{parts.scheme}://{parts.netloc}"
        self._https = parts.scheme == "https"
        self._host, self._port = parts.hostname, parts.port or (443 if self._https else 80)
        self._secret = secret
        self.timeout = timeout

    def _open(self, method: str, request: bytes, timeout) -> _Call:
        cls = http.client.HTTPSConnection if self._https else http.client.HTTPConnection
        conn = cls(self._host, self._port, timeout=self.timeout)
        headers = {"content-type": "application/grpc-web+proto", "x-grpc-web": "1",
                   "accept": "application/grpc-web+proto"}
        if self._secret:
            headers["authorization"] = f"Bearer {self._secret}"
        try:
            conn.request("POST", SERVICE + method, body=_frame(request), headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise BoxApiError(f"API недоступен ({self.url}): {exc}") from exc
        if resp.status == 401:
            conn.close()
            raise BoxApiError("API вернул 401 — неверный secret")
        if resp.status != 200:
            conn.close()
            raise BoxApiError(f"API {method}: HTTP {resp.status}")
        # Ошибка без тела приходит «trailers-only» — статус прямо в заголовках.
        try:
            _check_status(resp.getheader("grpc-status"), resp.getheader("grpc-message"), method)
        except BoxApiError:
            conn.close()
            raise
        if conn.sock is not None:
            conn.sock.settimeout(timeout)
        return _Call(conn, resp, method)

    def unary(self, method: str, request: bytes = b"") -> bytes:
        messages = list(self._open(method, request, self.timeout))
        return messages[0] if messages else b""

    def first(self, method: str, request: bytes = b"") -> bytes:
        """Первое сообщение потока (снимок состояния), затем поток закрывается."""
        call = self._open(method, request, self.timeout)
        try:
            for message in call:
                return message
        finally:
            call.close()
        raise BoxApiError(f"API {method}: пустой поток")

    def stream(self, method: str, request: bytes = b"") -> _Call:
        """Долгий поток: без тайм-аута чтения, прервать можно через close()."""
        return self._open(method, request, None)

    # --- методы -------------------------------------------------------------

    def version(self) -> dict:
        m = decode(self.unary("GetVersion"))
        return {"version": _str(m, 1), "api_version": _int(m, 2)}

    def groups(self) -> list[dict]:
        return [_group(g) for g in _msgs(decode(self.first("SubscribeGroups")), 1)]

    def outbounds(self) -> list[dict]:
        return [_group_item(o) for o in _msgs(decode(self.first("SubscribeOutbounds")), 1)]

    def select(self, group: str, outbound: str) -> None:
        self.unary("SelectOutbound", encode({1: group, 2: outbound}))

    def url_test(self, tag: str) -> None:
        self.unary("URLTest", encode({1: tag}))

    def subscribe_connections(self, interval: float) -> _Call:
        """Поток ConnectionEvents; interval — период UPDATE-событий, секунды."""
        return self.stream("SubscribeConnections", encode({1: int(interval * 1e9)}))


# --- адрес из конфига sing-box и проверка связности --------------------------


def from_singbox_config(path: str, timeout: float = 5.0) -> BoxApi:
    """BoxApi по `services[type=api]` из конфига sing-box."""
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    for svc in cfg.get("services") or []:
        if svc.get("type") == "api":
            host = svc.get("listen") or "127.0.0.1"
            if host in ("0.0.0.0", "::", ""):
                host = "127.0.0.1"
            if ":" in host:
                host = f"[{host}]"
            return BoxApi(f"http://{host}:{svc.get('listen_port') or 9090}",
                          svc.get("secret", ""), timeout)
    raise BoxApiError(f"в {path} нет services[type=api]")


def group_delay(api: BoxApi, group: str, since: int) -> int | None:
    """Лучшая задержка URL-теста членов группы, измеренная не раньше since (unix, с)."""
    for g in api.groups():
        if g["tag"] == group:
            delays = [i["url_test_delay"] for i in g["items"]
                      if i["url_test_delay"] > 0 and i["url_test_time"] >= since]
            return min(delays) if delays else None
    raise BoxApiError(f"группа '{group}' не найдена")


def health(api: BoxApi, group: str, wait: float) -> int | None:
    """Запустить URL-тест группы и дождаться успешной задержки до wait секунд."""
    deadline = time.monotonic() + wait
    started = int(time.time()) - 1
    while True:
        try:
            api.url_test(group)
            time.sleep(3)
            delay = group_delay(api, group, started)
            if delay:
                return delay
        except BoxApiError:
            pass
        if time.monotonic() >= deadline:
            return None
        time.sleep(2)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python3 -m nodes_common.box_api")
    sub = ap.add_subparsers(dest="cmd", required=True)
    h = sub.add_parser("health", help="связность через группу (URL-тест)")
    h.add_argument("--config", required=True, help="конфиг sing-box с services[type=api]")
    h.add_argument("--group", required=True)
    h.add_argument("--wait", type=float, default=0, help="ждать успеха до N секунд")
    args = ap.parse_args(argv)
    try:
        api = from_singbox_config(args.config)
        delay = health(api, args.group, args.wait)
    except (OSError, ValueError, BoxApiError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if delay is None:
        return 1
    print(delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
