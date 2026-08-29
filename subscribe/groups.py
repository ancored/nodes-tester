'''
Build sing-box selector / urltest groups from the renamed node list.

Every node carries a '_meta' dict {provider, protocol, country} (or None for an
unparsed user node). Nodes are classified into regions by country:

    ru            -> ru
    us            -> us
    a European cc -> eu
    anything else -> other

Leaf groups collect nodes as:
    eu / other : by provider AND protocol   -> "eu-LUNA-vless(reality)-out"
    us / ru    : by provider only           -> "us-LUNA-out"

Each selector gets a matching "-failsafe" urltest holding the same members and
used as its default. Region "-auto-out" selectors gather the leaf groups, and a top
"global-auto-out" selector gathers the region groups.

Plain "vless" / "vmess" nodes (no transport, no reality — e.g. XHTTP) are dropped
from the output entirely by main.finalize_nodes before this runs.

Nodes whose name could not be parsed (no flag emoji / no Provider-CC form, so
'_meta' is None) stay in the output with their original tag but are never placed
into any group. Nodes with an undetermined country ('_meta' country == 'undef')
are likewise left ungrouped (they must NOT fall into the 'other' region, which is
reserved for real non-eu/us/ru countries).
'''
import json
import os
from collections import OrderedDict

# Per-type group parameters (interval / tolerance / interrupt_exist_connections …)
# live in groups_params.json next to this module, so they can be tuned without
# touching the code. Keys: "selector" and "urltest".
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корень репо
_PARAMS_PATH = os.path.join(_ROOT, 'config', 'groups_params.json')
_PARAMS = {}
_SELECTOR_PARAMS = {}
_URLTEST_PARAMS = {}
# _EMIT.nodes_tester — создавать ли тестовые селекторы nodes-tester +
# {region}-nodes-tester (нужны тестеру в режиме by_selector).
_EMIT = {}
# raw_user_nodes: true → ноды из user_nodes.json НЕ сшиваются в группы, а копируются
# в вывод как есть (без rename/группировки/CRC). По умолчанию false.
_RAW_USER_NODES = False


def load_params(config_dir=None):
    '''(Пере)загрузить групповые параметры из groups_params.json. config_dir —
    альтернативная папка config (для отдельной генерации через --config-dir).
    Файл опционален: если отсутствует — параметры пустые (emit.nodes_tester=True).'''
    global _PARAMS, _SELECTOR_PARAMS, _URLTEST_PARAMS, _EMIT, _RAW_USER_NODES
    path = os.path.join(config_dir, 'groups_params.json') if config_dir else _PARAMS_PATH
    try:
        with open(path, encoding='utf-8') as f:
            _PARAMS = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"  [groups] groups_params.json не прочитан ({path}): "
              f"{exc.__class__.__name__} — пустые параметры")
        _PARAMS = {}
    _SELECTOR_PARAMS = _PARAMS.get('selector', {})
    _URLTEST_PARAMS = _PARAMS.get('urltest', {})
    _EMIT = _PARAMS.get('emit', {})
    _RAW_USER_NODES = bool(_PARAMS.get('raw_user_nodes', False))


def raw_user_nodes():
    '''Копировать user-ноды как есть (не группировать)? — из groups_params.raw_user_nodes.'''
    return _RAW_USER_NODES


load_params()   # по умолчанию — из config/ этого проекта

_OUT = '-out'
_FAILSAFE = '-failsafe'
# Protocols that mark an ungrouped node (bare vless == XHTTP, bare vmess).
# These nodes are dropped from the output entirely (see main.finalize_nodes).
UNGROUPED_PROTOCOLS = {'vless', 'vmess'}
# Region order used wherever regions are listed together
_REGION_ORDER = ('eu', 'us', 'ru', 'other')
# Regions grouped by provider + protocol (the rest are grouped by provider only)
_BY_PROVIDER_AND_PROTOCOL = ('eu', 'other')

# Коарс-регион — из общего naming (та же логика, что у тестера).
from naming import coarse_region as _region


def _selector(tag, members, default):
    return {'tag': tag, 'type': 'selector', 'default': default,
            'outbounds': members, **_SELECTOR_PARAMS}


def _urltest(tag, members):
    return {'tag': tag, 'type': 'urltest', 'outbounds': members, **_URLTEST_PARAMS}


