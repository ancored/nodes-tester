"""Источники подписок → узлы sing-box (dict, tag = имя из подписки).

Источник — URL, локальный файл, ссылка happ://crypt… или каталог файлов (type=folder).
Содержимое распознаётся само: share-ссылки построчно, base64 от них, Clash/mihomo YAML
(proxies) или JSON sing-box (outbounds). Сбой или пустота → FetchError: fetch пометит
подписку упавшей и подставит её прошлые ноды.
"""

import json
import os
import re
import time
from urllib.parse import urlparse

import yaml

from . import device as devicemod
from . import happ, parsers, util
from .parsers import clash
from .parsers._common import LinkError, b64text

# В JSON-подписке sing-box служебные аутбаунды — не ноды.
_NON_NODE_TYPES = {"selector", "urltest", "direct", "block", "dns"}
# Паузы перед повторами URL-подписки (разовый 5xx/обрыв панели).
RETRY_DELAYS = (2, 10, 30)


class FetchError(Exception):
    """Источник недоступен или отдал пустоту/мусор."""


class Context:
    """Параметры загрузки для одного прогона fetch."""

    def __init__(self, base_dir, timeout=util.DEFAULT_TIMEOUT, retries=3, proxy=None,
                 log=print, device=None, happ_keys=None, happ_keys_url=None):
        self.base_dir = base_dir
        self.timeout = timeout
        self.retries = retries
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.log = log
        self.device = device or {}          # device.json: {"hwid": …}
        self.happ_keys = happ_keys          # путь к кэшу ключей Happ (happ-keys.json)
        self.happ_keys_url = happ_keys_url  # источник ключей Happ
        self.headers = {}                   # заголовки устройства текущей подписки (url)
        self.response_headers = {}          # заголовки ответа текущей подписки → meta


def kind_of(sub):
    if sub.get("type") == "folder":
        return "folder"
    if sub.get("file"):
        return "file"
    if happ.is_happ_link(sub.get("url", "")):
        return "happ"
    if sub.get("url"):
        return "url"
    return None


def fetch_subscription(sub, ctx):
    """Узлы одной подписки. Ничего не нашли/сбой → FetchError."""
    kind = kind_of(sub)
    ctx.response_headers = {}
    ctx.headers = (devicemod.headers(ctx.device)
                   if kind == "url" and sub.get("send_device") and ctx.device.get("hwid") else {})
    if kind == "folder":
        nodes = _from_folder(sub, ctx)
    elif kind == "file":
        nodes = _from_file(_resolve(sub["file"], ctx), ctx)
    elif kind == "happ":
        text = happ.subscription_text(sub["url"], sub.get("happ_headers") or None,
                                      timeout=ctx.timeout, proxies=ctx.proxies,
                                      hwid=ctx.device.get("hwid", ""),
                                      meta_out=ctx.response_headers, log=ctx.log,
                                      keys_path=ctx.happ_keys, keys_url=ctx.happ_keys_url)
        nodes = nodes_from_text(text, ctx)
    elif kind == "url":
        nodes = _from_url(sub["url"], sub.get("user_agent"), ctx)
    else:
        raise FetchError("у подписки нет url/file/type=folder")
    if not nodes:
        raise FetchError("в подписке не найдено ни одной ноды")
    return nodes


# --- Разбор содержимого ---------------------------------------------------------------

# http(s):// в начале — адрес подписки, а не HTTP-прокси.
_LINK_SCHEMES = set(parsers.SCHEMES) - {"http", "https"}


def _starts_with_link(text):
    words = text.split(None, 1)
    return parsers.scheme_of(words[0] if words else "") in _LINK_SCHEMES


def parse_links(text, ctx):
    """Share-ссылки по одной на строку → узлы. Битая строка пропускается с записью в лог."""
    nodes = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            nodes.extend(parsers.parse_link(line))
        except (LinkError, ValueError, KeyError, TypeError) as exc:
            ctx.log(f"  [fetch] пропущена строка {parsers.scheme_of(line) or '?'}:// "
                    f"({exc.__class__.__name__}: {exc})")
    return nodes


def from_clash(proxies, ctx):
    nodes = []
    for proxy in proxies or []:
        try:
            converted = clash.convert(proxy) if isinstance(proxy, dict) else None
        except (KeyError, ValueError, TypeError) as exc:
            ctx.log(f"  [fetch] пропущена запись Clash ({exc.__class__.__name__}: {exc})")
            continue
        if isinstance(converted, list):
            nodes.extend(converted)
        elif converted:
            nodes.append(converted)
    return nodes


