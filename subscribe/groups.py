'''
Build sing-box selector / urltest groups from the renamed node list.

Every node carries a '_meta' dict {provider, protocol, country} (or None for an
unparsed user node). Nodes are classified into regions by country:

    ru            -> ru
    us            -> us
    a European cc -> eu
    anything else -> other

Плоская структура: ноды каждого региона — ПРЯМЫЕ члены "{region}-auto-out"
(selector) и "{region}-auto-out-failsafe" (urltest, он же default селектора).
Промежуточных провайдер/leaf-групп между "-auto-out" и нодами больше нет. Сверху
"global-auto-out" собирает регионы. Тестовый селектор "nodes-tester" (если включён)
— плоский список всех нод; регион тестер берёт из имени ноды (recognition=parse).

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
# _EMIT.nodes_tester — создавать ли плоский тестовый селектор nodes-tester
# (все ноды прямыми членами; нужен тестеру, регион берётся из имени ноды).
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

# Коарс-регион — из общего naming (та же логика, что у тестера).
from naming import coarse_region as _region


def _selector(tag, members, default):
    return {'tag': tag, 'type': 'selector', 'default': default,
            'outbounds': members, **_SELECTOR_PARAMS}


def _urltest(tag, members):
    return {'tag': tag, 'type': 'urltest', 'outbounds': members, **_URLTEST_PARAMS}


def build(nodelist):
    '''Return the list of selector / urltest outbounds for the given nodes.'''
    region_nodes = OrderedDict()     # region -> [node tags] (ноды — прямые члены)

    for node in nodelist:
        meta = node.get('_meta')
        if meta is None:                          # name could not be parsed: the node
            continue                              # stays in the output but is never grouped
        if meta['country'] == 'undef':            # страна не определена — НЕ в 'other',
            continue                              # нода остаётся в выводе, но вне групп
        if meta['protocol'] in UNGROUPED_PROTOCOLS:   # XHTTP and the like are not grouped
            continue
        region = _region(meta['country'])
        region_nodes.setdefault(region, []).append(node['tag'])

    all_nodes = [t for tags in region_nodes.values() for t in tags]

    # Region "auto" selectors + failsafe urltests: ноды — ПРЯМЫЕ члены (плоско, без
    # промежуточных провайдер/leaf-групп). "{region}-auto-out" (selector, default =
    # failsafe) и "{region}-auto-out-failsafe" (urltest) держат один и тот же список
    # всех нод региона.
    # Мало нод: требуемый регион (ensure_regions, деф. eu/us/other) без нод всё равно
    # обязан существовать — иначе основной конфиг sing-box, ссылающийся на него по имени,
    # не стартует ("outbound not found"). Пустой регион заполняем всеми нодами (фолбэк).
    ensure = _EMIT.get('ensure_regions', ['eu', 'us', 'other'])
    region_auto = OrderedDict()      # region -> auto selector tag
    region_selectors, region_urltests = [], []
    for region in _REGION_ORDER:
        members = region_nodes.get(region)
        if not members:
            if region not in ensure or not all_nodes:
                continue
            members = all_nodes
            print(f"  [groups] регион '{region}': нод нет — auto-селектор заполнен "
                  f"фолбэком из {len(all_nodes)} нод (config sing-box не сломается)")
        auto = '{}-auto{}'.format(region, _OUT)
        auto_fs = auto + _FAILSAFE
        region_auto[region] = auto
        region_selectors.append(_selector(auto, [auto_fs] + members, auto_fs))
        region_urltests.append(_urltest(auto_fs, list(members)))

    # Плоский тестовый селектор: ВСЕ тестируемые ноды прямыми членами. Регион тестер
    # берёт из имени ноды (recognition=parse). Эмиссия — по флагу emit.nodes_tester.
    testers = []
    if _EMIT.get('nodes_tester', True) and all_nodes:
        testers.append(_selector('nodes-tester', list(all_nodes), all_nodes[0]))

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
    top += region_selectors + testers

    return top + region_urltests
