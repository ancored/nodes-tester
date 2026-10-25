**English** | [Русский](README.ru.md)

# nodes_config

`nodes_config` turns `raw_nodes.json` into a ready sing-box configuration fragment of the router, and the router nodes
checked by the tester into the client fragment. It is the second stage of the pipeline.

```text
raw_nodes.json + config.json (pipeline.router, groups, subscriptions[].exclude_names)
    -> filters that keep sing-box from failing
    -> names and keys [node_crc.cred_crc]
    -> groups of all nodes
    -> nodes.json

nodes.json + client-candidates.json (the tester) + config.json (pipeline.clients)
    -> client filters and domain_resolver
    -> urltest of the client groups, global-auto-out
    -> whnodes.json
```

The output has `outbounds` and, when there is WireGuard or AWG, `endpoints`. The fragment must be
merged with the base configuration and checked with the sing-box that will run it.

## Running

```sh
python3 -m nodes_config -c config.json \
  --raw data/raw/router.json -o data/nodes.json --json
```

`--raw` can be repeated. The `--check` flag builds the result and compares it with the current file,
but writes nothing. The client fragment:

```sh
python3 -m nodes_config -c config.json --branch clients \
  --nodes data/nodes.json --candidates data/client-candidates.json -o data/whnodes.json
```

The file is replaced only when the JSON changes. The write is atomic and protected by a lock file. The
`--json` summary has `changed`, `written`, the added and removed tags, the raw hashes and the number of
dropped nodes by reason.

## Processing order

1. Nodes are extracted from the raw with their original name and provider.
2. `exclude_types` of the branch and `exclude_names` of the subscription are applied.
3. Bare VLESS and VMess without a recognised transport or Reality are dropped; `exclude_protocols`
   of the branch is applied.
4. The node gets its name, `domain_resolver` and key.
5. Identical accesses collapse, `detour` references are moved to the new names.
6. Groups are built.
7. WireGuard endpoints are separated from regular outbounds.

The country is not determined or filtered anywhere: the tester measures it and decides which node
fits a group.

## Filters

The settings are in the `pipeline.router` or `pipeline.clients` section of
[`config.json`](../docs/CONFIG.md#pipeline):

- `exclude_types` is compared with the outbound `type`, for example `shadowsocksr`;
- `exclude_protocols` is compared with the tokens of the protocol descriptor: `xhttp` catches
  `vless|xhttp|tls` and `trojan|xhttp|tls`, `wg` WireGuard and AWG;
- `exclude_names` of a subscription looks for substrings in the original node name.

`nodes_fetch` does not apply these filters.

### Core compatibility

XHTTP and AWG are meant for `sing-box-lx`. For the regular sing-box:

```json
"pipeline": { "router": { "exclude_protocols": ["xhttp", "wg"] } }
```

The `wg` token removes both AWG and regular WireGuard endpoints. Details and the candidate check are in
[`docs/SING_BOX_COMPATIBILITY.md`](../docs/SING_BOX_COMPATIBILITY.md).

## Names and DNS

`domain_resolver` of the branch sets the DNS server for resolving the node domain; the tag must exist in
`dns.servers` of the base config. For WireGuard the field is not added.

```text
{subscription}-{protocol}-out [node_crc.cred_crc]

LUNA-vless|reality-out [b40c2d73.0be1f7a2]
LUNA-vmess|ws|tls-out [eebdd291.5a1c03de]
AWG-wg-out [61df49c0.9d2e4b17]
```

There is no country and no labels in the name. Your own nodes (a `"type": "inline"` subscription) are named the same way, by
the subscription tag.

The key in square brackets is the CRC32 of the server settings and the CRC32 of the credentials (see
[`naming`](../naming/README.md#node-key)). New credentials from the provider change only
the second part. The same access from two subscriptions (the same key) stays one node.

## Groups

The `router` branch builds groups from the [`groups`](../docs/CONFIG.md#groups) section. Each group is a
selector `{tag}-auto-out` with **all** nodes of the `router` and `both` subscriptions; the tester decides which one to
activate by the group checks. Besides the nodes, the selector has fallback members:

| Group | Members on top of the nodes | Default |
|---|---|---|
| non-strict | `failsafe-out` | `failsafe-out` |
| non-strict, `direct_fallback` | `failsafe-out`, `direct-out` | `failsafe-out` |
| strict (`strict`) | `block-out` | `block-out` |
| strict, `direct_fallback` | `block-out`, `direct-out` | `direct-out` |

- `failsafe-out` is one urltest over all nodes for all non-strict groups; without nodes, `direct-out`.
- `block-out` and `direct-out` are taken from the sing-box base config (`base.json`), for example
  `{"type": "block", "tag": "block-out"}`.
- `global-auto-out` is a selector of the groups with `in_global`.
- `nodes-tester` is a flat selector of all nodes for the tester plus `failsafe-out`: through it the monitor
  probes the fallback outbound of groups. Nodes of `clients` subscriptions are only here: the tester checks them, router
  traffic does not go through them.

The `selector` and `urltest` fields of the branch are added to all selectors and to `failsafe-out`.

The `clients` branch takes the leaf outbounds from `nodes.json`: the nodes of `clients` and `both` subscriptions that the
tester listed for a client group (`pipeline.clients.groups`), and the nodes their `detour` points to. On top of them come the
`clients` filters and `domain_resolver`. Each client group is a urltest `{tag}-auto-out`; the groups with `in_global` form the
`global-auto-out` selector, which the client configs use. A group without nodes, a group the tester did not list and candidates
older than 30 minutes are errors: `whnodes.json` stays as it was.

## Code map

| File | Responsibility |
|---|---|
| `__main__.py` | CLI, comparison and atomic write |
| `build.py` | filters, names, collapsing accesses, building the fragment |
| `groups.py` | group selectors and urltest |
