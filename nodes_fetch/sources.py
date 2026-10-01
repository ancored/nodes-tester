"""Источники подписок → узлы парсера (dict sing-box, tag = исходное имя из подписки).

Логика разбора контента перенесена из subscribe/main.py (исходный sing-box-subscribe) без
изменения результата. Отличия — только в обработке сбоев: вместо «тихого» пустого списка
источник бросает FetchError, чтобы fetch мог отметить подписку упавшей и подставить last-good.
"""

import json
import os
import re
import time
from urllib.parse import urlparse

import ruamel.yaml
import yaml

from . import device as devicemod
from . import happ, parsers, util
from .parsers.clash2base64 import clash2v2ray

# Контент, который уже является share-ссылками (не base64/yaml/json).
_SHARE_PREFIXES = ("vmess://", "vless://", "ss://", "ssr://", "trojan://", "tuic://",
                   "hysteria://", "hysteria2://", "hy2://", "wg://", "wireguard://",
                   "http2://", "socks://", "socks5://")
# sing-box JSON-подписка: служебные аутбаунды — не ноды.
_NON_NODE_TYPES = {"selector", "urltest", "direct", "block", "dns"}


class FetchError(Exception):
    """Источник недоступен или отдал пустоту/мусор."""


class Context:
    """Параметры загрузки для одного прогона fetch."""

    def __init__(self, base_dir, timeout=util.DEFAULT_TIMEOUT, retries=3, proxy=None,
                 log=print, device=None):
        self.base_dir = base_dir
        self.timeout = timeout
        self.retries = retries
        self.proxies = {"http": proxy, "https": proxy} if proxy else None
        self.log = log
        self.device = device or {}          # device.json: {"hwid": …}
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
                                      meta_out=ctx.response_headers, log=ctx.log)
        nodes = _flatten(parse_content(text, ctx))
    elif kind == "url":
        nodes = _from_url(sub["url"], sub.get("user_agent"), ctx)
    else:
        raise FetchError("у подписки нет url/file/type=folder")
    if not nodes:
        raise FetchError("в подписке не найдено ни одной ноды")
    return nodes


def parse_content(content, ctx):
    """Текст со share-ссылками (по одной на строку) → узлы. Нераспознанные строки пропускаются."""
    nodes = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        module = parsers.get(util.get_protocol(line))
        if module is None or not hasattr(module, "parse"):
            continue
        try:
            node = module.parse(line)
        except Exception as exc:  # noqa: BLE001 — битая строка не должна ронять подписку
            ctx.log(f"  [fetch] пропущена строка ({exc.__class__.__name__})")
            continue
        if node:
            nodes.append(node)
    return nodes


def _flatten(items):
    """shadowtls-парсер отдаёт кортеж (нода, shadowtls-нода) — раскладываем в две."""
    out = []
    for item in items:
        if isinstance(item, tuple):
            out.extend([item[0], item[1]])
        else:
            out.append(item)
    return out


def _resolve(path, ctx):
    return path if os.path.isabs(path) else os.path.join(ctx.base_dir, path)


def _from_url(url, user_agent, ctx):
    if url.startswith("sub://"):
        url = util.b64Decode(url[6:]).decode("utf-8")
    if not urlparse(url).scheme:
        # «url» без схемы — сами ссылки в base64, либо путь к файлу
        try:
            return _flatten(parse_content(util.b64Decode(url).decode("utf-8"), ctx))
        except Exception:  # noqa: BLE001 — не base64: считаем путём к файлу
            content = _file_content(_resolve(url, ctx))
    else:
        content = _url_content(url, user_agent, ctx)
    return _nodes_from_content(content, ctx)


def _nodes_from_content(content, ctx):
    if isinstance(content, dict):
        if "proxies" in content:          # clash-yaml → share-ссылки → парсеры
            links = "\n".join(clash2v2ray(proxy) for proxy in content["proxies"])
            return _flatten(parse_content(links, ctx))
        if "outbounds" in content:        # готовая sing-box JSON-подписка
            return [o for o in content["outbounds"] if o.get("type") not in _NON_NODE_TYPES]
        return []
    return _flatten(parse_content(content, ctx))


# Паузы перед повторами URL-подписки (разовый 5xx/обрыв панели).
RETRY_DELAYS = (2, 10, 30)


def _get(url, user_agent, ctx):
    if ctx.headers:
        response = util.http_get(url, user_agent, timeout=ctx.timeout, proxies=ctx.proxies,
                                 headers=ctx.headers)
    else:
        response = util.http_get(url, user_agent, timeout=ctx.timeout, proxies=ctx.proxies)
    if response is not None:
        ctx.response_headers = dict(getattr(response, "headers", None) or {})
    return response


def _url_content(url, user_agent, ctx):
    if url.startswith(_SHARE_PREFIXES):   # в «url» лежит сама ссылка
        return util.noblankLine(url)
    response = _get(url, user_agent, ctx)
    attempt = 1
    while not response and attempt <= ctx.retries:
        delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS)) - 1]
        ctx.log(f"  [fetch] нет ответа, повтор {attempt} из {ctx.retries} через {delay} с…")
        time.sleep(delay)
        response = _get(url, user_agent, ctx)
        attempt += 1
    if not response:
        raise FetchError("нет ответа (сеть или HTTP-статус ≠ 200)")
    try:
        text = response.content.decode("utf-8-sig")   # utf-8-sig срезает BOM
    except UnicodeDecodeError as exc:
        raise FetchError("ответ не в UTF-8") from exc
    if text.isspace():
        raise FetchError("пустой ответ")
    if not text:                          # некоторые панели отдают контент только clash-клиентам
        response = _get(url, "clashmeta", ctx)
        text = response.text if response else ""
        if not text:
            raise FetchError("пустой ответ")
    if text.startswith(_SHARE_PREFIXES):
        return util.noblankLine(text)
    if "proxies" in text:
        try:
            return dict(ruamel.yaml.YAML().load(response.content.decode("utf-8").replace("\t", " ")))
        except Exception:  # noqa: BLE001 — не yaml: отдаём как текст
            return text
    if "outbounds" in text:
        try:
            return json.loads(response.text)
        except ValueError:
            return json.loads(re.sub(r"//.*", "", text))   # JSON с комментариями
    try:
        text = util.b64Decode(text).decode("utf-8")
    except Exception:  # noqa: BLE001 — не base64: оставляем как есть
        pass
    return text


def _file_content(path):
    """Локальный файл подписки: clash .yaml → share-ссылки, иначе текст как есть."""
    if os.path.splitext(path)[1].lower() == ".yaml":
        with open(path, "rb") as f:
            data = dict(yaml.safe_load(f.read()))
        return util.noblankLine("\n".join(clash2v2ray(proxy) for proxy in data["proxies"]))
    return util.noblankLine(util.readFile(path).decode("utf-8"))


def _from_file(path, ctx):
    if not os.path.isfile(path):
        raise FetchError(f"файл подписки не найден: {path}")
    return _flatten(parse_content(_file_content(path), ctx))


def _from_folder(sub, ctx):
    """Каталог отдельных файлов, по узлу на файл (AWG .conf). Битый файл — пропуск."""
    fmt = sub.get("format", "awg")
    module = parsers.get(fmt)
    if module is None or not hasattr(module, "parse_file"):
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
            nodes.append(module.parse_file(os.path.join(folder, name)))
        except Exception as exc:  # noqa: BLE001 — битый/неполный файл: пропуск
            ctx.log(f"  [fetch] пропущен {name} ({exc.__class__.__name__}: {exc})")
    return nodes
