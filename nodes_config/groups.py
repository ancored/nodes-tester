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
Ноды подписок из `exclude_subscriptions` группы в её селектор не входят; подписку, которую
исключили все группы, не берут ни `failsafe-out`, ни `nodes-tester`. Ноды подписок только для
клиентов (`branch: clients`) — только в `nodes-tester`: тестер их проверяет, трафик роутера через
них не идёт.

Клиенты (whnodes.json): urltest `{tag}-auto-out` каждой группы pipeline.clients.groups из нод,
прошедших её проверки на роутере, и selector `global-auto-out` групп с `in_global` — базы
клиентов ссылаются на global-auto-out.
'''

FAILSAFE, BLOCK, DIRECT = 'failsafe-out', 'block-out', 'direct-out'


def _selector(tag, members, default, params):
    return {'tag': tag, 'type': 'selector', 'default': default, 'outbounds': members, **params}


def _urltest(tag, members, params):
    return {'tag': tag, 'type': 'urltest', 'outbounds': members, **params}


def router_groups(nodes, groups, selector=None, urltest=None, provider_of=None, tester_only=()):
    '''Selector-ы групп, global-auto-out, nodes-tester и общий failsafe-out.
    provider_of — тег ноды → подписка (для exclude_subscriptions); tester_only — подписки
    только для клиентов.'''
    selector, urltest, provider_of = selector or {}, urltest or {}, provider_of or {}
    tester_only = set(tester_only)

    def members(excluded):
        return [n for n in nodes if provider_of.get(n) not in excluded]

    # Подписку, которую исключили все группы, не берут и failsafe-out с nodes-tester.
    unused = set.intersection(*(set(g.exclude_subscriptions) for g in groups)) if groups else set()
    tested, routed = members(unused), members(unused | tester_only)
    out, in_global = [], []
    for g in groups:
        if g.strict:
            fallback = [BLOCK] + ([DIRECT] if g.direct_fallback else [])
            default = DIRECT if g.direct_fallback else BLOCK
        else:
            fallback = [FAILSAFE] + ([DIRECT] if g.direct_fallback else [])
            default = FAILSAFE
        auto = '{}-auto-out'.format(g.tag)
        out.append(_selector(auto, fallback + members(set(g.exclude_subscriptions) | tester_only),
                             default, selector))
        if g.in_global:
            in_global.append(auto)
    top = []
    if in_global:
        top.append(_selector('global-auto-out', in_global, in_global[0], selector))
    failsafe = any(not g.strict for g in groups)
    if tested:
        out.append(_selector('nodes-tester', tested + ([FAILSAFE] if failsafe else []),
                             tested[0], selector))
    if failsafe:
        # urltest без членов sing-box не принимает: без нод проверять нечего — direct.
        out.append(_urltest(FAILSAFE, routed or [DIRECT], urltest))
    return top + out


def client_groups(members, groups, selector=None, urltest=None):
    '''whnodes.json: global-auto-out (группы с in_global) и urltest каждой группы.
    members — группа → теги её нод; fallback группы (outbound базы клиента) — последним членом.'''
    selector, urltest = selector or {}, urltest or {}
    top = ['{}-auto-out'.format(g.tag) for g in groups if g.in_global]
    tests = []
    for g in groups:
        outbounds = list(members[g.tag]) + ([g.fallback] if g.fallback else [])
        tests.append(_urltest('{}-auto-out'.format(g.tag), outbounds, urltest))
    return [_selector('global-auto-out', top, top[0], selector)] + tests
