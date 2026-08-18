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
used as its default. Region "-auto-out" selectors gather the leaf groups, a top
"global-auto-out" selector gathers the region groups, and "ru-predef-out" lists
every ru node plus the ru failsafe.

Plain "vless" / "vmess" nodes (no transport, no reality — e.g. XHTTP) are dropped
from the output entirely by main.finalize_nodes before this runs.

Nodes whose name could not be parsed (no flag emoji / no Provider-CC form, so
'_meta' is None) stay in the output with their original tag but are never placed
into any group.
'''
import json
import os
from collections import OrderedDict

# Per-type group parameters (interval / tolerance / interrupt_exist_connections …)
# live in groups_params.json next to this module, so they can be tuned without
# touching the code. Keys: "selector" and "urltest".
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корень репо
_PARAMS_PATH = os.path.join(_ROOT, 'config', 'groups_params.json')
with open(_PARAMS_PATH, encoding='utf-8') as _f:
    _PARAMS = json.load(_f)
_SELECTOR_PARAMS = _PARAMS.get('selector', {})
_URLTEST_PARAMS = _PARAMS.get('urltest', {})
# Что генерировать. emit.nodes_tester — создавать ли тестовые селекторы
# nodes-tester + {region}-nodes-tester (нужны тестеру в режиме by_selector).
_EMIT = _PARAMS.get('emit', {})

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
    ru_nodes = []                    # every ru node tag, for ru-predef-out

    for node in nodelist:
        meta = node.get('_meta')
        if meta is None:                          # name could not be parsed: the node
            continue                              # stays in the output but is never grouped
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
        if region == 'ru':
            ru_nodes.append(node['tag'])

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
        default = region_auto.get('eu') or next(iter(region_auto.values()))
        top.append(_selector('global-auto-out', list(region_auto.values()), default))
    top += region_selectors
    if 'ru' in region_auto:
        ru_fs = region_auto['ru'] + _FAILSAFE
        top.append(_selector('ru-predef-out', [ru_fs] + ru_nodes, ru_fs))
    top += leaf_selectors + testers

    return top + region_urltests + leaf_urltests
