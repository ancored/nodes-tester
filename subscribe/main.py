import json, os, shutil, tool, time, importlib, argparse, yaml, ruamel.yaml
import re
from datetime import datetime
from urllib.parse import urlparse
from parsers.clash2base64 import clash2v2ray
import groups
import happ

parsers_mod = {}
providers = None
# Каталог config/ активной генерации — база для относительных путей folder-подписок.
# Выставляется в __main__ (по умолчанию — config/ этого проекта, либо --config-dir).
active_config_dir = None


def init_parsers():
    # Папка парсеров — рядом с этим модулем (не относительно cwd), иначе при
    # запуске `python -m subscribe` из корня репо os.walk('parsers') не найдёт её.
    parsers_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'parsers')
    for path, dirs, files in os.walk(parsers_dir):
        for file in files:
            f = os.path.splitext(file)
            if f[1] == '.py':
                parsers_mod[f[0]] = importlib.import_module('parsers.' + f[0])


def load_json(path):
    return json.loads(tool.readFile(path))


def get_nodes_from_folder(subscribe):
    '''Подписка типа "folder": каталог с отдельными файлами, по одному узлу на файл.
    Формат файлов задаётся ключом "format" (имя парсера с функцией parse_file, деф. awg),
    расширение — "ext" (деф. .conf). Путь "path" — относительно config/ активной
    генерации либо абсолютный. Возвращает список узлов (как get_nodes для url).'''
    fmt = subscribe.get('format', 'awg')
    mod = parsers_mod.get(fmt)
    if not mod or not hasattr(mod, 'parse_file'):
        print(f"  [subscribe] неизвестный формат folder-подписки: {fmt!r} — пропуск")
        return []
    folder = subscribe.get('path') or subscribe.get('folder') or ''
    if not os.path.isabs(folder):
        folder = os.path.join(active_config_dir or _CONFIG_DIR, folder)
    if not os.path.isdir(folder):
        print(f"  [subscribe] папка folder-подписки не найдена: {folder} — пропуск")
        return []
    ext = subscribe.get('ext', '.conf').lower()
    print('Processing: \033[31m' + folder + '\033[0m')
    nodes = []
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(ext):
            continue
        try:
            nodes.append(mod.parse_file(os.path.join(folder, fn)))
        except Exception as exc:  # noqa: BLE001 — битый/неполный файл: пропуск, не роняем генерацию
            print(f"  [subscribe] пропущен {fn} ({exc.__class__.__name__}: {exc})")
    return nodes


def process_subscribes(subscribes):
    nodes = {}
    for subscribe in subscribes:
        if 'enabled' in subscribe and not subscribe['enabled']:
            continue
        if subscribe.get('type') == 'folder':           # папка с файлами (напр. AWG .conf)
            _nodes = get_nodes_from_folder(subscribe)
        elif subscribe.get('file'):                     # подписка из локального файла
            try:
                _nodes = get_nodes_from_file(subscribe['file'])
            except Exception as exc:  # noqa: BLE001 — нет файла/битый: пропуск, не роняем генерацию
                print(f"  [subscribe] файл подписки недоступен ({subscribe['file']}): "
                      f"{exc.__class__.__name__} — пропуск")
                _nodes = []
        elif happ.is_happ_link(subscribe.get('url', '')):   # зашифрованная ссылка Happ
            try:
                _nodes = get_nodes_from_happ(subscribe['url'], subscribe)
            except Exception as exc:  # noqa: BLE001 — не роняем генерацию из-за одной подписки
                print(f"  [subscribe] Happ-подписка недоступна: "
                      f"{exc.__class__.__name__}: {exc} — пропуск")
                _nodes = []
        else:
            if 'sing-box-subscribe-doraemon.vercel.app' in subscribe.get('url', ''):
                continue
            _nodes = get_nodes(subscribe['url'])
        if _nodes and len(_nodes) > 0:
            add_prefix(_nodes, subscribe)
            add_emoji(_nodes, subscribe)
            nodefilter(_nodes, subscribe)
            for node in _nodes:
                node['_provider'] = subscribe['tag']  # provider name for the rename
            if subscribe.get('subgroup'):
                subscribe['tag'] = subscribe['tag'] + '-' + subscribe['subgroup'] + '-' + 'subgroup'
            if not nodes.get(subscribe['tag']):
                nodes[subscribe['tag']] = []
            nodes[subscribe['tag']] += _nodes
        else:
            print('No nodes found in this subscription, skipping')
    return nodes


# Пользовательские конфиги — в папке config/ в корне репозитория.
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config')
USER_NODES_PATH = os.path.join(_CONFIG_DIR, 'user_nodes.json')
PROVIDERS_PATH = os.path.join(_CONFIG_DIR, 'providers.json')


