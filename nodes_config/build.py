"""Стадия nodes_config: raw_nodes (+ groups_params, + user_nodes) → фрагмент nodes.json.

Порядок операций перенесён из subscribe/main.py (finalize_nodes) без изменений — от него
зависят теги/CRC (golden-инвариант):
  1. raw → рабочие узлы (tag = исходный title, _provider, _file_cc);
  2. фильтры по raw: exclude_types, exclude_names (подстрока исходного title);
  3. голые vless/vmess (UNGROUPED) и exclude_protocols (токены naming.node_protocol);
  4. labels (по title) → group_meta → custom_rename; user_nodes → rename_user_node;
  5. domain_resolver (кроме wireguard) → exclude_countries → " [CRC]" → dedupe → detour-ремап;
  6. groups.build → {outbounds: ноды+группы, endpoints: wireguard}.
"""

from collections import Counter

from naming import coarse_region, content_crc32, node_payload, node_protocol

from . import groups
from .rename import custom_rename, group_meta, rename_user_node

_META_KEYS = ("_meta", "_provider", "_label", "_file_cc")


def raw_to_nodes(raws):
    """Список raw-конвертов → рабочие узлы в порядке raw (конверты — по порядку)."""
    nodes = []
    for raw in raws:
        for item in raw["nodes"]:
            node = {"tag": item["title"], **item["outbound"], "_provider": item["provider"]}
            if item.get("cc_hint"):
                node["_file_cc"] = item["cc_hint"]
            nodes.append(node)
    return nodes


def _match_label(title, labels):
    '''First label whose keyword appears in the node title (groups_params order),
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
    пересекается с exclude_tokens: "xhttp" ловит vless|xhttp|tls, "wg" — wireguard/awg.'''
    if not exclude_tokens:
        return False
    return bool(set(node_protocol(node).split('|')) & exclude_tokens)


def dedupe_nodes(nodelist, dropped):
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
            if node_payload(node) == node_payload(seen[tag]):
                dropped['duplicate'] += 1
                continue                      # identical node listed twice -> keep one
            raise ValueError('duplicate tag {!r}: two different nodes share a CRC32'.format(tag))
        seen[tag] = node
        result.append(node)
    return result


def add_domain_resolver(nodelist, resolver_tag):
    # point every outbound's server-domain resolution at the given DNS server tag.
    # WG-эндпоинты пропускаем: peer уже IP, резолвить нечего (и поле лишнее).
    for node in nodelist:
        if node.get('type') == 'wireguard':
            continue
        node['domain_resolver'] = resolver_tag


def filter_raw_nodes(nodes, filters, dropped):
    """Фильтры, которым нужен исходный вид ноды: тип outbound и подстрока исходного имени."""
    types = set(filters["exclude_types"])
    names = filters["exclude_names"]
    common = names.get("*", [])
    out = []
    for node in nodes:
        if node.get("type") in types:
            dropped["type"] += 1
            continue
        title = node.get("tag", "")
        if any(s and s in title for s in common + names.get(node["_provider"], [])):
            dropped["name"] += 1
            continue
        out.append(node)
    return out


