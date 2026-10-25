**English** | [Русский](README.ru.md)

# nodes_fetch

`nodes_fetch` fetches node sources and turns them into the intermediate
`raw_nodes.json`. It is the first stage of the pipeline. It does not rename nodes, does not build
groups and does not decide whether the installed sing-box supports them.

```text
config.json (all subscriptions) + URLs, files, happ, AWG folders, inline
    -> nodes_fetch
    -> raw_nodes.json
```

The next stage, [`nodes_config`](../nodes_config/README.md), applies filters and creates
a sing-box configuration fragment.

## Running

```sh
python3 -m nodes_fetch -c config.json -o data/raw/router.json

python3 -m nodes_fetch -c config.json -o data/raw/router.json \
  --only LUNA --json

python3 -m nodes_fetch -c config.json --dry-run --json
```

Options:

| Option | Purpose |
|---|---|
| `-c`, `--config` | `config.json`: subscriptions and the `pipeline.fetch` section |
| `-o`, `--output` | output `raw_nodes.json` |
| `--only TAG` | update the given source, take the others from the previous raw |
| `--dry-run` | fetch and parse the sources without writing the file |
| `--force` | write the result even if the guard threshold triggered |
| `--json` | print a machine summary to stdout; the regular log stays in stderr |
| `--device`, `--happ-keys` | paths of `device.json` and `happ-keys.json` instead of the ones next to `config.json` |

Exit codes: `0` success, `2` the guard check refused to replace the previous raw,
`1` error. The write is atomic and protected by a lock file.

## Sources