def load_user_nodes(path):
    # user-supplied nodes, copied to the output verbatim except for their tag
    if not os.path.exists(path):
        return []
    nodes = json.loads(tool.readFile(path))
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


def add_prefix(nodes, subscribe):
    if subscribe.get('prefix'):
        for node in nodes:
            node['tag'] = subscribe['prefix'] + node['tag']
            if node.get('detour'):
                node['detour'] = subscribe['prefix'] + node['detour']


def add_emoji(nodes, subscribe):
    if subscribe.get('emoji'):
        for node in nodes:
            node['tag'] = tool.rename(node['tag'])
            if node.get('detour'):
                node['detour'] = tool.rename(node['detour'])


def nodefilter(nodes, subscribe):
    if subscribe.get('ex-node-name'):
        ex_nodename = re.split(r'[,\|]', subscribe['ex-node-name'])
        for exns in ex_nodename:
            for node in nodes[:]:  # iterate over a copy of nodes so items can be removed safely
                if exns in node['tag']:
                    nodes.remove(node)


def get_nodes(url):
    if url.startswith('sub://'):
        url = tool.b64Decode(url[6:]).decode('utf-8')
    urlstr = urlparse(url)
    if not urlstr.scheme:
        try:
            content = tool.b64Decode(url).decode('utf-8')
            data = parse_content(content)
            processed_list = []
            for item in data:
                if isinstance(item, tuple):
                    processed_list.extend([item[0], item[1]])  # handle shadowtls
                else:
                    processed_list.append(item)
            return processed_list
        except:
            content = get_content_form_file(url)
    else:
        content = get_content_from_url(url)
    # print (content)
    if type(content) == dict:
        if 'proxies' in content:
            share_links = []
            for proxy in content['proxies']:
                share_links.append(clash2v2ray(proxy))
            data = '\n'.join(share_links)
            data = parse_content(data)
            processed_list = []
            for item in data:
                if isinstance(item, tuple):
                    processed_list.extend([item[0], item[1]])  # handle shadowtls
                else:
                    processed_list.append(item)
            return processed_list
        elif 'outbounds' in content:
            outbounds = []
            excluded_types = {"selector", "urltest", "direct", "block", "dns"}
            filtered_outbounds = [outbound for outbound in content['outbounds'] if outbound.get("type") not in excluded_types]
            outbounds.extend(filtered_outbounds)
            return outbounds
    else:
        data = parse_content(content)
        processed_list = []
        for item in data:
            if isinstance(item, tuple):
                processed_list.extend([item[0], item[1]])  # handle shadowtls
            else:
                processed_list.append(item)
        return processed_list


def get_nodes_from_file(path):
    '''Прочитать подписку из локального файла: raw share-links (.txt) или clash .yaml.
    Возвращает список нод, как get_nodes для url.'''
    content = get_content_form_file(path)     # .yaml → share-links, иначе текст файла
    data = parse_content(content)
    processed_list = []
    for item in data:
        if isinstance(item, tuple):
            processed_list.extend([item[0], item[1]])  # shadowtls
        else:
            processed_list.append(item)
    return processed_list


def get_nodes_from_happ(url, subscribe=None):
    '''Подписка Happ: happ://crypt…/… → расшифровать URL → скачать с happ-заголовками →
    ноды (как get_nodes). Необязательный ключ подписки `happ_headers` — оверрайд заголовков
    (напр. свой X-Hwid при упоре в лимит устройств).'''
    headers = (subscribe or {}).get('happ_headers') or None
    content = happ.subscription_text(url, headers)
    data = parse_content(content)
    processed_list = []
    for item in data:
        if isinstance(item, tuple):
            processed_list.extend([item[0], item[1]])  # shadowtls
        else:
            processed_list.append(item)
    return processed_list


def parse_content(content):
    # firstline = tool.firstLine(content)
    # # print(firstline)
    # if not get_parser(firstline):
    #     return None
    nodelist = []
    for t in content.splitlines():
        t = t.strip()
        if len(t) == 0:
            continue
        factory = get_parser(t)
        if not factory:
            continue
        node = None
        try:
            node = factory(t)
        except Exception as e:  # строка не распарсилась — пропускаем (не дублируем предыдущую)
            print(f"  [subscribe] пропущена строка ({e.__class__.__name__})")
            continue
        if node:
            nodelist.append(node)
    return nodelist


