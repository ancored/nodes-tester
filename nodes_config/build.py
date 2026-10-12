"""Стадия nodes_config: raw ветви + config.json → фрагмент nodes.json (ноды и группы sing-box).

Порядок операций:
  1. raw → рабочие ноды (tag = исходное имя в подписке, _provider);
  2. фильтры против падения sing-box: exclude_types, exclude_protocols (токены
     naming.node_protocol), голые vless/vmess (XHTTP без транспорта); exclude_names
     подписки — подстроки исходного имени;
  3. имя {provider}-{protocol}-out [node_crc.cred_crc], domain_resolver (кроме wireguard);
  4. одинаковый доступ из двух подписок — одна нода; detour — на новое имя;
  5. группы (nodes_config.groups) → {outbounds: ноды+группы, endpoints: wireguard}.

Страну сборка не определяет и ноды по ней не делит: в каждой группе все ноды, а какую
активировать, решает тестер по проверкам группы.
"""

from collections import Counter

from naming import node_key, node_payload, node_protocol

from . import groups

# Голые vless/vmess без распознанного транспорта и Reality (XHTTP и т. п.) не собираются.
UNGROUPED_PROTOCOLS = {'vless', 'vmess'}
_META_KEYS = ("_provider", "_key")


def raw_to_nodes(raws):
    """Список raw-конвертов → рабочие ноды в порядке raw (конверты — по порядку)."""
    nodes = []
    for raw in raws:
        for item in raw["nodes"]:
            nodes.append({"tag": item["title"], **item["outbound"], "_provider": item["provider"]})
    return nodes


def _excluded(node, branch, exclude_names, dropped):
    '''Причина, по которой ноду нельзя отдавать sing-box, или None.'''
    if node.get("type") in branch.exclude_types:
        dropped["type"] += 1
        return True
    title = node.get("tag", "")
    if any(s and s in title for s in exclude_names.get(node["_provider"], ())):
        dropped["name"] += 1
        return True
    protocol = node_protocol(node)
    if protocol in UNGROUPED_PROTOCOLS:
        dropped["ungrouped_protocol"] += 1
        return True
    if set(protocol.split("|")) & set(branch.exclude_protocols):
        dropped["protocol"] += 1
        return True
    return False


def dedupe_nodes(nodelist, dropped):
    '''
    Одинаковый доступ (тот же ключ node_crc.cred_crc) из двух подписок — одна нода: первая
    остаётся, остальные отбрасываются. Разные настройки при одном ключе — коллизия CRC32
    (практически невозможна): ошибка, а не тихо испорченный конфиг.
    '''
    seen = {}
    result = []
    for node in nodelist:
        key = node['_key']
        if key in seen:
            if node_payload(node) != node_payload(seen[key]):
                raise ValueError('ключ {} у двух разных нод: коллизия CRC32'.format(key))
            dropped['duplicate'] += 1
            continue
        seen[key] = node
        result.append(node)
    return result


def finalize_nodes(nodes, branch, exclude_names, dropped):
    '''Отфильтровать, переименовать и схлопнуть одинаковые доступы; detour — на новые имена.'''
    nodes = [n for n in nodes if not _excluded(n, branch, exclude_names, dropped)]
    original = [node.get('tag') for node in nodes]
    for node in nodes:
        node['tag'] = '{}-{}-out'.format(node['_provider'], node_protocol(node))
        if node.get('type') != 'wireguard':        # у WG peer — уже IP, резолвить нечего
            node['domain_resolver'] = branch.domain_resolver
    # ключ доступа в теге делает теги уникальными без нумерации
    for node in nodes:
        node['_key'] = node_key(node)
        node['tag'] = '{} [{}]'.format(node['tag'], node['_key'])
    kept = dedupe_nodes(nodes, dropped)
    # detour указывает на ту же ноду, что до переименования; на месте отброшенного
    # дубля — оставленная нода с тем же ключом
    new_tag = {node['_key']: node['tag'] for node in kept}
    renamed = {old: new_tag[node['_key']] for old, node in zip(original, nodes)}
    for node in kept:
        if node.get('detour') in renamed:
            node['detour'] = renamed[node['detour']]
    return kept


def build(raws, cfg, branch_name="router", log=print):
    """raw-конверты ветви + Config (nodes_tester.config) → (фрагмент, отчёт).
    Фрагмент — {"outbounds": […], "endpoints": […]?} для склейки с базой."""
    branch = getattr(cfg.pipeline, branch_name)
    exclude_names = {s["tag"]: s.get("exclude_names") or [] for s in cfg.subscriptions}
    dropped = Counter({k: 0 for k in ("type", "name", "ungrouped_protocol", "protocol", "duplicate")})
    nodes = raw_to_nodes(raws)
    total = len(nodes)
    leaf_nodes = finalize_nodes(nodes, branch, exclude_names, dropped)
    tags = [n['tag'] for n in leaf_nodes]
    if branch_name == "router":
        group_outbounds = groups.router_groups(tags, cfg.groups, branch.selector, branch.urltest)
    else:
        group_outbounds = groups.client_groups(tags, branch.selector, branch.urltest)
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
    if not leaf_nodes:
        log("  [groups] нод нет: группы ведут на запасные outbound-ы")
    report = {"raw_nodes": total, "leaf_nodes": len(leaf_nodes), "groups": len(group_outbounds),
              "dropped": {k: v for k, v in dropped.items() if v}}
    return fragment, report


def tags(fragment):
    """Все теги фрагмента (ноды, группы, endpoints) — для diff между сборками."""
    return [o["tag"] for o in fragment.get("outbounds", []) + fragment.get("endpoints", [])]
