"""subscribe — совместимая обёртка «fetch → nodes_config» над конфигами v1 (strangler, Ф2).

Весь код теперь в пакетах nodes_fetch (подписки → raw) и nodes_config (переименование,
фильтры, CRC, группы). Здесь — только прежний интерфейс для роутерных скриптов:

    python -m subscribe                        # config/ этого проекта
    python -m subscribe --config-dir DIR       # другой набор (providers/groups_params/user_nodes)

Конфиги v1 (общий providers.json + groups_params.json) переводятся в v2 на лету
(nodes_config.migrate.split_v1), результат пишется в providers.save_config_path.
Удаляется на Ф6, когда роутерные скрипты перейдут на nodes_fetch/nodes_config напрямую.
"""
import argparse
import json
import os
import sys

from nodes_common.fileio import atomic_write_text, dumps_json, read_json
from nodes_config import build as config_build
from nodes_config import migrate, params
from nodes_fetch import fetch as nodes_fetch

# Пользовательские конфиги по умолчанию — в папке config/ в корне репозитория.
_CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config')


def _log(msg):
    print(msg, flush=True)


def generate(config_dir, providers=None):
    """v1-набор конфигов → (фрагмент nodes.json, путь вывода, отчёт)."""
    if providers is None:
        providers = read_json(os.path.join(config_dir, 'providers.json'))
    gp_path = os.path.join(config_dir, 'groups_params.json')
    groups_v1 = read_json(gp_path) if os.path.exists(gp_path) else None
    prov2, gp2, output = migrate.split_v1(providers, groups_v1)
    raw = nodes_fetch.run(nodes_fetch.load_providers(prov2, log=_log), config_dir, log=_log)
    un_path = os.path.join(config_dir, 'user_nodes.json')
    user_nodes = read_json(un_path) if os.path.exists(un_path) else []
    if not isinstance(user_nodes, list):
        user_nodes = []
    fragment, report = config_build.build([raw], params.load(gp2), user_nodes, log=_log)
    return fragment, output, report


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m subscribe')
    parser.add_argument('--temp_json_data', type=json.loads,
                        help='inline providers JSON, overrides providers.json')
    parser.add_argument('--config-dir', dest='config_dir',
                        help='папка config (providers.json/user_nodes.json/groups_params.json) '
                             'для независимой генерации из другого набора конфигов')
    args = parser.parse_args(argv)
    config_dir = os.path.abspath(args.config_dir or _CONFIG_DIR)
    providers = args.temp_json_data if args.temp_json_data not in (None, {}) else None
    fragment, output, report = generate(config_dir, providers)
    if not output:
        print('providers.json: не задан save_config_path', file=sys.stderr)
        return 1
    atomic_write_text(output, dumps_json(fragment))
    _log(f"Nodes saved: {output} (нод {report['leaf_nodes']}, групп {report['groups']})")
    return 0


if __name__ == '__main__':
    sys.exit(main())
