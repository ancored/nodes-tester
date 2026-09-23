"""Парсер AmneziaWG (AWG) — файлы .conf (WireGuard-INI с AWG-полями).

В отличие от остальных парсеров, вход — не share-ссылка (URL), а файл .conf.
Экспортирует parse_file(path) -> один узел sing-box типа "wireguard" (в 1.14 —
endpoint). Диспетчер папок (main.process_subscribes, type=="folder") вызывает
parse_file по каждому файлу каталога; load_dir — самостоятельный обход каталога
(на случай прямого вызова/тестов).

Ручной line-parser надёжнее configparser: base64 с '=' в значениях, экзотический
I1 с '=' внутри байт-блоков, потенциально >1 peer. Split — по ПЕРВОМУ '='.

Карта полей .conf (PascalCase) -> endpoint (snake_case) — см. AWG-PARSER-INSTRUCTION.
Значения нормализуются: диапазон "min-max" оставляем строкой, чистое число -> int,
ключи/I1 -> строка как есть. DNS игнорируется (централизован). i1..i5 — дословно.
"""
import os, re


def _val(v):
    """min-max -> строка; чистое число -> int; прочее -> строка как есть."""
    v = v.strip()
    if re.fullmatch(r'\d+-\d+', v):   # диапазон -> строка
        return v
    if re.fullmatch(r'\d+', v):       # целое
        return int(v)
    return v


_IFACE_INT_OR_RANGE = {
    'Jc': 'jc', 'Jmin': 'jmin', 'Jmax': 'jmax',
    'S1': 's1', 'S2': 's2', 'S3': 's3', 'S4': 's4',
    'H1': 'h1', 'H2': 'h2', 'H3': 'h3', 'H4': 'h4',
    'RekeyAfterTime': 'rekey_after_time', 'RekeyTimeout': 'rekey_timeout',
    'RejectAfterTime': 'reject_after_time', 'KeepaliveTimeout': 'keepalive_timeout',
    'MaxHandshakeAttempts': 'max_handshake_attempts',
    'ContentPaddingAddition': 'content_padding_addition',
}
_IFACE_STR = {
    'HeaderProtectionKey': 'header_protection_key',
    'I1': 'i1', 'I2': 'i2', 'I3': 'i3', 'I4': 'i4', 'I5': 'i5',
}



def _addr_list(v):
    out = []
    for part in v.split(','):
        p = part.strip()
        if not p:
            continue
        if '/' not in p:
            p += '/128' if ':' in p else '/32'
        out.append(p)
    return out


def parse_file(path):
    section = None
    node = {'type': 'wireguard', 'peers': [{}]}
    peer = node['peers'][0]
    peer.setdefault('allowed_ips', ['0.0.0.0/0'])
    peer.setdefault('persistent_keepalive_interval', 30)
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('['):
                section = line.strip('[]').lower()   # 'interface' | 'peer'
                continue
            if '=' not in line:
                continue
            key, val = line.split('=', 1)             # split по ПЕРВОМУ '=' (base64!)
            key, val = key.strip(), val.strip()
            if section == 'interface':
                if key == 'Address':
                    node['address'] = _addr_list(val)
                elif key == 'PrivateKey':
                    node['private_key'] = val
                elif key == 'MTU':
                    node['mtu'] = int(val)
                elif key == 'DNS':
                    pass                              # игнор — DNS централизован
                elif key in _IFACE_INT_OR_RANGE:
                    node[_IFACE_INT_OR_RANGE[key]] = _val(val)
                elif key in _IFACE_STR:
                    node[_IFACE_STR[key]] = val        # i1 — дословно
            elif section == 'peer':
                if key == 'Endpoint':
                    host, port = val.rsplit(':', 1)
                    peer['address'] = re.sub(r'[\[\]]', '', host)
                    peer['port'] = int(port)
                elif key == 'PublicKey':
                    peer['public_key'] = val
                elif key == 'PresharedKey':
                    peer['pre_shared_key'] = val
                elif key == 'AllowedIPs':
                    peer['allowed_ips'] = [x.strip() for x in val.split(',') if x.strip()]
                elif key == 'PersistentKeepalive':
                    peer['persistent_keepalive_interval'] = _val(val)
                elif key == 'Reserved':
                    peer['reserved'] = [int(x) for x in val.split(',')]
    if not peer.get('public_key'):
        raise ValueError('no [Peer] public_key')     # битый файл -> пропуск с логом
    # У .conf нет ни флага, ни названия страны — cc известен ТОЛЬКО из имени файла.
    # Отдаём его как подсказку в '_file_cc'; group_meta берёт страну по приоритету
    # флаг → название страны в тексте → _file_cc (см. nodes_config: group_meta). Тег — только для
    # отображения, custom_rename всё равно соберёт {provider}-wg-{cc}-out.
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    node['_file_cc'] = stem if (len(stem) == 2 and stem.isalpha()) else ''
    node['tag'] = f"AWG | {stem.upper()}"
    return node


def load_dir(awg_dir):
    nodes = []
    if not os.path.isdir(awg_dir):
        return nodes
    for fn in sorted(os.listdir(awg_dir)):
        if fn.lower().endswith('.conf'):
            try:
                nodes.append(parse_file(os.path.join(awg_dir, fn)))
            except Exception as e:
                print(f"  [awg] пропущен {fn} ({e.__class__.__name__}: {e})")
    return nodes