def _structured(text):
    """Clash YAML или JSON sing-box → dict; иначе None."""
    if "outbounds" in text:
        for candidate in (text, re.sub(r"(?m)^\s*//.*$", "", text)):
            try:
                data = json.loads(candidate)
            except ValueError:
                continue
            if isinstance(data, dict):
                return data
    if "proxies" in text:
        try:
            data = yaml.safe_load(text.replace("\t", "  "))
        except yaml.YAMLError:
            return None
        if isinstance(data, dict):
            return data
    return None


def nodes_from_text(text, ctx, _depth=0):
    """Содержимое подписки любого поддерживаемого вида → узлы."""
    text = text.strip()
    if not text:
        return []
    if _starts_with_link(text):
        return parse_links(text, ctx)
    data = _structured(text)
    if data is not None:
        if isinstance(data.get("proxies"), list):
            return from_clash(data["proxies"], ctx)
        if isinstance(data.get("outbounds"), list):
            return [o for o in data["outbounds"]
                    if isinstance(o, dict) and o.get("type") not in _NON_NODE_TYPES]
        return []
    if _depth == 0:
        try:
            return nodes_from_text(b64text(text), ctx, _depth=1)
        except LinkError:
            pass
    return parse_links(text, ctx)


# --- Источники ------------------------------------------------------------------------

def _resolve(path, ctx):
    return path if os.path.isabs(path) else os.path.join(ctx.base_dir, path)


def _read_text(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8-sig")


def _from_file(path, ctx):
    if not os.path.isfile(path):
        raise FetchError(f"файл подписки не найден: {path}")
    return nodes_from_text(_read_text(path), ctx)


def _from_url(url, user_agent, ctx):
    url = url.strip()
    if url.startswith("sub://"):                  # адрес подписки в base64
        url = b64text(url[len("sub://"):]).strip()
    if _starts_with_link(url):                    # в «url» лежат сами ссылки
        return parse_links(url, ctx)
    if not urlparse(url).scheme:                  # base64 от ссылок или путь к файлу
        try:
            return nodes_from_text(b64text(url), ctx, _depth=1)
        except LinkError:
            return _from_file(_resolve(url, ctx), ctx)
    return nodes_from_text(_download(url, user_agent, ctx), ctx)


def _get(url, user_agent, ctx):
    response = util.http_get(url, user_agent, timeout=ctx.timeout, proxies=ctx.proxies,
                             **({"headers": ctx.headers} if ctx.headers else {}))
    if response is not None:
        ctx.response_headers = dict(getattr(response, "headers", None) or {})
    return response


def _download(url, user_agent, ctx):
    """Тело подписки текстом; повторы с паузами RETRY_DELAYS."""
    response = _get(url, user_agent, ctx)
    for attempt in range(1, ctx.retries + 1):
        if response:
            break
        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)) - 1]
        ctx.log(f"  [fetch] нет ответа, повтор {attempt} из {ctx.retries} через {delay} с…")
        time.sleep(delay)
        response = _get(url, user_agent, ctx)
    if not response:
        raise FetchError("нет ответа (сеть или HTTP-статус ≠ 200)")
    try:
        text = response.content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FetchError("ответ не в UTF-8") from exc
    if not text:                                  # часть панелей отдаёт подписку только Clash
        response = _get(url, "clashmeta", ctx)
        text = response.content.decode("utf-8-sig", errors="replace") if response else ""
    if not text.strip():
        raise FetchError("пустой ответ")
    return text


def _from_folder(sub, ctx):
    """Каталог отдельных файлов, по узлу на файл (AWG .conf). Битый файл — пропуск."""
    fmt = sub.get("format", "awg")
    parse_file = parsers.FOLDER_FORMATS.get(fmt)
    if parse_file is None:
        raise FetchError(f"неизвестный формат folder-подписки: {fmt!r}")
    folder = _resolve(sub.get("path") or sub.get("folder") or "", ctx)
    if not os.path.isdir(folder):
        raise FetchError(f"папка подписки не найдена: {folder}")
    ext = sub.get("ext", ".conf").lower()
    nodes = []
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(ext):
            continue
        try:
            nodes.append(parse_file(os.path.join(folder, name)))
        except Exception as exc:  # noqa: BLE001 — битый/неполный файл: пропуск
            ctx.log(f"  [fetch] пропущен {name} ({exc.__class__.__name__}: {exc})")
    return nodes
