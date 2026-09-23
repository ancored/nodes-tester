"""Переименование нод по единой схеме: {provider}-{protocol}-{country}-out [label].

Перенесено из subscribe/tool.py без изменения логики (CRC/теги не меняются). Схема имени,
протокол, флаги и страны — в общем пакете naming (его же использует тестер).
"""

import re

from naming import country_from_text, flag_to_code, node_protocol
from naming.regions import _flag_regex

_OUT_SUFFIX = '-out'


# A user-supplied node names its provider and country as "Provider-CC"
_user_tag_regex = re.compile(r'^(?P<provider>.+)-(?P<cc>[A-Za-z]{2})$')


def _tag_from_meta(meta):
    parts = [p for p in (meta['provider'], meta['protocol'], meta['country']) if p]
    tag = '-'.join(parts) + _OUT_SUFFIX
    if meta.get('label'):                       # доп. поле после -out, до [CRC]
        tag += ' [{}]'.format(meta['label'])
    return tag


def group_meta(node):
    '''
    Parse a subscription node into {provider, protocol, country}, or None when the
    country cannot be determined at all.
        provider: the subscription's tag from providers.json, stamped onto the
                  node as '_provider' (the node title is often just a flag +
                  country, with no provider name in it)
        country:  two-letter ISO code, по приоритету: флаг-эмодзи в title → название
                  страны в тексте (country_from_text) → '_file_cc' (для folder-парсеров
                  вроде AWG, где страна известна лишь из имени файла) → 'undef'
        protocol: derived from the node fields (see node_protocol)
    '''
    title = node.get('tag', '')
    m = _flag_regex.search(title)
    if m:
        country = flag_to_code(m.group())
    else:
        country = country_from_text(title) or node.get('_file_cc') or 'undef'
    return {'provider': node.get('_provider', ''),
            'protocol': node_protocol(node),
            'country': country,
            'label': node.get('_label', '')}


def custom_rename(node):
    '''
    Build the tag of one subscription node as {provider}-{protocol}-{country}-out:
        provider "LUNA", "🇫🇮 Finland | Reality"  -> "LUNA-vless(reality)-fi-out"
    The provider comes from node['_provider'] (the subscription tag), not the title.
    Titles without a flag emoji are returned unchanged. Uniqueness is provided by
    the CRC32 fingerprint appended later (see content_crc32).
    '''
    meta = group_meta(node)
    if meta is None:
        return node.get('tag', '')
    return _tag_from_meta(meta)


def rename_user_node(node):
    '''
    Rename a user-supplied node in place, deriving provider and country from its
    tag "Provider-CC" and the protocol from the node fields:
        "Sbercloud-RU"  -> "Sbercloud-vless(reality)-ru-out"
    Sets node['_meta'] for grouping. When the tag cannot be parsed the node is
    left untouched and _meta is None, so it stays in the output as-is and is not
    placed into any group.
    '''
    m = _user_tag_regex.match(node.get('tag', ''))
    if not m:
        node['_meta'] = None
        return
    meta = {'provider': m.group('provider'),
            'protocol': node_protocol(node),
            'country': m.group('cc').lower(),
            'label': node.get('_label', '')}
    node['_meta'] = meta
    node['tag'] = _tag_from_meta(meta)


# node_payload / content_crc32 — в общем пакете naming (используются в build.py).
