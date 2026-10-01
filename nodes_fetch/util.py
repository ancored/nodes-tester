"""Утилиты загрузки и парсинга подписок (перенесены из subscribe/tool.py без изменения логики).

Сюда вынесено только то, что нужно стадии fetch: base64, чтение файлов, определение
протокола share-ссылки, HTTP-загрузка и сборка XHTTP-транспорта для парсеров vless/trojan.
Ничего из naming/переименования здесь нет — fetch про имена нод не знает.
"""

import base64
import json
import random
import re
import string
import urllib.parse

import requests

# Браузерный UA по умолчанию (как у исходного sing-box-subscribe).
DEFAULT_UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
              '(KHTML, like Gecko) Version/16.5 Safari/605.1.15')
# connect/read, сек — роутер тоннелирует DNS/connect, нужен запас
DEFAULT_TIMEOUT = (30, 120)


def http_get(url, user_agent=None, timeout=DEFAULT_TIMEOUT, proxies=None, headers=None):
    """GET подписки. Ответ 200 → requests.Response, иначе (ошибка/не-200) → None.
    headers — дополнительные заголовки (устройство для панелей с лимитом устройств)."""
    headers = {**(headers or {}), 'User-Agent': user_agent or DEFAULT_UA}
    try:
        response = requests.get(url, headers=headers, timeout=timeout, proxies=proxies)
    except requests.RequestException:
        return None
    return response if response.status_code == 200 else None


def b64Decode(str):
    str = urllib.parse.unquote(str.strip())
    str += (len(str)%4)*'='
    return base64.urlsafe_b64decode(str)

def readFile(path):
    file = open(path,'rb')
    content = file.read()
    file.close()
    return content

def noblankLine(data):
    lines = data.splitlines()
    newdata = ''
    for index in range(len(lines)):
        line = lines[index]
        t = line.strip()
        if len(t)>0:
            newdata += t
            if index+1<len(lines):
                newdata += '\n'
    return newdata

def firstLine(data):
    lines = data.splitlines()
    for line in lines:
        line = line.strip()
        if line:
            return line

def genName(length=8):
    name = ''
    for i in range(length):
        name += random.choice(string.ascii_letters+string.digits)
    return name

def is_ip(str):
    return re.search(r'^\d+\.\d+\.\d+\.\d+$',str)

def get_protocol(s):
    try:
        m = re.search(r'^(.+?)://', s)
    except Exception as e:
        return None
    if m:
        if m.group(1) == 'hy2':
            s = re.sub(r'^(.+?)://', 'hysteria2://', s)
            m = re.search(r'^(.+?)://', s)
        if m.group(1) == 'wireguard':
            s = re.sub(r'^(.+?)://', 'wg://', s)
            m = re.search(r'^(.+?)://', s)
        if m.group(1) == 'http2':
            s = re.sub(r'^(.+?)://', 'http://', s)
            m = re.search(r'^(.+?)://', s)
        if m.group(1) == 'socks5':
            s = re.sub(r'^(.+?)://', 'socks://', s)
            m = re.search(r'^(.+?)://', s)
        return m.group(1)


# --- XHTTP transport (sing-box-lx, тег with_xhttp) ------------------------
# XHTTP — значение transport.type в аутбаунде vless/trojan (форк Leadaxe/sing-box-lx).
# Share-ссылка: vless://uuid@host:port?type=xhttp&mode=…&path=…&extra=<json>#name
# Правило вывода: эмитить только пришедшие поля — у транспорта свои дефолты.

# URL camelCase -> JSON snake_case для строковых полей транспорта.
_XHTTP_STRMAP = {
    'xPaddingBytes': 'x_padding_bytes',
    'sessionPlacement': 'session_placement', 'sessionKey': 'session_key',
    'seqPlacement': 'seq_placement', 'seqKey': 'seq_key',
    'uplinkDataPlacement': 'uplink_data_placement', 'uplinkDataKey': 'uplink_data_key',
    'uplinkChunkSize': 'uplink_chunk_size', 'uplinkHTTPMethod': 'uplink_http_method',
    'xPaddingKey': 'x_padding_key', 'xPaddingHeader': 'x_padding_header',
    'xPaddingPlacement': 'x_padding_placement', 'xPaddingMethod': 'x_padding_method',
}

