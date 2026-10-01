"""Идентичность устройства для панелей подписок (Marzban, Remnawave, Marzneshin…).

Панели с лимитом устройств считают устройством каждый новый `X-Hwid`. Свой HWID у каждой
установки хранится в `device.json` — один на роутер, общий для config-main и config-wh:

    {"hwid": "k3j9…"}

Файл создаётся при первой загрузке. Установка, где happ-подписка уже работала со старым
общим HWID из кода, получает его же (`LEGACY_HWID`), чтобы панель не засчитала новое
устройство; сменить его можно правкой файла или `happ_headers` подписки.
"""

from __future__ import annotations

import json
import os
import secrets
import string

from nodes_common.fileio import atomic_write_text

FILE_NAME = "device.json"
# HWID, который до 0.3.7 был зашит в код и уходил с каждой happ-подпиской.
LEGACY_HWID = "74jf74nf8f4jr5je"
_ALPHABET = string.ascii_lowercase + string.digits

# Описание устройства для запросных заголовков (как у мобильного клиента Happ).
DEVICE_HEADERS = {
    "X-Device-Os": "Android",
    "X-Device-Locale": "ru",
    "X-Device-Model": "ELP-NX1",
    "X-Ver-Os": "15",
}


def default_path(providers_path: str) -> str:
    """device.json рядом с providers.json; для раскладки v2 (config-main/, config-wh/) —
    на уровень выше, чтобы основной и клиентский наборы шли с одним HWID."""
    folder = os.path.dirname(os.path.abspath(providers_path))
    if os.path.basename(folder).startswith("config-"):
        folder = os.path.dirname(folder)
    return os.path.join(folder, FILE_NAME)


def new_hwid() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(16))


def read(path: str) -> dict:
    """Содержимое device.json или {} (нет файла/битый)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(path: str, legacy: bool = False, log=print) -> dict:
    """Прочитать device.json, создав его при отсутствии. legacy — установка уже ходила
    в happ-подписку со старым общим HWID: сохраняем его."""
    data = read(path)
    hwid = data.get("hwid")
    if isinstance(hwid, str) and hwid.strip():
        return {**data, "hwid": hwid.strip()}
    data = {**data, "hwid": LEGACY_HWID if legacy else new_hwid()}
    try:
        atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        log(f"[fetch] создан {path} ({'прежний общий HWID' if legacy else 'новый HWID'})")
    except OSError as exc:
        log(f"[fetch] {path} не записан ({exc}) — HWID только на этот прогон")
    return data


def headers(device: dict) -> dict:
    """Стандартные заголовки устройства для подписки."""
    return {**DEVICE_HEADERS, "X-Hwid": device["hwid"]}
