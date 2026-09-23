"""subscribe — обёртка «fetch → переименование/группы» (strangler, REFACTOR-MODULES.md Ф1).

Загрузка подписок — пакет nodes_fetch (тот же raw-конверт, что пишет `python -m nodes_fetch`).
Переименование, фильтры, CRC и группы пока живут здесь (переедут в nodes_config на Ф2).
Формат providers.json — прежний (v1): фильтры exclude_protocol / ex-node-name применяются
к raw-нодам здесь же, до переименования, как и раньше.
"""
import argparse
import json
import os
import re
import shutil
from datetime import datetime

import groups
import tool
from nodes_fetch import fetch as nodes_fetch

providers = None
# Каталог config/ активной генерации — база для относительных путей подписок (folder/file).
# Выставляется в __main__ (по умолчанию — config/ этого проекта, либо --config-dir).
active_config_dir = None

# Пользовательские конфиги — в папке config/ в корне репозитория.
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config')
USER_NODES_PATH = os.path.join(_CONFIG_DIR, 'user_nodes.json')
PROVIDERS_PATH = os.path.join(_CONFIG_DIR, 'providers.json')

# Схема share-ссылки (providers.exclude_protocol) → тип sing-box outbound: фильтр v1 работал
# по схеме ссылки до парсинга, теперь fetch парсит всё, а отсев идёт по типу узла.
_SCHEME_TYPES = {'ss': 'shadowsocks', 'ssr': 'shadowsocksr', 'hy2': 'hysteria2',
                 'wg': 'wireguard', 'wireguard': 'wireguard', 'socks5': 'socks',
                 'http2': 'http', 'https': 'http'}


def load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def fetch_nodes(providers, base_dir):
    """Подписки → узлы в прежнем внутреннем виде (tag = исходное имя, _provider, _file_cc)."""
    normalized = nodes_fetch.load_providers(providers)
    raw = nodes_fetch.run(normalized, base_dir)
    excluded_types = {_SCHEME_TYPES.get(p.strip(), p.strip())
                      for p in (providers.get('exclude_protocol') or '').split(',') if p.strip()}
    ex_names = {sub['tag']: [x for x in re.split(r'[,\|]', sub['ex-node-name']) if x]
                for sub in providers['subscribes'] if sub.get('ex-node-name')}
    nodes = []
    for item in raw['nodes']:
        outbound = item['outbound']
        if outbound.get('type') in excluded_types:
            continue
        if any(name in item['title'] for name in ex_names.get(item['provider'], ())):
            continue
        node = {'tag': item['title'], **outbound, '_provider': item['provider']}
        if item.get('cc_hint'):
            node['_file_cc'] = item['cc_hint']
        nodes.append(node)
    return nodes


def load_user_nodes(path):
    # user-supplied nodes, copied to the output verbatim except for their tag
    if not os.path.exists(path):
        return []
    nodes = load_json(path)
    return nodes if isinstance(nodes, list) else []


def dedupe_nodes(nodelist):
    '''
    Final safety net for tag uniqueness. Byte-identical nodes that ended up with
    the same tag are collapsed to one. Two *different* nodes sharing a tag means a
    CRC32 collision (astronomically unlikely) and is raised as a hard error rather
    than silently emitting a config sing-box would reject for duplicate tags.
    '''
    seen = {}
    result = []
    for node in nodelist:
        tag = node['tag']
        if tag in seen:
            if tool.node_payload(node) == tool.node_payload(seen[tag]):
                continue                      # identical node listed twice -> keep one
            raise ValueError('duplicate tag {!r}: two different nodes share a CRC32'.format(tag))
        seen[tag] = node
        result.append(node)
    return result


def _match_label(title, labels):
    '''First label whose keyword appears in the node title (providers.json order),
    e.g. {"AI": ["Gemini","OpenAI"]} -> "AI". No match -> "".'''
    if not labels:
        return ''
    low = (title or '').lower()
    for label, keywords in labels.items():
        if any(kw.lower() in low for kw in keywords):
            return label
    return ''


def _protocol_excluded(node, exclude_tokens):
    '''True, если дескриптор протокола ноды (naming.node_protocol, пайп-токены)
    пересекается с exclude_tokens. Позволяет выкинуть транспорт-уровневые протоколы,
    которые exclude_protocol (по базовой share-ссылке) не различает: "xhttp" ловит
    vless|xhttp|tls, "wg" — wireguard/awg-эндпоинты.'''
    if not exclude_tokens:
        return False
    return bool(set(tool.node_protocol(node).split('|')) & exclude_tokens)


