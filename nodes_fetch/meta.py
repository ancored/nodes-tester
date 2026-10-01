"""Метаданные подписки из заголовков ответа панели.

    Subscription-Userinfo: upload=0; download=5480844999; total=32212254720; expire=1792577243
    Profile-Title: base64:0JLQuNC9…           (или обычный текст)
    Announce: base64:4o+z…                     (сообщение провайдера)
    Support-Url, Profile-Web-Page-Url, Profile-Update-Interval (часы)

Результат — dict для `sources[].meta` в raw (только распознанные поля):
{upload, download, total, expire, title, announce, support_url, web_page_url,
update_interval_hours}. Нули в total/expire у панелей означают «без лимита» и сохраняются.
"""

from __future__ import annotations

import base64
import binascii

_USERINFO_KEYS = ("upload", "download", "total", "expire")
_TEXT_HEADERS = {
    "profile-title": "title",
    "announce": "announce",
    "support-url": "support_url",
    "profile-web-page-url": "web_page_url",
}
_MAX_TEXT = 2000


def _text(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("base64:"):
        try:
            value = base64.b64decode(value[7:].strip() + "===", validate=False).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return ""
    return value.strip()[:_MAX_TEXT]


def userinfo(value: str) -> dict:
    out = {}
    for part in value.split(";"):
        key, _, num = part.partition("=")
        key = key.strip().lower()
        if key in _USERINFO_KEYS:
            try:
                out[key] = int(float(num.strip()))
            except ValueError:
                continue
    return out


def from_headers(headers) -> dict:
    """Заголовки ответа (любой mapping) → meta; пусто, если панель ничего не прислала."""
    if not headers:
        return {}
    low = {str(k).lower(): str(v) for k, v in headers.items()}
    meta = userinfo(low.get("subscription-userinfo", ""))
    for header, key in _TEXT_HEADERS.items():
        if low.get(header):
            text = _text(low[header])
            if text:
                meta[key] = text
    interval = low.get("profile-update-interval", "").strip()
    if interval:
        try:
            meta["update_interval_hours"] = float(interval)
        except ValueError:
            pass
    return meta
