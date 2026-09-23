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

import requests

from . import happ_decode

# Заголовки, имитирующие мобильное приложение Happ (без них подписка часто пустая).
# X-Hwid / X-Real-Ip намеренно фиксированные — при упоре в лимит устройств их можно
# заменить в providers.json через ключ подписки (см. subscription_text(..., headers=)).
_HEADERS = {
    "User-Agent": "Happ/3.13.0",
    "X-Device-Os": "Android",
    "X-Device-Locale": "ru",
    "X-Device-Model": "ELP-NX1",
    "X-Ver-Os": "15",
    "Accept-Encoding": "gzip",
    "Connection": "close",
    "X-Hwid": "74jf74nf8f4jr5je",
    "X-Real-Ip": "101.202.303.404",
    "X-Forwarded-For": "101.202.303.404",
}
_TIMEOUT = 30


def is_happ_link(url: str) -> bool:
    """Ссылка вида happ://crypt…/… (зашифрованный URL подписки)."""
    return isinstance(url, str) and url.strip().startswith("happ://crypt")


def _fetch(url: str, headers: dict, timeout=_TIMEOUT, proxies=None) -> str:
    """GET подписки с happ-заголовками (+ попытка Base64-декода). Ошибка HTTP → исключение."""
    resp = requests.get(url, headers=headers, timeout=timeout, proxies=proxies)
    resp.raise_for_status()
    text = resp.content.decode("utf-8", errors="replace")   # gzip уже распакован requests
    try:                                    # подписка часто отдаётся в Base64
        return base64.b64decode(text, validate=True).decode("utf-8", errors="replace")
    except Exception:                       # noqa: BLE001 — не Base64: берём как есть
        return text


def subscription_text(happ_link: str, headers: "dict | None" = None,
                      timeout=_TIMEOUT, proxies=None) -> str:
    """happ://crypt…/… → расшифровать URL → скачать → текст подписки (share-links).

    headers — необязательный оверрайд/добавка к happ-заголовкам (напр. свой X-Hwid).
    """
    real_url = happ_decode.decode_link(happ_link)
    hdrs = dict(_HEADERS)
    if headers:
        hdrs.update(headers)
    return _fetch(real_url, hdrs, timeout=timeout, proxies=proxies)
