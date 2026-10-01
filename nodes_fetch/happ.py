"""Подписки Happ: расшифровка ссылок happ://crypt..crypt5 и загрузка подписки.

Ссылка happ://cryptN/… содержит зашифрованный реальный URL подписки. Мы его
расшифровываем (happ_decode, чистый Python — работает и на роутере) и скачиваем
подписку, отправляя заголовки, имитирующие приложение Happ (иначе провайдер может
отдать пустой ответ / упереться в лимит устройств). Ответ при необходимости
распаковывается gzip и декодируется из Base64 — на выходе текст со share-links.

Порт логики из medved-vpn/happ_sub.py. Загрузка — requests (gzip распаковывается
автоматически; поддерживается proxy, в т.ч. socks5 через PySocks).
"""

from __future__ import annotations

import base64
import time

import requests

from . import happ_decode
from .device import DEVICE_HEADERS

# Заголовки, имитирующие мобильное приложение Happ (без них подписка часто пустая).
# X-Hwid берётся из device.json установки (nodes_fetch.device); X-Real-Ip намеренно
# фиксированный. Любой заголовок можно заменить в providers.json через happ_headers подписки.
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


def is_happ_link(url: str) -> bool:
    """Ссылка вида happ://crypt…/… (зашифрованный URL подписки)."""
    return isinstance(url, str) and url.strip().startswith("happ://crypt")


def _fetch(url: str, headers: dict, timeout=_TIMEOUT, proxies=None, meta_out=None) -> str:
    """GET подписки с happ-заголовками (+ попытка Base64-декода). Ошибка HTTP → исключение.
    meta_out — dict, куда кладутся заголовки ответа (метаданные подписки)."""
    resp = requests.get(url, headers=headers, timeout=timeout, proxies=proxies)
    resp.raise_for_status()
    if meta_out is not None:
        meta_out.update(resp.headers)
    text = resp.content.decode("utf-8", errors="replace")   # gzip уже распакован requests
    try:                                    # подписка часто отдаётся в Base64
        return base64.b64decode(text, validate=True).decode("utf-8", errors="replace")
    except Exception:                       # noqa: BLE001 — не Base64: берём как есть
        return text


def _retryable(exc: Exception) -> bool:
    """Сеть/таймаут/5xx — повторяем; 4xx (ссылка, лимит устройств) — нет."""
    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else 0
        return code >= 500 or code == 429
    return isinstance(exc, (requests.RequestException, OSError))


def subscription_text(happ_link: str, headers: "dict | None" = None,
                      timeout=_TIMEOUT, proxies=None, hwid: str = "",
                      meta_out=None, log=None) -> str:
    """happ://crypt…/… → расшифровать URL → скачать → текст подписки (share-links).

    hwid — X-Hwid установки; headers — оверрайд/добавка к happ-заголовкам подписки.
    Сетевые сбои и 5xx повторяются с паузами RETRY_DELAYS.
    """
    real_url = happ_decode.decode_link(happ_link)
    hdrs = dict(_HEADERS)
    if hwid:
        hdrs["X-Hwid"] = hwid
    if headers:
        hdrs.update(headers)
    for attempt, delay in enumerate((*RETRY_DELAYS, None), 1):
        try:
            return _fetch(real_url, hdrs, timeout=timeout, proxies=proxies, meta_out=meta_out)
        except Exception as exc:  # noqa: BLE001 — решаем по типу ниже
            if delay is None or not _retryable(exc):
                raise
            if log:
                log(f"  [fetch] {exc.__class__.__name__}, повтор {attempt} из "
                    f"{len(RETRY_DELAYS)} через {delay} с…")
            time.sleep(delay)
    raise AssertionError("unreachable")
