'''
Build sing-box selector / urltest groups from the renamed node list.

Every node carries a '_meta' dict {provider, protocol, country, label} (or None for an
unparsed user node).

Группы задаются в groups_params: список `groups` (порядок = порядок в выводе), у группы —
имя, условия отбора нод и флаги. Условия объединяются через И, внутри условия — ИЛИ:

    {"name": "ai", "enabled": true, "in_global": false, "fallback": true,
     "match": {"regions": ["eu", "us"], "labels": ["AI"], "exclude_countries": ["tr"]}}

    countries / regions / labels                    — нода должна подойти хотя бы под одно;
    exclude_countries / exclude_regions / exclude_labels — не должна подходить ни под одно;
    пустой match — все ноды.

Регион — именованный список стран. Встроенные: eu (европейские страны), us, ru и other
(всё, что не попало в eu/us/ru); их можно переопределить и добавить свои в `regions`.
Нода может попасть в несколько групп.

Для группы создаются "{name}-auto-out" (selector, default = failsafe) и
"{name}-auto-out-failsafe" (urltest) с нодами — прямыми членами. "global-auto-out"
собирает селекторы групп с in_global. Тестовый селектор "nodes-tester" (если включён) —
плоский список нод, попавших хотя бы в одну включённую группу.

Без `groups` действует прежняя схема: eu, us, ru, other по регионам, обязательные —
из emit.ensure_regions (по умолчанию eu, us, other).

Plain "vless" / "vmess" nodes (no transport, no reality — e.g. XHTTP) are dropped
from the output entirely by build.finalize_nodes before this runs.

Nodes whose name could not be parsed ('_meta' is None) and nodes with an undetermined
country ('_meta' country == 'undef') stay in the output but are never grouped.
'''
from collections import OrderedDict

from naming.regions import EUROPE

_OUT = '-out'
_FAILSAFE = '-failsafe'
# Protocols that mark an ungrouped node (bare vless == XHTTP, bare vmess).
# These nodes are dropped from the output entirely (see build.finalize_nodes).
UNGROUPED_PROTOCOLS = {'vless', 'vmess'}
LEGACY_REGIONS = ('eu', 'us', 'ru', 'other')
DEFAULT_ENSURE_REGIONS = ['eu', 'us', 'other']
BUILTIN_REGIONS = {'eu': sorted(EUROPE), 'us': ['us'], 'ru': ['ru']}
MATCH_KEYS = ('countries', 'regions', 'labels',
              'exclude_countries', 'exclude_regions', 'exclude_labels')


def _selector(tag, members, default, params):
    return {'tag': tag, 'type': 'selector', 'default': default,
            'outbounds': members, **params}


def _urltest(tag, members, params):
    return {'tag': tag, 'type': 'urltest', 'outbounds': members, **params}


def legacy_groups(emit):
    '''Группы прежней схемы: eu, us, ru, other; обязательные — emit.ensure_regions.'''
    ensure = emit.get('ensure_regions', DEFAULT_ENSURE_REGIONS)
    return [{'name': r, 'match': {'regions': [r]}, 'fallback': r in ensure}
            for r in LEGACY_REGIONS]


_BUILTIN_MAP = {name: set(ccs) for name, ccs in BUILTIN_REGIONS.items()}


def resolve_regions(regions=None):
    '''{регион: множество стран}: встроенные eu/us/ru с переопределениями + свои.
    other не хранится списком — это страны вне eu/us/ru, если other не задан явно.'''
    out = {name: set(ccs) for name, ccs in BUILTIN_REGIONS.items()}
    for name, ccs in (regions or {}).items():
        out[name] = {c.lower() for c in ccs}
    return out


def node_regions(country, regions):
    '''Регионы страны по resolve_regions (включая неявный other).'''
    cc = (country or '').split('(')[0].lower()     # 'nl(n)' -> 'nl'
    found = {name for name, ccs in regions.items() if cc in ccs}
    if 'other' not in regions and not found & {'eu', 'us', 'ru'}:
        found.add('other')
    return found


