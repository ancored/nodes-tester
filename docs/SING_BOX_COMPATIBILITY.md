**English** | [Русский](SING_BOX_COMPATIBILITY.ru.md)

# sing-box compatibility

[About the project](../README.md) · [`nodes_fetch`](../nodes_fetch/README.md) · [`nodes_config`](../nodes_config/README.md)

## The main point

A successfully parsed subscription does not mean that the installed core accepts the resulting node.
`nodes_fetch` can parse XHTTP and AmneziaWG (AWG), but these extensions are meant for
[Leadaxe/sing-box-lx](https://github.com/Leadaxe/sing-box-lx). The regular sing-box does not
support them.

If the router runs the regular sing-box, exclude XHTTP and AWG before `nodes.json` is
created. Otherwise the resulting config may fail `sing-box check`, and sing-box will not accept
it on restart.

| Node | What the parser creates | Required core |
|---|---|---|
| VLESS or Trojan with XHTTP | `transport.type: "xhttp"` | `sing-box-lx` |
| AmneziaWG from `.conf` | endpoint `type: "wireguard"` with AWG fields | `sing-box-lx` |
| Regular WireGuard | endpoint `type: "wireguard"` without AWG fields | depends on the regular sing-box version |

The core version and capabilities still need to be checked with `sing-box version` and on
the resulting merged config. This table describes the extensions added specifically
for `sing-box-lx`.

## Core version for the tester

The tester controls sing-box through the API service (`services[].type = "api"`), which appeared in
sing-box 1.14. Version 1.14 or newer with the service enabled is required:

```json
"services": [
  { "type": "api", "tag": "api", "listen": "127.0.0.1", "listen_port": 9090,
    "secret": "<secret>", "dashboard": true }
]
```

Through this service the tester picks nodes in selector groups, checks connectivity after
a config is applied and receives the connection event stream for exact traffic accounting. The Clash API
(`experimental.clash_api`) is not used. The service also serves the official sing-box web panel
(`dashboard`). `clash_mode` modes in rules work without the Clash API as well; after a restart
the `Rule` mode is active until it is switched in the panel.

## Where incompatibility appears

The pipeline is split into stages:

```text
config.json (subscriptions)
    -> nodes_fetch
    -> raw_nodes.json
    -> nodes_config
    -> nodes.json
    -> merging with the base and presets, sing-box check
```

`nodes_fetch` keeps everything it could parse. It does not know which core is installed on the
router and does not apply build filters. Thanks to this, one raw file can be
used for different purposes.

Compatibility with the target core is set by `nodes_config`. It computes the protocol descriptor:

- XHTTP contains the `xhttp` token, for example `vless|xhttp|tls`;
- WireGuard and AmneziaWG contain the `wg` token.

Then `exclude_protocols` of the branch (`pipeline.router` or `pipeline.clients` in `config.json`)
removes nodes with the given tokens before renaming, grouping and writing `nodes.json`.

## Regular sing-box: a safe setup

Add the branch filter to `config.json`:

```json
"pipeline": {
  "router": { "exclude_protocols": ["xhttp", "wg"] },
  "clients": { "exclude_protocols": ["xhttp", "wg"] }
}
```

The `wg` token excludes all WireGuard endpoints, including regular WireGuard. If you need regular
WireGuard but not AWG, remove the folder source with
`type: "folder", format: "awg"` from `subscriptions`. Then AWG does not get into the raw file.
After that decide separately whether your core version supports regular WireGuard endpoints.

The filter is set only in the `pipeline` section: `nodes_fetch` does not apply filters.

## sing-box-lx: keep XHTTP and AWG

If `sing-box-lx` is installed, do not add `xhttp` and `wg` to `exclude_protocols`. For AWG
add a folder source to `subscriptions`:

```json
{
  "type": "folder",
  "path": "awg",
  "format": "awg",
  "ext": ".conf",
  "tag": "AWG"
}
```

Each `.conf` becomes a separate endpoint. The tester measures the node country; the file name does
not affect it. DNS from the file is not carried over,
because DNS is configured centrally in sing-box.

XHTTP is extracted from VLESS and Trojan share links with `type=xhttp`. Transport parameters and
`extra` are translated into `sing-box-lx` fields; camelCase XMUX keys are normalised to snake_case.

The fork version and the configuration format must match. Before applying always check the
candidate with the binary that actually runs on the router.

## Rereading local rules

After `.srs` and JSON source rule sets in `/etc/sing-box/rules/` are updated, sing-box 1.10 and newer rereads the changed files without a restart. That is why the pipeline restarts sing-box only when the resulting config or `base.json` changed, or on an explicit forced apply. On an older core the changed local rules take effect at the next restart. Check the installed version with `sing-box version` before setting up rules.

## Check before applying

For the package installation on OpenWrt:

```sh
nodes-tester pipeline router --dry-run
```

This mode fetches the subscriptions and builds the candidate, then merges it with the base and presets and runs
`sing-box check`, but does not replace the working config and does not restart sing-box. On an error
read the `sing-box check` output: an unknown XHTTP transport or unknown AWG fields
mean that the filter is not on or an unsuitable core is used.

For a manual run the build can be checked separately:

```sh
python3 -m nodes_fetch -c config/config.json -o data/raw/router.json --json
python3 -m nodes_config -c config/config.json --raw data/raw/router.json \
  -o data/nodes.json --check --json
```

`nodes_config --check` checks only the fragment build. Final compatibility is
confirmed by `sing-box check` after the fragment is merged with the base config.
