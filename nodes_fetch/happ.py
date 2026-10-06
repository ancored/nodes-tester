"""Подписки Happ: ссылка happ://crypt…/ → URL подписки → текст со share-ссылками.

URL расшифровывается ключами Happ (happ_keys, happ_crypto). Подписка скачивается с
заголовками мобильного приложения Happ — без них панели часто отдают пустой ответ или
упираются в лимит устройств. Ответ в base64 декодируется.
"""

from __future__ import annotations

import base64
import binascii
import time

import requests

from . import happ_crypto, happ_keys
from .device import DEVICE_HEADERS

# Заголовки мобильного Happ. X-Hwid — из device.json установки; X-Real-Ip фиксированный.
# Любой заголовок переопределяется в providers.json (happ_headers подписки).
_HEADERS = {
    "User-Agent": "Happ/3.13.0",
    **DEVICE_HEADERS,
    "Accept-Encoding": "gzip",
    "Connection": "close",
    "X-Real-Ip": "101.202.303.404",
    "X-Forwarded-For": "101.202.303.404",
}
_TIMEOUT = 30
# Паузы перед повторами: разовый 5xx/обрыв панели не должен ронять подписку на сутки.
RETRY_DELAYS = (2, 10, 30)
# Не чаще: ссылка с неизвестным ключом не должна скачивать ключи на каждом прогоне.
KEYS_REFRESH_INTERVAL = 6 * 3600


def is_happ_link(url: str) -> bool:
    """Ссылка вида happ://crypt…/… (зашифрованный URL подписки)."""
    return isinstance(url, str) and url.strip().startswith("happ://crypt")


def resolve_url(link: str, keys_path: str = "", keys_url: str = "", proxies=None, log=None) -> str:
    """URL подписки из ссылки happ. Ключи — из кэша; нет кэша или нужного ключа — скачать."""
    keys = happ_keys.load(keys_path) if keys_path else None
    if keys is not None:
        try:
            return happ_crypto.decode(link, keys)
        except happ_crypto.UnknownKey as exc:
            if happ_keys.age(keys_path) < KEYS_REFRESH_INTERVAL:
                raise
            if log:
                log(f"  [fetch] {exc} — обновляю ключи Happ")
    keys = happ_keys.refresh(keys_path, keys_url, proxies=proxies, log=log)
    return happ_crypto.decode(link, keys)


def _fetch(url: str, headers: dict, timeout=_TIMEOUT, proxies=None, meta_out=None) -> str:
    """GET подписки (gzip распаковывает requests); base64 → текст. Ошибка HTTP → исключение.
    meta_out — dict для заголовков ответа (метаданные подписки)."""
    resp = requests.get(url, headers=headers, timeout=timeout, proxies=proxies)
    resp.raise_for_status()
    if meta_out is not None:
        meta_out.update(resp.headers)
    text = resp.content.decode("utf-8", errors="replace")
    try:
        return base64.b64decode(text, validate=True).decode("utf-8", errors="replace")
    except (binascii.Error, ValueError):
        return text


def _retryable(exc: Exception) -> bool:
    """Сеть/таймаут/5xx/429 — повторяем; прочие 4xx (ссылка, лимит устройств) — нет."""
    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else 0
        return code >= 500 or code == 429
    return isinstance(exc, (requests.RequestException, OSError))


def subscription_text(happ_link: str, headers: "dict | None" = None,
                      timeout=_TIMEOUT, proxies=None, hwid: str = "",
                      meta_out=None, log=None, keys_path: str = "", keys_url: str = "") -> str:
    """happ://crypt…/… → текст подписки. headers дополняют и переопределяют заголовки Happ."""
    url = resolve_url(happ_link, keys_path, keys_url, proxies=proxies, log=log)
    hdrs = dict(_HEADERS)
    if hwid:
        hdrs["X-Hwid"] = hwid
    hdrs.update(headers or {})
    for attempt, delay in enumerate((*RETRY_DELAYS, None), 1):
        try:
            return _fetch(url, hdrs, timeout=timeout, proxies=proxies, meta_out=meta_out)
        except Exception as exc:  # noqa: BLE001 — решаем по типу ниже
            if delay is None or not _retryable(exc):
                raise
            if log:
                log(f"  [fetch] {exc.__class__.__name__}, повтор {attempt} из "
                    f"{len(RETRY_DELAYS)} через {delay} с…")
            time.sleep(delay)
    raise AssertionError("unreachable")