def get_parser(node):
    proto = tool.get_protocol(node)
    if providers.get('exclude_protocol'):
        eps = providers['exclude_protocol'].split(',')
        if len(eps) > 0:
            eps = [protocol.strip() for protocol in eps]
            if 'hy2' in eps:
                index = eps.index('hy2')
                eps[index] = 'hysteria2'
            if proto in eps:
                return None
    if not proto or proto not in parsers_mod.keys():
        return None
    return parsers_mod[proto].parse


def get_content_from_url(url, n=10):
    UA = ''
    print('Processing: \033[31m' + url + '\033[0m')
    prefixes = ["vmess://", "vless://", "ss://", "ssr://", "trojan://", "tuic://", "hysteria://", "hysteria2://",
                "hy2://", "wg://", "wireguard://", "http2://", "socks://", "socks5://"]
    if any(url.startswith(prefix) for prefix in prefixes):
        response_text = tool.noblankLine(url)
        return response_text
    for subscribe in providers["subscribes"]:
        if 'enabled' in subscribe and not subscribe['enabled']:
            continue
        if subscribe.get('url') == url:
            UA = subscribe.get('User-Agent', '')
    response = tool.getResponse(url, custom_user_agent=UA)
    concount = 1
    while concount <= n and not response:
        print('Connection error, retry ' + str(concount) + ' of ' + str(n) + '...')
        response = tool.getResponse(url)
        concount = concount + 1
        time.sleep(1)
    if not response:
        print('Fetch failed, skipping this subscription')
        print('----------------------------')
        pass
    try:
        response_content = response.content
        response_text = response_content.decode('utf-8-sig')  # utf-8-sig ignores the BOM
        #response_encoding = response.encoding
    except:
        return ''
    if response_text.isspace():
        print('No content received from the subscription link')
        return None
    if not response_text:
        response = tool.getResponse(url, custom_user_agent='clashmeta')
        response_text = response.text
    if any(response_text.startswith(prefix) for prefix in prefixes):
        response_text = tool.noblankLine(response_text)
        return response_text
    elif 'proxies' in response_text:
        yaml_content = response.content.decode('utf-8')
        response_text_no_tabs = yaml_content.replace('\t', ' ') #fuckU
        yaml = ruamel.yaml.YAML()
        try:
            response_text = dict(yaml.load(response_text_no_tabs))
            return response_text
        except:
            pass
    elif 'outbounds' in response_text:
        try:
            response_text = json.loads(response.text)
            return response_text
        except:
            response_text = re.sub(r'//.*', '', response_text)
            response_text = json.loads(response_text)
            return response_text
    else:
        try:
            response_text = tool.b64Decode(response_text)
            response_text = response_text.decode(encoding="utf-8")
            # response_text = bytes.decode(response_text,encoding=response_encoding)
        except:
            pass
            # traceback.print_exc()
    return response_text


def get_content_form_file(url):
    print('Processing: \033[31m' + url + '\033[0m')
    file_extension = os.path.splitext(url)[1]  # get the file extension
    if file_extension.lower() == '.yaml':
        with open(url, 'rb') as file:
            content = file.read()
        yaml_data = dict(yaml.safe_load(content))
        share_links = []
        for proxy in yaml_data['proxies']:
            share_links.append(clash2v2ray(proxy))
        node = '\n'.join(share_links)
        processed_list = tool.noblankLine(node)
        return processed_list
    else:
        data = tool.readFile(url)
        data = bytes.decode(data, encoding='utf-8')
        data = tool.noblankLine(data)
        return data


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
    init_parsers()
    parser = argparse.ArgumentParser()
    parser.add_argument('--temp_json_data', type=parse_json, help='inline providers JSON, overrides providers.json')
    parser.add_argument('--config-dir', dest='config_dir',
                        help='папка config (providers.json/user_nodes.json/groups_params.json) '
                             'для независимой генерации из другого набора конфигов')
    args = parser.parse_args()
    # Пути конфигов: по умолчанию config/ этого проекта; --config-dir переопределяет всё.
    providers_path = os.path.join(args.config_dir, 'providers.json') if args.config_dir else PROVIDERS_PATH
    user_nodes_path = os.path.join(args.config_dir, 'user_nodes.json') if args.config_dir else USER_NODES_PATH
    # база для относительных путей folder-подписок (см. get_nodes_from_folder)
    active_config_dir = args.config_dir or _CONFIG_DIR
    if args.config_dir:
        groups.load_params(args.config_dir)     # групповые параметры из той же папки
    temp_json_data = args.temp_json_data
    if temp_json_data and temp_json_data != '{}':
        providers = temp_json_data              # inline-JSON перебивает providers.json
    else:
        providers = load_json(providers_path)
    nodes = process_subscribes(providers["subscribes"])
    sub_nodes = [node for contents in nodes.values() for node in contents]
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