def build(nodelist):
    '''Return the list of selector / urltest outbounds for the given nodes.'''
    leaf = OrderedDict()             # leaf_tag -> [member node tags]
    region_leaves = OrderedDict()    # region -> [leaf_tag, ...]

    for node in nodelist:
        meta = node.get('_meta')
        if meta is None:                          # name could not be parsed: the node
            continue                              # stays in the output but is never grouped
        if meta['country'] == 'undef':            # страна не определена — НЕ в 'other',
            continue                              # нода остаётся в выводе, но вне групп
        protocol = meta['protocol']
        if protocol in UNGROUPED_PROTOCOLS:       # XHTTP and the like are not grouped
            continue
        region = _region(meta['country'])
        provider = meta['provider']
        if region in _BY_PROVIDER_AND_PROTOCOL:
            leaf_tag = '{}-{}-{}{}'.format(region, provider, protocol, _OUT)
        else:
            leaf_tag = '{}-{}{}'.format(region, provider, _OUT)
        leaf.setdefault(leaf_tag, []).append(node['tag'])
        region_leaves.setdefault(region, [])
        if leaf_tag not in region_leaves[region]:
            region_leaves[region].append(leaf_tag)

    # Leaf group selectors + their failsafe urltests
    leaf_selectors, leaf_urltests = [], []
    for leaf_tag, members in leaf.items():
        fs = leaf_tag + _FAILSAFE
        leaf_selectors.append(_selector(leaf_tag, [fs] + members, fs))
        leaf_urltests.append(_urltest(fs, list(members)))

    # Region "auto" selectors + failsafes, in canonical region order
    region_auto = OrderedDict()      # region -> auto selector tag
    region_selectors, region_urltests = [], []
    for region in _REGION_ORDER:
        leaves = region_leaves.get(region)
        if not leaves:
            continue
        auto = '{}-auto{}'.format(region, _OUT)
        auto_fs = auto + _FAILSAFE
        region_auto[region] = auto
        region_selectors.append(_selector(auto, [auto_fs] + leaves, auto_fs))
        region_urltests.append(_urltest(auto_fs, [lt + _FAILSAFE for lt in leaves]))

    # Мало нод: если требуемый регион остался БЕЗ нод, его {region}-auto-out всё равно
    # обязан существовать — иначе основной конфиг sing-box, ссылающийся на него по имени,
    # не стартует ("outbound not found"). Заполняем такой регион кросс-региональным
    # фолбэком (все имеющиеся leaf-группы): селектор валиден, а failsafe-urltest сам
    # выберет лучшую ноду. ensure_regions — из groups_params.emit (деф. eu/us/other).
    ensure = _EMIT.get('ensure_regions', ['eu', 'us', 'other'])
    all_leaf_tags = list(leaf.keys())
    for region in _REGION_ORDER:
        if region in region_auto or region not in ensure or not all_leaf_tags:
            continue
        auto = '{}-auto{}'.format(region, _OUT)
        auto_fs = auto + _FAILSAFE
        region_auto[region] = auto
        region_selectors.append(_selector(auto, [auto_fs] + all_leaf_tags, auto_fs))
        region_urltests.append(_urltest(auto_fs, [lt + _FAILSAFE for lt in all_leaf_tags]))
        print(f"  [groups] регион '{region}': нод нет — auto-селектор заполнен "
              f"фолбэком из {len(all_leaf_tags)} групп (config sing-box не сломается)")

    # Standalone "{region}-nodes-tester" selectors: a flat list of every leaf node
    # of the region, gathered under a top "nodes-tester" selector. Not referenced
    # by any other group — used only for testing. Эмиссия — по флагу emit.nodes_tester.
    testers = []
    if _EMIT.get('nodes_tester', True):
        tester_tags = []
        for region in _REGION_ORDER:
            leaves = region_leaves.get(region)
            if not leaves:
                continue
            tester_tag = '{}-nodes-tester'.format(region)
            region_nodes = [tag for lt in leaves for tag in leaf[lt]]
            testers.append({'tag': tester_tag, 'type': 'selector',
                            'outbounds': region_nodes, **_SELECTOR_PARAMS})
            tester_tags.append(tester_tag)
        if tester_tags:
            default = 'eu-nodes-tester' if 'eu-nodes-tester' in tester_tags else tester_tags[0]
            testers.insert(0, _selector('nodes-tester', list(tester_tags), default))

    top = []
    if region_auto:
        # канонический порядок регионов (фолбэк-заполненные добавлялись в конце)
        region_autos = [region_auto[r] for r in _REGION_ORDER if r in region_auto]
        if _EMIT.get('global_failsafe', False):
            # global-auto-out = [global-failsafe] + региональные auto; default — failsafe.
            # global failsafe — urltest над региональными failsafe'ами (как у региональных групп).
            gfs = 'global-auto-out' + _FAILSAFE
            top.append(_selector('global-auto-out', [gfs] + region_autos, gfs))
            region_urltests.append(_urltest(gfs, [t + _FAILSAFE for t in region_autos]))
        else:
            default = region_auto.get('eu') or region_autos[0]
            top.append(_selector('global-auto-out', region_autos, default))
    top += region_selectors
    top += leaf_selectors + testers

    return top + region_urltests + leaf_urltests
