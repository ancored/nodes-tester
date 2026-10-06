"""Ключи расшифровки ссылок happ://crypt…: загрузка из внешнего источника и кэш.

Ключи принадлежат приложению Happ и в nodes-tester не входят. Источник задаётся в
providers.json (fetch.happ_keys_url) — URL или локальный путь; по умолчанию —
src/keys.rs проекта hpwnr. Понимаются два вида: исходник keys.rs (константы PKCS1_KEYS
и CRYPT5_PKCS8) и JSON {"crypt": [4 ключа], "crypt5": {маркер: ключ}}.

Скачанные ключи кэшируются в happ-keys.json рядом с device.json. Кэш обновляется, если
его нет или для ссылки не нашлось ключа (новая версия Happ), но не чаще раза в 6 ч.
"""

from __future__ import annotations

import json
import os
import re
import time

import requests

from nodes_common.fileio import atomic_write_text

from .device import default_path as _device_path

FILE_NAME = "happ-keys.json"
DEFAULT_URL = "https://raw.githubusercontent.com/Omegaplexx/hpwnr/main/src/keys.rs"
_TIMEOUT = (30, 60)


class KeysError(RuntimeError):
    """Ключи не получены или не распознаны."""


def default_path(providers_path: str) -> str:
    """happ-keys.json — там же, где device.json."""
    return os.path.join(os.path.dirname(_device_path(providers_path)), FILE_NAME)


def _block(text: str, name: str) -> str:
    """Тело константы `name … = [ … ];` из исходника Rust."""
    m = re.search(rf"\b{name}\b[^=]*=\s*&?\[(.*?)\];", text, re.S)
    return m.group(1) if m else ""


def parse(text: str) -> dict:
    """keys.rs или JSON → {"crypt": [...], "crypt5": {...}}."""
    try:
        data = json.loads(text)
    except ValueError:
        data = {"crypt": re.findall(r'"([A-Za-z0-9+/=]{100,})"', _block(text, "PKCS1_KEYS")),
                "crypt5": dict(re.findall(r'\(\s*"([A-Za-z0-9]{8})"\s*,\s*"([A-Za-z0-9+/=]{100,})"\s*\)',
                                          _block(text, "CRYPT5_PKCS8")))}
    if not isinstance(data, dict):
        raise KeysError("источник ключей не распознан")
    keys = {"crypt": [k for k in data.get("crypt") or [] if isinstance(k, str)],
            "crypt5": {m: k for m, k in (data.get("crypt5") or {}).items()
                       if isinstance(m, str) and isinstance(k, str)}}
    if not keys["crypt"] and not keys["crypt5"]:
        raise KeysError("в источнике нет ключей Happ")
    return keys


def load(path: str):
    """Ключи из кэша или None."""
    try:
        with open(path, encoding="utf-8") as f:
            return parse(f.read())
    except (OSError, KeysError):
        return None


def age(path: str) -> float:
    """Возраст кэша в секундах (бесконечность, если файла нет)."""
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return float("inf")


def download(source: str, proxies=None, timeout=_TIMEOUT) -> dict:
    """Ключи из URL или локального файла."""
    if "://" not in source:
        try:
            with open(source, encoding="utf-8") as f:
                return parse(f.read())
        except OSError as exc:
            raise KeysError(f"файл ключей не прочитан: {exc}") from exc
    try:
        response = requests.get(source, timeout=timeout, proxies=proxies)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise KeysError(f"ключи Happ не скачаны ({source}): {exc}") from exc
    return parse(response.text)


def refresh(path: str, source: str, proxies=None, log=None) -> dict:
    """Скачать ключи и сохранить в кэш."""
    keys = download(source or DEFAULT_URL, proxies=proxies)
    if path:
        atomic_write_text(path, json.dumps(keys, indent=1) + "\n")
    if log:
        log(f"  [fetch] ключи Happ обновлены из {source or DEFAULT_URL}: "
            f"crypt {len(keys['crypt'])}, crypt5 {len(keys['crypt5'])}")
    return keys