Subscriptions are the [`subscriptions`](../docs/CONFIG.md#subscriptions) section of `config.json`. Each
entry has a unique `tag` and one source:

- `url`: HTTP(S), a single share link, `sub://` or `happ://`;
- `file`: a local text subscription or Clash YAML;
- `type: "folder"`: a directory of files, currently used for AWG `.conf`;
- `type: "inline"`: your own sing-box outbounds right in `outbounds`;
- `type: "amnezia"`: a directory of AWG `.conf` files of Amnezia Premium countries; the files are issued and
  replaced by the tester ([CONFIG.md](../docs/CONFIG.md#amnezia-premium)), fetch reads them like `folder`.

`enabled`, `user_agent`, `happ_headers` and `send_device` are supported; `branch` does not matter to the fetch: all
subscriptions are fetched, the branch is used by the build. Relative paths are counted from the `config.json` directory.

### Device and panel headers

Panels with a device limit (Marzban, Remnawave and others) count every new
`X-Hwid` as a device. The installation has its own HWID in `device.json` next to `config.json`, shared by all subscriptions
(`--device` sets the path explicitly). The file is created with a random value on the first fetch.

happ subscriptions always send the HWID and the device description; `happ_headers` of a subscription
overrides any header, including `X-Hwid`. URL subscriptions send them only with
`send_device: true`: some panels change the answer or take a device slot.

The panel metadata from the answer is kept in `sources[].meta` of the raw: `Subscription-Userinfo`
(`upload`, `download`, `total`, `expire`), `Profile-Title` (`title`), `Announce`
(`announce`), `Support-Url`, `Profile-Web-Page-Url`, `Profile-Update-Interval`. `base64:…`
values are decoded. When a fetch fails, the metadata of the last successful one stays.

Network errors, timeouts, HTTP 429 and 5xx are retried with pauses of 2, 10 and 30 s: a single panel failure
must not leave a subscription without an update until the next run.

The fetch is controlled by the [`pipeline.fetch`](../docs/CONFIG.md#pipeline) section:

| Field | Default | Purpose |
|---|---:|---|
| `timeout` | `2m` | overall read timeout |
| `retries` | 3 | HTTP retries |
| `proxy` | — | proxy for downloading, for example `socks5://127.0.0.1:2080` |
| `keep_stale` | `48h` | how long the last-good data of a failed source can be used |
| `min_ratio` | 0.5 | the minimum share of nodes relative to the previous raw |

An error of one source does not stop the others. If there is a usable previous raw,
the source can go into the `stale` state. After the build the guard compares the total number of nodes
with the previous file and keeps the old file intact on a sharp drop.


## Parsers

The parsers are in `nodes_fetch/parsers`: `parse_link` parses a subscription line,
`FOLDER_FORMATS` directories of files (AWG), `clash.convert` the `proxies` entries of Clash/mihomo,
`xray_json` an Xray JSON subscription.

Supported are VLESS, VMess, Trojan, Shadowsocks (SIP002 and the old format), Hysteria,
Hysteria2, TUIC, AnyTLS, SOCKS, HTTP/HTTPS, WireGuard and AWG. The subscription content
is recognised automatically: share links line by line, base64 of them, Clash YAML, sing-box JSON or
an Xray JSON subscription. ShadowsocksR and other types that sing-box does not have are skipped.

An Xray JSON subscription (an array of full client configs) is served by panels like Remnawave when
the User-Agent does not look like sing-box or Clash. There is one config per menu item, the node name is `remarks`.
Nodes repeated in several items (“Auto”, items with a backup server)
are taken once with the name of the item where the node stands alone. Xray Mux (mux.cool) is not carried over.
Some panels give a random Reality `short_id` out of several allowed ones: from fetch to
fetch such a node changes the second part of its key (`cred_crc`), while the node and its history in the tester
stay the same (`node_crc`).

An unrecognised or broken line is skipped with a log entry; the subscription does not
fail because of it. If a link has no name, the tag becomes `type address:port`.

## Happ subscriptions

A `happ://crypt…/…` link contains an encrypted subscription address. The decryption keys
belong to the Happ app and are not part of nodes-tester. They are downloaded from
`pipeline.fetch.happ_keys_url` of config.json: a URL or a path to a `keys.rs` file or a JSON
`{"crypt": [...], "crypt5": {...}}`. By default it is `src/keys.rs` of the
[Omegaplexx/hpwnr](https://github.com/Omegaplexx/hpwnr) project. The source is changed in the admin panel on the
subscriptions tab.

The keys are cached in `happ-keys.json` next to `device.json`. The cache is refreshed if it is missing
or no key was found for a link, but not more often than once in 6 hours. The subscription is downloaded with the
headers of the mobile Happ; they can be overridden in `happ_headers`.

## XHTTP and AWG

`nodes_fetch` parses XHTTP and AWG on purpose, even if the machine has the regular
sing-box:

- XHTTP becomes a `type: "xhttp"` transport of VLESS or Trojan;
- each AWG `.conf` becomes a WireGuard endpoint with the extended AmneziaWG fields;
- a two-letter AWG file name is kept as `cc_hint`, for example `de.conf` gives `de`; the build and
  the tester do not use it.

These extensions are meant for `sing-box-lx`. The regular sing-box does not accept them.
The parser does not drop them itself, because the raw may be used by several builds.

For the regular sing-box set a filter of the next stage in `config.json`:

```json
"pipeline": { "router": { "exclude_protocols": ["xhttp", "wg"] } }
```

Details, including the difference between AWG and regular WireGuard, are in the
[compatibility guide](../docs/SING_BOX_COMPATIBILITY.md).

## The `raw_nodes.json` format

The contract is in [`nodes_common/raw.py`](../nodes_common/raw.py) and
[`schemas/raw_nodes.schema.json`](../schemas/raw_nodes.schema.json).

```json
{
  "version": 1,
  "generated_at": "2026-09-27T12:00:00Z",
  "providers": "router",
  "nodes_hash": "sha256:...",
  "sources": [],
  "nodes": [
    {
      "provider": "LUNA",
      "title": "Finland Reality",
      "cc_hint": null,
      "outbound": {"type": "vless"}
    }
  ]
}
```

`outbound` has no `tag` and no service fields starting with `_`. The original name is kept in `title`.
`nodes_hash` is computed from the canonical JSON of the `nodes` array and changes only when
its content changes.

## Code map

| File | Responsibility |
|---|---|
| `__main__.py` | CLI, lock, guard and atomic write |
| `fetch.py` | the subscriptions of the branch from config.json, last-good and the summary |
| `sources.py` | URL, file, folder, inline and happ as sources |
| `util.py` | HTTP download of subscriptions |
| `happ.py` | expanding and downloading happ subscriptions |
| `happ_crypto.py`, `_chacha.py` | decrypting `happ://crypt…` (RSA, ChaCha20-Poly1305) |
| `happ_keys.py` | source and cache of the Happ keys |
| `amnezia.py`, `_aes.py` | Amnezia gateway client: the `vpn://` link, AES-256-CBC, RSA, X25519 in pure Python |
| `parsers/` | share links, Clash and Xray JSON → sing-box objects; `_common.py`: address, TLS, transports, XHTTP |