def matches(meta, match, regions):
    '''Подходит ли нода под условия группы (И между ключами, ИЛИ внутри).'''
    cc = (meta.get('country') or '').split('(')[0].lower()
    label = meta.get('label') or ''
    nregions = node_regions(cc, regions)
    checks = (('countries', {cc}), ('regions', nregions), ('labels', {label} - {''}))
    for key, have in checks:
        want = {str(v).lower() if key != 'labels' else str(v) for v in match.get(key) or []}
        if want and not want & have:
            return False
        excl = {str(v).lower() if key != 'labels' else str(v)
                for v in match.get('exclude_' + key) or []}
        if excl & have:
            return False
    return True


def build(nodelist, selector=None, urltest=None, emit=None, groups=None, regions=None,
          log=print):
    '''Return the list of selector / urltest outbounds for the given nodes.
    selector / urltest — доп. поля групп этого типа (interval, tolerance, …);
    emit — {nodes_tester (деф. True), global_failsafe (деф. False), ensure_regions};
    groups / regions — настраиваемые группы и регионы (None — прежняя схема).'''
    selector = selector or {}
    urltest = urltest or {}
    emit = emit or {}
    defs = legacy_groups(emit) if groups is None else groups
    region_map = resolve_regions(regions)

    eligible = []                                 # (tag, meta) — ноды, пригодные для групп
    for node in nodelist:
        meta = node.get('_meta')
        if meta is None:                          # name could not be parsed: the node
            continue                              # stays in the output but is never grouped
        if meta['country'] == 'undef':            # страна не определена — вне групп
            continue
        if meta['protocol'] in UNGROUPED_PROTOCOLS:   # XHTTP and the like are not grouped
            continue
        eligible.append((node['tag'], meta))
    # Общий список (фолбэк, nodes-tester) — по регионам eu/us/ru/other в порядке их первого
    # появления, внутри региона — в исходном порядке (так было до настраиваемых групп).
    by_region = OrderedDict()
    for tag, meta in eligible:
        coarse = next((r for r in LEGACY_REGIONS[:3]
                       if r in node_regions(meta['country'], _BUILTIN_MAP)), 'other')
        by_region.setdefault(coarse, []).append(tag)
    all_nodes = [tag for tags in by_region.values() for tag in tags]

    # Мало нод: обязательная группа (fallback, в прежней схеме — ensure_regions) без нод
    # всё равно должна существовать — иначе основной конфиг sing-box, ссылающийся на неё
    # по имени, не стартует ("outbound not found"). Пустую заполняем всеми нодами.
    group_auto = OrderedDict()                    # name -> auto selector tag
    in_global, grouped = [], set()
    group_selectors, group_urltests = [], []
    for g in defs:
        if not g.get('enabled', True):
            continue
        name = g['name']
        members = [tag for tag, meta in eligible if matches(meta, g.get('match') or {}, region_map)]
        grouped.update(members)
        if not members:
            if not g.get('fallback', True) or not all_nodes:
                continue
            members = all_nodes
            log(f"  [groups] группа '{name}': нод нет — auto-селектор заполнен "
                f"фолбэком из {len(all_nodes)} нод (config sing-box не сломается)")
        auto = '{}-auto{}'.format(name, _OUT)
        auto_fs = auto + _FAILSAFE
        group_auto[name] = auto
        if g.get('in_global', True):
            in_global.append(auto)
        group_selectors.append(_selector(auto, [auto_fs] + members, auto_fs, selector))
        group_urltests.append(_urltest(auto_fs, list(members), urltest))

    # Плоский тестовый селектор: все ноды, попавшие в группы, — прямыми членами.
    testers = []
    tested = [tag for tag in all_nodes if tag in grouped]
    if emit.get('nodes_tester', True) and tested:
        testers.append(_selector('nodes-tester', tested, tested[0], selector))

    top = []
    if in_global:
        if emit.get('global_failsafe', False):
            # global-auto-out = [global-failsafe] + auto групп; default — failsafe.
            # global failsafe — urltest над failsafe'ами групп (как у самих групп).
            gfs = 'global-auto-out' + _FAILSAFE
            top.append(_selector('global-auto-out', [gfs] + in_global, gfs, selector))
            group_urltests.append(_urltest(gfs, [t + _FAILSAFE for t in in_global], urltest))
        else:
            default = group_auto.get('eu') if group_auto.get('eu') in in_global else in_global[0]
            top.append(_selector('global-auto-out', in_global, default, selector))
    top += group_selectors + testers

    return top + group_urltests