def finalize_nodes(sub_nodes, user_nodes, params, dropped):
    '''
    Rename every node, make tags unique, keep detour references valid and add the
    domain resolver to the subscription nodes. Returns the flat node list, each
    node carrying a '_meta' dict used by groups.build.

    raw_user_nodes=True → ноды из user_nodes КОПИРУЮТСЯ в вывод как есть (без
    rename/группировки/CRC/exclude): у них нет '_meta', поэтому groups.build их не
    группирует, а свой тег/настройки сохраняются 1:1.
    '''
    filters, rename = params["filters"], params["rename"]
    labels = rename["labels"]
    exclude_tokens = {p.strip().lower() for p in filters["exclude_protocols"] if p and p.strip()}

    def keep(node):
        if node_protocol(node) in groups.UNGROUPED_PROTOCOLS:
            dropped['ungrouped_protocol'] += 1
            return False
        if _protocol_excluded(node, exclude_tokens):
            dropped['protocol'] += 1
            return False
        return True

    # drop XHTTP (plain vless / vmess) nodes and any exclude_protocols, before renaming
    sub_nodes = [n for n in sub_nodes if keep(n)]
    raw_users = []
    if not params["raw_user_nodes"]:
        user_nodes = [n for n in user_nodes if keep(n)]
    else:
        raw_users = user_nodes            # как есть, без какой-либо обработки
        user_nodes = []
    original = [node.get('tag') for node in sub_nodes]
    for node in sub_nodes:
        node['_label'] = _match_label(node.get('tag', ''), labels)  # из исходного названия
        node['_meta'] = group_meta(node)
        node['tag'] = custom_rename(node)
    add_domain_resolver(sub_nodes, rename["domain_resolver_tag"])
    for node in user_nodes:
        node['_label'] = _match_label(node.get('tag', ''), labels)
        rename_user_node(node)
    nodelist = sub_nodes + user_nodes
    # drop nodes of excluded countries entirely (they don't reach the output)
    exclude = {c.lower() for c in filters["exclude_countries"]}
    if exclude:
        before = len(nodelist)
        nodelist = [n for n in nodelist if not (n.get('_meta') and n['_meta']['country'] in exclude)]
        dropped['country'] += before - len(nodelist)
    # append a CRC32 fingerprint of the node's own settings to its tag; this alone
    # makes tags unique, so no numbering is needed
    for node in nodelist:
        node['tag'] = '{} [{}]'.format(node['tag'], content_crc32(node))
    nodelist = dedupe_nodes(nodelist, dropped)
    # keep detour pointing at the subscription node it referenced before renaming
    renamed = {old: node['tag'] for old, node in zip(original, sub_nodes)}
    for node in nodelist:
        if node.get('detour') in renamed:
            node['detour'] = renamed[node['detour']]
    return nodelist + raw_users          # raw_users — как есть (пусто, если группируем)


def _region_counts(leaf_nodes):
    counts = Counter()
    for node in leaf_nodes:
        meta = node.get('_meta')
        if meta and meta['country'] != 'undef' and meta['protocol'] not in groups.UNGROUPED_PROTOCOLS:
            counts[coarse_region(meta['country'])] += 1
    return dict(counts)


def build(raws, params, user_nodes=(), log=print):
    """raw-конверты + нормализованные params (params.load) + user_nodes → (фрагмент, отчёт).
    Фрагмент — {"outbounds": […], "endpoints": […]?} для склейки с базой."""
    dropped = Counter({k: 0 for k in ("type", "name", "ungrouped_protocol", "protocol",
                                       "country", "duplicate")})
    sub_nodes = raw_to_nodes(raws)
    total = len(sub_nodes)
    sub_nodes = filter_raw_nodes(sub_nodes, params["filters"], dropped)
    user_nodes = [dict(n) for n in user_nodes]
    leaf_nodes = finalize_nodes(sub_nodes, user_nodes, params, dropped)
    regions = _region_counts(leaf_nodes)
    group_outbounds = groups.build(leaf_nodes, selector=params["selector"],
                                   urltest=params["urltest"], emit=params["emit"],
                                   groups=params["groups"], regions=params["regions"], log=log)
    for node in leaf_nodes:
        for key in _META_KEYS:
            node.pop(key, None)
    # В sing-box 1.14 wireguard-outbound удалён: WG-узлы живут в endpoints[]
    # (type:"wireguard"). Их теги остаются в едином пространстве с outbounds, поэтому
    # селекторы/urltest по-прежнему на них ссылаются.
    wg_eps = [n for n in leaf_nodes if n.get('type') == 'wireguard']
    non_wg = [n for n in leaf_nodes if n.get('type') != 'wireguard']
    fragment = {'outbounds': non_wg + group_outbounds}
    if wg_eps:
        fragment['endpoints'] = wg_eps
    report = {"raw_nodes": total, "user_nodes": len(user_nodes), "leaf_nodes": len(leaf_nodes),
              "groups": len(group_outbounds), "regions": regions,
              "dropped": {k: v for k, v in dropped.items() if v}}
    return fragment, report


def tags(fragment):
    """Все теги фрагмента (ноды, группы, endpoints) — для diff между сборками."""
    return [o["tag"] for o in fragment.get("outbounds", []) + fragment.get("endpoints", [])]
