'''
Группы sing-box из списка нод.

Роутер (config.groups): у каждой группы selector `{tag}-auto-out` со ВСЕМИ нодами — какую
активировать, решает тестер по проверкам группы. Кроме нод в селекторе запасной член:
  нестрогая группа — общий urltest `failsafe-out` (по умолчанию: включается при старте и
  когда кандидатов нет);
  строгая — `block-out` (outbound из базы sing-box: пока нет подходящего кандидата, трафик
  группы блокируется);
  `direct_fallback: true` добавляет `direct-out` — крайний случай; у строгой группы он
  же стоит по умолчанию вместо block.
`global-auto-out` — selector групп с `in_global`; `nodes-tester` — плоский selector всех
нод для тестера и `failsafe-out`: через него монитор зондирует запасной выход групп.

Клиенты (whnodes.json): selector `global-auto-out` со всеми нодами и urltest
`failsafe-out` по умолчанию — клиентские конфиги ссылаются только на global-auto-out.
'''

FAILSAFE, BLOCK, DIRECT = 'failsafe-out', 'block-out', 'direct-out'


def _selector(tag, members, default, params):
    return {'tag': tag, 'type': 'selector', 'default': default, 'outbounds': members, **params}


def _urltest(tag, members, params):
    return {'tag': tag, 'type': 'urltest', 'outbounds': members, **params}


def router_groups(nodes, groups, selector=None, urltest=None):
    '''Selector-ы групп, global-auto-out, nodes-tester и общий failsafe-out.'''
    selector, urltest = selector or {}, urltest or {}
    out, in_global = [], []
    for g in groups:
        if g.strict:
            fallback = [BLOCK] + ([DIRECT] if g.direct_fallback else [])
            default = DIRECT if g.direct_fallback else BLOCK
        else:
            fallback = [FAILSAFE] + ([DIRECT] if g.direct_fallback else [])
            default = FAILSAFE
        auto = '{}-auto-out'.format(g.tag)
        out.append(_selector(auto, fallback + list(nodes), default, selector))
        if g.in_global:
            in_global.append(auto)
    top = []
    if in_global:
        top.append(_selector('global-auto-out', in_global, in_global[0], selector))
    failsafe = any(not g.strict for g in groups)
    if nodes:
        out.append(_selector('nodes-tester', list(nodes) + ([FAILSAFE] if failsafe else []),
                             nodes[0], selector))
    if failsafe:
        # urltest без членов sing-box не принимает: без нод проверять нечего — direct.
        out.append(_urltest(FAILSAFE, list(nodes) or [DIRECT], urltest))
    return top + out


def client_groups(nodes, selector=None, urltest=None):
    '''whnodes.json: global-auto-out (все ноды, по умолчанию failsafe-out) и failsafe-out.'''
    selector, urltest = selector or {}, urltest or {}
    return [_selector('global-auto-out', [FAILSAFE] + list(nodes), FAILSAFE, selector),
            _urltest(FAILSAFE, list(nodes) or [DIRECT], urltest)]