def finalize_nodes(sub_nodes, user_nodes, resolver_tag, exclude_countries=(), labels=None,
                   group_user_nodes=True, exclude_protocols=()):
    '''
    Rename every node, make tags unique, keep detour references valid and add the
    domain resolver to the subscription nodes. Returns the flat node list, each
    node carrying a '_meta' dict used by groups.build.

    group_user_nodes=False → ноды из user_nodes КОПИРУЮТСЯ в вывод как есть (без
    rename/группировки/CRC/exclude): у них нет '_meta', поэтому groups.build их не
    группирует, а свой тег/настройки сохраняются 1:1.

    exclude_protocols — токены дескриптора протокола (providers.exclude_node_protocols),
    ноды с любым из них выкидываются целиком (напр. ["xhttp","wg"] для клиентов на
    ванильном sing-box, не понимающих xhttp-транспорт и amneziawg).
    '''
    exclude_tokens = {p.strip().lower() for p in exclude_protocols if p and p.strip()}
    # drop XHTTP (plain vless / vmess) nodes and any exclude_node_protocols, before renaming
    sub_nodes = [n for n in sub_nodes
                 if tool.node_protocol(n) not in groups.UNGROUPED_PROTOCOLS
                 and not _protocol_excluded(n, exclude_tokens)]
    raw_users = []
    if group_user_nodes:
        user_nodes = [n for n in user_nodes
                      if tool.node_protocol(n) not in groups.UNGROUPED_PROTOCOLS
                      and not _protocol_excluded(n, exclude_tokens)]
    else:
        raw_users = user_nodes            # как есть, без какой-либо обработки
        user_nodes = []
    original = [node.get('tag') for node in sub_nodes]
    for node in sub_nodes:
        node['_label'] = _match_label(node.get('tag', ''), labels)  # из исходного названия
        node['_meta'] = tool.group_meta(node)
        node['tag'] = tool.custom_rename(node)
    add_domain_resolver(sub_nodes, resolver_tag)
    for node in user_nodes:
        node['_label'] = _match_label(node.get('tag', ''), labels)
        tool.rename_user_node(node)
    nodelist = sub_nodes + user_nodes
    # drop nodes of excluded countries entirely (they don't reach the output)
    exclude = {c.lower() for c in exclude_countries}
    if exclude:
        nodelist = [n for n in nodelist if not (n.get('_meta') and n['_meta']['country'] in exclude)]
    # append a CRC32 fingerprint of the node's own settings to its tag; this alone
    # makes tags unique, so no numbering is needed
    for node in nodelist:
        node['tag'] = '{} [{}]'.format(node['tag'], tool.content_crc32(node))
    nodelist = dedupe_nodes(nodelist)
    # keep detour pointing at the subscription node it referenced before renaming
    renamed = {old: node['tag'] for old, node in zip(original, sub_nodes)}
    for node in nodelist:
        if node.get('detour') in renamed:
            node['detour'] = renamed[node['detour']]
    return nodelist + raw_users          # raw_users — как есть (пусто, если group_user_nodes)


def add_domain_resolver(nodelist, resolver_tag):
    # point every outbound's server-domain resolution at the given DNS server tag.
    # WG-эндпоинты пропускаем: peer уже IP, резолвить нечего (и поле лишнее).
    for node in nodelist:
        if node.get('type') == 'wireguard':
            continue
        node['domain_resolver'] = resolver_tag


def save_config(path, nodes):
    try:
        # Сериализуем и проверяем ДО любых операций с целевым файлом.
        payload = json.dumps(nodes, indent=2, ensure_ascii=False)
        json.loads(payload)
        if providers.get('auto_backup') and os.path.exists(path):
            now = datetime.now().strftime('%Y%m%d%H%M%S')
            shutil.copy2(path, f'{path}.{now}.bak')   # копия, не переименование
        # Пишем во временный файл рядом и атомарно заменяем — при ошибке
        # старый nodes.json остаётся целым.
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        print(f"Nodes saved: \033[33m{path}\033[0m")
    except Exception as e:
        print(f"Error while saving the config file: {str(e)}")
        raise


# Helper that parses a CLI argument as JSON
def parse_json(value):
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        raise argparse.ArgumentTypeError(f"Invalid JSON: {value}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--temp_json_data', type=parse_json, help='inline providers JSON, overrides providers.json')
    parser.add_argument('--config-dir', dest='config_dir',
                        help='папка config (providers.json/user_nodes.json/groups_params.json) '
                             'для независимой генерации из другого набора конфигов')
    args = parser.parse_args()
    # Пути конфигов: по умолчанию config/ этого проекта; --config-dir переопределяет всё.
    providers_path = os.path.join(args.config_dir, 'providers.json') if args.config_dir else PROVIDERS_PATH
    user_nodes_path = os.path.join(args.config_dir, 'user_nodes.json') if args.config_dir else USER_NODES_PATH
    # база для относительных путей подписок (folder/file)
    active_config_dir = os.path.abspath(args.config_dir or _CONFIG_DIR)
    if args.config_dir:
        groups.load_params(args.config_dir)     # групповые параметры из той же папки
    temp_json_data = args.temp_json_data
    if temp_json_data and temp_json_data != '{}':
        providers = temp_json_data              # inline-JSON перебивает providers.json
    else:
        providers = load_json(providers_path)
    sub_nodes = fetch_nodes(providers, active_config_dir)
    user_nodes = load_user_nodes(user_nodes_path)
    resolver_tag = providers.get('domain_resolver_tag') or 'bootstrap'
    leaf_nodes = finalize_nodes(sub_nodes, user_nodes, resolver_tag,
                                providers.get('exclude_countries', []),
                                providers.get('labels') or {},
                                group_user_nodes=not groups.raw_user_nodes(),
                                exclude_protocols=providers.get('exclude_node_protocols', []))
    # build the selector / urltest groups, then drop the grouping metadata
    group_outbounds = groups.build(leaf_nodes)
    for node in leaf_nodes:
        node.pop('_meta', None)
        node.pop('_provider', None)
        node.pop('_label', None)
        node.pop('_file_cc', None)
    # wrap as a sing-box config fragment so it can be merged into the main config
    # via: sing-box merge <output> -c config.json -c nodes.json.
    # В sing-box 1.14 wireguard-outbound удалён: WG-узлы живут в endpoints[]
    # (type:"wireguard"). Их теги остаются в едином пространстве с outbounds, поэтому
    # селекторы/urltest по-прежнему на них ссылаются.
    wg_eps = [n for n in leaf_nodes if n.get('type') == 'wireguard']
    non_wg = [n for n in leaf_nodes if n.get('type') != 'wireguard']
    final_config = {'outbounds': non_wg + group_outbounds}
    if wg_eps:
        final_config['endpoints'] = wg_eps
    save_config(providers["save_config_path"], final_config)