# xmux: некоторые провайдеры (MEDVED/puxvpn) шлют ключи camelCase — их надо
# смаппить в snake_case, иначе sing-box падает: `unknown field "cMaxReuseTimes"`.
_XHTTP_XMUX_MAP = {
    'cMaxReuseTimes': 'c_max_reuse_times', 'maxConcurrency': 'max_concurrency',
    'maxConnections': 'max_connections', 'hKeepAlivePeriod': 'h_keep_alive_period',
    'hMaxRequestTimes': 'h_max_request_times', 'hMaxReusableSecs': 'h_max_reusable_secs',
}


def xhttp_range(v):
    '''Нормализация диапазона в строку "min-max". Принимает int/float/str/[a,b].
    Одиночное число -> "N-N"; дробную часть отбрасываем.'''
    if isinstance(v, (list, tuple)) and len(v) == 2:
        return '{}-{}'.format(int(v[0]), int(v[1]))
    if isinstance(v, bool):          # bool — подтип int, не путать с числом
        return str(v)
    if isinstance(v, float):
        v = int(v)
    s = str(v)
    return s if '-' in s else '{}-{}'.format(s, s)


def xhttp_transport(netquery):
    '''Собрать transport-объект XHTTP из netquery (значения уже percent-декодированы
    parse_qs). Ключи extra (urlencoded-JSON) мёржатся поверх query с приоритетом.'''
    params = dict(netquery)
    extra = netquery.get('extra')
    if extra:
        parsed = None
        try:
            parsed = json.loads(extra)
        except Exception:
            try:                     # fallback: провайдер двойным энкодит extra
                parsed = json.loads(urllib.parse.unquote(extra))
            except Exception:
                parsed = None
        if isinstance(parsed, dict):
            params.update(parsed)    # ключи extra перебивают query

    # path: срезать хвостовой ?-суффикс, дефолт '/', хвостовой слэш сохраняем
    path = (params.get('path') or '/').split('?', 1)[0] or '/'
    t = {'type': 'xhttp', 'path': path}
    if params.get('host'):
        t['host'] = params['host']
    if params.get('mode'):
        t['mode'] = params['mode']

    for src, dst in _XHTTP_STRMAP.items():
        if params.get(src) not in (None, ''):
            t[dst] = params[src]

    if str(params.get('noGRPCHeader', '')).lower() in ('1', 'true'):
        t['no_grpc_header'] = True
    if str(params.get('xPaddingObfsMode', '')).lower() in ('1', 'true'):
        t['x_padding_obfs_mode'] = True

    for src, dst in (('scMaxEachPostBytes', 'sc_max_each_post_bytes'),
                     ('scMinPostsIntervalMs', 'sc_min_posts_interval_ms')):
        if params.get(src) not in (None, ''):
            t[dst] = xhttp_range(params[src])

    # xmux — вложенный объект (обычно приходит только в extra). Ключи могут быть
    # camelCase (MEDVED/puxvpn) — маппим в snake_case. h_keep_alive_period —
    # int64 (не диапазон!): из "0-0" берём первое число.
    xmux = params.get('xmux')
    if isinstance(xmux, dict) and xmux:
        out = {}
        for k, val in xmux.items():
            nk = _XHTTP_XMUX_MAP.get(k, k)      # camelCase -> snake_case
            if nk == 'h_keep_alive_period':
                s = str(val).strip()
                try:
                    # диапазон "a-b" -> первое число; но "-1" (<0=выкл) — само число
                    if '-' in s and not s.startswith('-'):
                        out[nk] = int(s.split('-')[0])
                    else:
                        out[nk] = int(float(s))
                except (TypeError, ValueError):
                    continue
            else:
                out[nk] = xhttp_range(val)
        if out:
            t['xmux'] = out
    return t
