**English** | [Русский](CONFIG.ru.md)

# config.json

[About the project](../README.md) · [sing-box config assembly](CONFIG_ASSEMBLY.md) · [Admin panel](USAGE.md) · [Security model](SECURITY_MODEL.md)

All nodes-tester settings are stored in one file, `config.json`: subscriptions, groups and their checks,
the tester, the rating, switching, the pipeline, the admin panel and notifications. The format follows the `config.json`
of sing-box itself:

- top-level sections are by entity; groups and subscriptions are arrays of objects with `tag`;
- durations are strings: `"250ms"`, `"5s"`, `"1h30m"`, `"30d"`;
- speed and size are numbers with the unit in the field name: `_mbps`, `_mb`;
- default values do not need to be written to the file; the admin panel shows them in grey in empty form fields;
- an unknown field is an error with its path: `groups[1].require.gemeni: unknown field (available: anthropic, country, …)`.

In the OpenWrt package the file is `/etc/nodes-tester/config.json` with permissions `0600`: it holds subscription URLs
and tokens. Next to it is `config.schema.json` for editor hints (`"$schema": "config.schema.json"`).
The data directory (`nodes.json`, `whnodes.json`, `stats.db`, `raw/`) is set by `data_dir` in
`/etc/config/nodes-tester`, `/opt/nodes-tester` by default.

Changes take effect after the tester restarts: the admin panel on OpenWrt does it itself after
saving, manually: `/etc/init.d/nodes-tester restart`. User connections are not dropped. Subscriptions, filters and the group structure change `nodes.json` and reach
sing-box only with the next pipeline build.

## Minimal config

```json
{
  "api": { "secret": "<sing-box API service secret>" },
  "subscriptions": [
    { "tag": "PROV", "url": "https://<subscription>" }
  ],
  "groups": [
    { "tag": "all" }
  ],
  "admin": { "token": "<admin panel token>" }
}
```

## Full example

Default values are shown so that all fields are visible.

```json
{
  "$schema": "config.schema.json",

  "api": { "url": "http://127.0.0.1:9090", "secret": "<secret>", "timeout": "5s" },

  "subscriptions": [
    { "tag": "LUNA", "url": "https://<…>", "user_agent": "clashmeta" },
    { "tag": "STAR", "url": "https://<…>", "send_device": true, "exclude_names": ["promo"] },
    { "tag": "HAPP", "url": "happ://crypt5/<…>", "happ_headers": { "User-Agent": "Happ/3.0" } },
    { "tag": "AWG", "type": "folder", "path": "awg", "format": "awg", "ext": ".conf" },
    { "tag": "OLD", "url": "https://<…>", "enabled": false },
    { "tag": "WH", "url": "https://<…>", "branch": "clients" },
    { "tag": "my", "type": "inline", "outbounds": [ { "type": "vless", "server": "<…>", "server_port": 443 } ] }
  ],

  "tests": {
    "connectivity": { "service": "cloudflare" },
    "latency": { "url": "https://speed.cloudflare.com/__down?bytes=0" },
    "jitter": { "samples": 5, "interval": "200ms" },
    "download": { "url": "https://speed.cloudflare.com/__down?bytes=5000000", "duration": "20s", "window_mb": 2 },
    "reachability": { "urls": ["https://www.google.com/generate_204"], "timeout": "8s", "workers": 8 },
    "for_region": {
      "ru": {
        "latency": { "url": "https://<address in Russia>" },
        "download": { "url": "https://<address in Russia>" }
      }
    }
  },

  "checks": {
    "country": { "ttl": "24h" },
    "ip_type": { "ttl": "24h", "url": "http://ip-api.com/batch", "via_tester": true },
    "geo_consistent": { "ttl": "24h" },
    "gemini": { "ttl": "24h", "attempts": 3, "timeout": "20s" },
    "openai": { "ttl": "24h", "attempts": 3, "timeout": "15s" },
    "anthropic": { "ttl": "24h", "attempts": 3, "timeout": "15s" },
    "heavy": { "ttl": "6h", "url": "https://speed.cloudflare.com/__down?bytes=20000000", "duration": "30s", "window_mb": 2 }
  },

  "regions": { "asia": ["jp", "sg", "kr"] },

  "groups": [
    { "tag": "eu", "require": { "country": { "regions": ["eu"] } } },
    { "tag": "us", "require": { "country": { "countries": ["us"] } } },
    {
      "tag": "ai",
      "strict": true,
      "in_global": false,
      "require": {
        "country": { "exclude_countries": ["ru"] },
        "geo_consistent": {},
        "gemini": {}, "openai": {}, "anthropic": {}
      },
      "prefer": { "ip_type": { "kinds": ["residential", "mobile"] } }
    }
  ],

  "rating": {
    "alpha": 0.3,
    "flap_lambda": 0.5,
    "availability": { "floor": 0.7, "full": 0.9 },
    "thresholds": {
      "latency": { "good": "150ms", "bad": "1.5s" },
      "jitter": { "good": "15ms", "bad": "250ms" },
      "loss_gate_percent": 50,
      "download_mbps": { "min": 2, "target": 25 },
      "throttle": { "bad": 0.3, "good": 0.9 },
      "variation": { "good": 0.1, "bad": 0.8 }
    },
    "weights": { "reliability": 0.35, "consistency": 0.2, "throttle": 0.15, "jitter": 0.1, "latency": 0.1, "throughput": 0.1 }
  },

  "selection": {
    "reserve": 3,
    "quality_margin": 15,
    "confirm_cycles": 2,
    "comfort_floor": 50,
    "tier_floor": 75,
    "cooldown": "5m",
    "host_penalty": "3h",
    "rotation": {
      "enabled": true, "interval": "3h", "jitter": "15m",
      "min_dwell": "30m", "min_score": 55, "avoid_recent": 2,
      "balance": { "window": "24h", "provider": 1.0, "country": 1.0, "protocol": 0.3 }
    },
    "quarantine": { "after_skips": 32, "duration": "72h", "low_score": 0 }
  },

  "tester": {
    "socks": "socks5h://127.0.0.1:2080",
    "selector": "nodes-tester",
    "switch_delay": "1s",
    "request_timeout": "10s",
    "host_gap": "2m"
  },

  "monitor": {
    "interval": "10s",
    "reassert_drift": true,
    "silence": "5m",
    "silence_floor_mb": 0,
    "probe": { "url": "", "size_mb": 1, "min_mbps": 1, "timeout": "8s" },
    "fails": 2,
    "retry": "30s",
    "error_burst": 5
  },

  "pipeline": {
    "timeout": "30m",
    "fetch": { "timeout": "2m", "retries": 3, "proxy": "", "keep_stale": "48h", "min_ratio": 0.5, "happ_keys_url": "" },
    "router": {
      "enabled": true,
      "at": ["03:00"],
      "exclude_types": ["shadowsocksr"],
      "domain_resolver": "bootstrap",
      "selector": { "interrupt_exist_connections": true },
      "urltest": { "interval": "5m", "tolerance": 100, "idle_timeout": "5m", "interrupt_exist_connections": true }
    },
    "clients": {
      "enabled": true,
      "at": ["00:30", "06:30", "12:30", "18:30"],
      "exclude_types": ["shadowsocksr"],
      "exclude_protocols": ["xhttp", "wg"],
      "domain_resolver": "dns-whitelist"
    }
  },

  "storage": { "path": "results/stats.db", "retention": "30d", "traffic": { "enabled": true, "poll": "5s", "flush": "1m" },
               "vacuum": { "every": "30d", "at": "04:00" } },

  "admin": { "enabled": true, "listen": "192.168.1.1", "port": 8088, "token": "<token>", "public_read": true, "refresh": "10s" },

  "notify": {
    "name": "<router name>",
    "telegram": { "token": "<bot token>", "chat_id": "<chat_id>" },
    "events": ["switch", "failsafe", "rollback", "pipeline", "subscription", "expiry", "singbox"],
    "switch_reasons": ["emergency", "emergency-stuck"],
    "expiry_warning": "3d"
  },

  "singbox": {
    "init": "/etc/init.d/sing-box",
    "killswitch": { "init": "/etc/init.d/killswitch", "table": "killswitch" },
    "watch": "5s",
    "down_alert": "1m"
  }
}
```

## api

The sing-box 1.14 API service (`services[]` with `"type": "api"` in the sing-box config). Through it the tester
switches selectors and reads traffic counters.

| Field | Type | Default | Description |
|---|---|---|---|
| `url` | string | `http://127.0.0.1:9090` | API address |
| `secret` | string | — | API secret |
| `timeout` | duration | `5s` | Request timeout |

## subscriptions

Node sources. The build (`nodes_config`) takes all nodes of the subscriptions of its branch; which of them to activate
is decided by the tester by the group checks. The country in the provider's node name is not used.

| Field | Type | Default | Description |
|---|---|---|---|
| `tag` | string | — | Provider name, unique; the first part of node tags |
| `enabled` | bool | `true` | Use the subscription |
| `branch` | `router` · `clients` · `both` | `router` | Build branch: router `nodes.json`, client `whnodes.json` or both |
| `type` | `url` · `file` · `folder` · `inline` · `amnezia` | `url` | Source kind |
| `url` | string | — | Subscription URL, share link, `happ://crypt…` or the Amnezia Premium `vpn://` link |
| `file` | string | — | `file`: path to the subscription file (relative to `config.json`) |
| `path`, `format`, `ext` | strings | — | `folder`: directory, format (`awg`) and file extension (`.conf`); `amnezia`: directory of the country files |
| `gateway_key` | string | — | `amnezia`: file with the public key of the Amnezia gateway (PEM), relative to `config.json` |
| `slots` | integer | all slots | `amnezia`: how many country files to keep |
| `min_rating` | number | `40` | `amnezia`: a country whose node keeps a lower rating is replaced |
| `exclude_countries` | list of strings | `["ru"]` | `amnezia`: country codes that are not issued |
| `outbounds` | array | — | `inline`: your own sing-box outbounds; the build sets their tags |
| `user_agent` | string | Safari | User-Agent of the HTTP download: `curl`, `clashmeta` or your own string |
| `happ_headers` | object | Happ set | `happ://`: headers on top of the default set |
| `send_device` | bool | `false` | URL subscription: send the HWID and device description |
| `exclude_names` | list of strings | — | Skip nodes whose name contains the substring |

The router HWID is stored in `device.json`, the `happ://crypt…` keys in `happ-keys.json`; both are next to
`config.json` and are created automatically.

### Amnezia Premium

A subscription with `type: "amnezia"` takes the `vpn://` link of Amnezia Premium and keeps AmneziaWG files of
countries in `path` (`<country code>.conf`, rights 0600). The files are issued by the tester through the Amnezia gateway
(`gw.amnezia.org`, with `pipeline.fetch.proxy` if it is set) once every 3 hours:

- a country whose node keeps a rating below `min_rating` for 6 hours is considered blocked: its file is revoked and
  deleted, and the country is not issued again;
- free places, up to `slots` and the free slots of the subscription, get random countries not issued before;
- a file whose server was updated after the issue is issued anew.

If all rated countries are below the threshold at once, the cause is not the countries (the protocol is blocked or the
network is down), and nothing is replaced. Countries issued elsewhere (no file in `path`) are not touched: a new issue
would break that file. The memory of the tried and blocked countries is `state.json` in `path`; delete a country from
`blocked` to allow it again. New files reach sing-box with the next subscription poll and router build.

The gateway key is not part of the project. It is the `-----BEGIN PUBLIC KEY-----` block in the official AmneziaVPN
app: for example, in `lib/<abi>/libAmneziaVPN_<abi>.so` of the Android APK. The application has two keys; the
working one is accepted by the gateway, the other one gets HTTP 500.

## tests

Rating measurements: `connectivity`, `latency`, `jitter`, `download`, `reachability`. The tester
checks a node when its measurement has expired (see
[result lifetimes](../nodes_tester/README.md#result-lifetimes)). The fields set lifetimes, addresses and timeouts;
unset ones come from the test code, `timeout` from `tester.request_timeout`.

Each test has `ttl` (`3h` by default) and `ttl_jitter` (a tenth of `ttl` by default):
a result lives from `ttl − ttl_jitter` to `ttl`. The lifetime is set for the whole test, `for_region` does not have it.

| Test | Fields |
|---|---|
| `connectivity` | `service` (`cloudflare` by default): the exit IP and country service; `timeout` |
| `latency` | `url`, `timeout` |
| `jitter` | `url`, `samples`, `interval`, `timeout` |
| `download` | `url`, `duration`, `window_mb`, `timeout` |
| `reachability` | `urls`, `timeout`, `workers` |

`for_region` holds test fields for nodes with an exit in the region (by the measured country). The block is written either
inside the test (`"latency": {"for_region": {"ru": {"url": "…"}}}`) or as a shared section
`tests.for_region.<region>.<test>`.

## checks

Checks with a “yes / no / unknown” verdict and a lifetime `ttl`; `ttl_jitter` (a tenth of
`ttl` by default) shortens the lifetime by a random share so that node verdicts do not expire
at once. These are the defaults; a group refers to a check by name in `require` or
`prefer`.

| Check | What it checks | Fields and defaults |
|---|---|---|
| `country` | exit country by the connection measurement | `ttl` `24h` |
| `ip_type` | exit IP type by ip-api: `residential`, `mobile`, `hosting`, `proxy` | `ttl` `24h`, `url`, `timeout` `10s`, `via_tester` `true` |
| `geo_consistent` | the IP country by the database matches the measured one | `ttl` `24h` |
| `gemini`, `openai`, `anthropic` | access to the AI service | `ttl` `24h`, `attempts` `3`, `timeout` |
| `upstream` | whether an upstream proxy works through the node: a sing-box outbound of type `socks` with `detour` to the group | `ttl` `6h`, `outbound` (required if the check is in a group), `url` `https://www.cloudflare.com/cdn-cgi/trace`, `attempts` `2`, `timeout` `15s` |
| `heavy` | a heavy download of reserve candidates; slower than `min_mbps` is a failure | `ttl` `6h`, `url`, `duration` `30s`, `window_mb` `2`, `min_mbps` `5` |
| `whitelist` | whether the whitelist mode lets the node through: the server IP is in the IP list and the SNI in the domain list (domain and subdomains); without TLS the IP is enough | `ttl` `24h`, `ip_list`, `sni_list` (text lists of [ru-mobile-whitelist](https://github.com/ancored/ru-mobile-whitelist)), `dns` `dns-ru`, `timeout` `10s` |

`whitelist` is a local check (every 5 minutes the tester looks for nodes with an expired verdict); no traffic goes through the node. The node server domain is resolved
with a DoH request to the `dns` server: a tag of a `type: https` DNS server from the running sing-box config
(`singbox.config`), its `type: group` group (the `https` servers in turn) or a URL; if none is found, Cloudflare
`1.1.1.1` (Quad9 requires HTTP/2). The router system resolver is not used: it would send the node name to the
provider.

`upstream` is needed when the exit goes through a group as a chain, for example a SOCKS proxy with a static IP
(`"detour": "ai-auto-out"`). A node may be alive but not allow connections to the proxy port, or the proxy itself
does not accept its IP; then the exit is dead even though the other checks pass the node. The reserve completes
the SOCKS5 handshake with the `outbound` server through the node (login and password from the running sing-box config) and requests
`url`; the node passes if all attempts succeed.

The tester runs expensive checks (AI, `heavy`) only for nodes that already passed the cheap ones: the gate and
the required country of the group.

## regions

Named lists of countries for group conditions and `for_region` of tests. Built-in: `eu` (European countries),
`us`, `ru` and `other`: everything not in `eu`, `us` and `ru`. A built-in region can be
overridden.

```json
"regions": { "us": ["us", "ca"], "asia": ["jp", "sg", "kr"] }
```

## groups

A group is a sing-box selector `{tag}-auto-out`. It has all nodes of the `router` branch, the fallback outbound and, with
`direct_fallback`, `direct-out`. The tester picks the active node.

| Field | Type | Default | Description |
|---|---|---|---|
| `tag` | string | — | Lowercase Latin letters, digits, `_`; not `global`, `nodes`, `failsafe` |
| `strict` | bool | `false` | Without candidates: `block-out` (the group traffic is blocked) instead of `failsafe-out` |
| `direct_fallback` | bool | `false` | Add `direct-out`; a strict group without candidates goes to it instead of `block-out` |
| `in_global` | bool | `true` | Part of `global-auto-out` |
| `reserve` | number | `selection.reserve` | Reserve candidates |
| `tier_floor` | number | `selection.tier_floor` | A lower rating puts the node into a tier after all other nodes of the group |
| `require` | object | — | Required checks: without a fresh success the node is not a candidate |
| `prefer` | object | — | Preferred checks: successes move the node up the choice queue |

A check inside a group is an object, usually empty (`"gemini": {}`). It can set its own `ttl`, and
`country` and `ip_type` can set conditions:

- `country`: `countries`, `regions`, `exclude_countries`, `exclude_regions` (ISO codes and region names);
- `ip_type`: `kinds`, the allowed IP types.

A group candidate passes the gate, has a rating above zero, is not paused or quarantined, and all its
`require` checks are fresh and successful. Among the candidates, nodes with successes in
`prefer` go first, then by rating.

No candidates: a non-strict group switches to the shared `failsafe-out` (urltest over all nodes), a strict one
to `block-out`, and with `direct_fallback` to `direct-out`. The monitor watches `failsafe-out` as a node:
when the probe fails `monitor.fails` times in a row, a non-strict group with `direct_fallback` goes to `direct-out`, and
when `failsafe-out` responds again, it comes back to it. `block-out` is taken from the sing-box `base.json`
(`{"type": "block", "tag": "block-out"}`). Without a `groups` section a single group `all` is created.

## rating

Smoothing, thresholds and weights of the node rating.

| Field | Default | Description |
|---|---|---|
| `alpha` | `0.3` | EWMA smoothing |
| `flap_lambda` | `0.5` | Penalty for flapping |
| `availability.floor`, `availability.full` | `0.7`, `0.9` | Availability below which the rating is 0, and the availability of the full rating |
| `thresholds.latency` | `150ms` / `1.5s` | TTFB: `good`, `bad` |
| `thresholds.jitter` | `15ms` / `250ms` | Jitter: `good`, `bad` |
| `thresholds.loss_gate_percent` | `50` | Loss at which the node fails the gate, % |
| `thresholds.download_mbps` | `2` / `25` | Speed: `min`, `target` |
| `thresholds.throttle` | `0.3` / `0.9` | Throttling: `bad`, `good` |
| `thresholds.variation` | `0.1` / `0.8` | Variation: `good`, `bad` |
| `weights` | see the example | Weights of the components: `reliability`, `consistency`, `throttle`, `jitter`, `latency`, `throughput` |

## selection

Choosing the active node in groups.

| Field | Default | Description |
|---|---|---|
| `reserve` | `3` | Reserve candidates of a group (checked by the required checks and `heavy`) |
| `quality_margin` | `15` | How many points better a candidate must be for a replacement by quality |
| `reserve_margin` | `quality_margin` | How many points better a node must be than the worst reserve candidate (of the same tier) to take its place |
| `confirm_cycles` | `2` | How many measurements in a row (of the active node or the reserve leader) confirm a replacement by quality |
| `comfort_floor` | `50` | Above this rating the active node is not replaced by quality |
| `tier_floor` | `75` | A lower rating puts the node into a tier after all other nodes of the group: a weak node that passed the preferred checks (`prefer`) does not overtake strong ones that did not |
| `cooldown` | `5m` | Pause between switches of a group |
| `host_penalty` | `3h` | How long not to choose the host of a node that the monitor removed in an emergency (the probe failed); `0` means no penalty |
| `rotation.enabled` | `true` | Scheduled rotation |
| `rotation.interval`, `rotation.jitter` | `3h`, `15m` | Rotation period and random shift; each group has its own deadline from its switch, groups do not switch at once |
| `rotation.min_dwell` | `30m` | Minimum time on a node |
| `rotation.min_score` | `55` | Minimum rating of a rotation candidate |
| `rotation.avoid_recent` | `2` | Do not go back to the last N nodes |
| `rotation.balance` | off | Balancing by traffic; the section turns it on: `window` `24h`, axis strength `provider` `1.0`, `country` `1.0`, `protocol` `0.3` |
| `quarantine.after_skips` | `32` | After the n-th failure in a row the node is not checked for 2^(n-1) `connectivity` periods; once the skip reaches N, quarantine |
| `quarantine.duration` | `72h` | Quarantine duration |
| `quarantine.low_score` | `0` | Above zero: quarantine also for weak nodes with a lower rating |

## tester

| Field | Default | Description |
|---|---|---|
| `socks` | `socks5h://127.0.0.1:2080` | Tester SOCKS inbound; with `socks5h` the chosen node resolves the name |
| `selector` | `nodes-tester` | Tester selector in sing-box |
| `switch_delay` | `1s` | Pause after switching the tester selector |
| `request_timeout` | `10s` | Test request timeout |
| `host_gap` | `2m` | At most one request to a host with another port or protocol within this time |

## monitor

Supervision of active nodes: live traffic flows, the node is alive; quiet for longer than `silence`, an unscheduled
probe; `error_burst` connection errors through the group in the sing-box log within a tick, a probe right away; a failure, a retry
after `retry`; `fails` failures in a row, an emergency and a replacement. Errors are read from the `log.output` file of the running
sing-box config (`singbox.config`); if the log is not in a file, this signal does not work.

| Field | Default | Description |
|---|---|---|
| `interval` | `10s` | Monitor tick |
| `reassert_drift` | `true` | Bring back the selector choice if it was changed behind the tester's back |
| `silence` | `5m` | Quiet for longer: a probe |
| `silence_floor_mb` | `0` | MB per window that count as traffic; `0` means `probe.min_mbps × silence` |
| `probe.url`, `probe.size_mb` | speed.cloudflare.com, `1` | Probe address and size |
| `probe.min_mbps`, `probe.timeout` | `1`, `8s` | Minimum probe speed and timeout |
| `fails` | `2` | Failures in a row before an emergency |
| `retry` | `30s` | Probe retry after a failure |
| `error_burst` | `5` | Connection errors through the group per tick: a probe right away; `0` turns it off |

## pipeline

The pipeline: fetching subscriptions (`nodes_fetch`), building (`nodes_config`), `sing-box check` and
applying. The admin panel runs it by the `at` schedule or manually.

| Field | Default | Description |
|---|---|---|
| `timeout` | `30m` | Run duration limit |
| `fetch.timeout` | `2m` | Subscription download timeout |
| `fetch.retries` | `3` | HTTP retries |
| `fetch.proxy` | — | Download proxy, for example `socks5://127.0.0.1:2080` |
| `fetch.keep_stale` | `48h` | How long to keep the previous nodes of a failed subscription |
| `fetch.min_ratio` | `0.5` | If fewer nodes than this share remain, `raw` is not overwritten |
| `fetch.happ_keys_url` | built-in | Source of the `happ://crypt…` keys |
| `fetch.every` | `3h` | Poll router subscriptions separately from the build: only `raw/router.json`, without building and applying; counted from the previous poll or router build. `0s`: subscriptions are polled only by the build |

The `router` branch (result: `nodes.json` for the router sing-box) and the `clients` branch (`whnodes.json` for
client configs) are configured the same way:

| Field | Default | Description |
|---|---|---|
| `enabled` | `true` | Scheduled build |
| `at` | `["03:00"]` for `router`, empty for `clients` | Start time, `HH:MM` |
| `exclude_types` | — | Skip outbounds of these types, for example `shadowsocksr` |
| `exclude_protocols` | — | Skip protocols and transports: `reality`, `xhttp`, `wg`… |
| `domain_resolver` | `bootstrap` | DNS server tag for node addresses |
| `selector` | — | Fields of the sing-box group selectors, for example `interrupt_exist_connections` |
| `urltest` | — | Fields of the `failsafe-out` urltest: `url`, `interval`, `tolerance`, `idle_timeout` |
| `rebuild` | see below | `router` only: rebuild on degradation |

Rebuild on degradation (`pipeline.router.rebuild`). A sing-box restart drops connections, so
the tester asks the pipeline to build the config anew only when both conditions hold: a group has been without
candidates longer than `degraded_for` (`15m`) or has had fewer candidates than the reserve + 1 longer than
`few_candidates_for` (`1h`); and the last subscription poll has new accesses, and of the dead nodes of the
config at least `gone_share` (`0.3`) are gone from the subscriptions. At most once per `min_interval` (`6h`) after the previous
router build and only with `pipeline.router.enabled`. A fresh poll comes from `fetch.every`; with `0s`
the comparison is against the raw of the last build and the rebuild does not trigger. Each rebuild goes to the log and a `pipeline` notification.

Branch filters protect sing-box from outbounds it would not accept; bare vless and vmess (without TLS and Reality)
are always dropped. The client branch builds a single group `global-auto-out` with all nodes and
`failsafe-out`.

## storage

| Field | Default | Description |
|---|---|---|
| `path` | `results/stats.db` | SQLite file; a relative path is from the data directory |
| `retention` | `30d` | How long to keep the history |
| `traffic.enabled` | `true` | Collect user traffic by node |
| `traffic.poll`, `traffic.flush` | `5s`, `1m` | Counter polling and writing to the database |
| `vacuum.every` | `30d` | Repack the database (`VACUUM`) once per this period; `0s` means manually from the admin panel only |
| `vacuum.at` | `04:00` | Start time (local) after the period expires |

The result of the last `VACUUM` (the database size before and after) and the “Run now” button are in the admin panel, the
“Settings” section. The period is counted from the first tester start.

## admin

The web admin panel built into the tester process.

| Field | Default | Description |
|---|---|---|
| `enabled` | `true` | Enable the admin panel |
| `listen` | `0.0.0.0` | Listen address; do not expose it to the outside |
| `port` | `8088` | Port |
| `token` | — | Administrator token; empty turns editing and control off |
| `public_read` | `true` | Viewing without the token |
| `refresh` | `10s` | Data refresh in the browser |

## notify

| Field | Default | Description |
|---|---|---|
| `enabled` | `true` | Send notifications |
| `name` | — | Router signature at the start of the message |
| `telegram.token`, `telegram.chat_id` | — | Bot and recipient |
| `webhook.url` | — | POST JSON `{event, text, ts}` |
| `proxy` | — | Proxy for sending |
| `via_tester` | `true` | If sending directly failed, retry through the tester SOCKS |
| `events` | all | `switch`, `failsafe`, `rollback`, `pipeline`, `subscription`, `expiry`, `singbox` |
| `switch_reasons` | `emergency`, `emergency-stuck` | Switch reasons to report; also `init`, `rotation`, `quality`, `manual` |
| `expiry_warning` | `3d` | Warn about the subscription end in advance |
| `dedupe` | `1h` | Do not repeat the same message more often |
| `max_per_hour` | `20` | Message limit per hour |
| `timeout` | `10s` | Sending timeout |

## singbox

Stopping and starting sing-box and the kill switch service from the admin panel (OpenWrt).

| Field | Default | Description |
|---|---|---|
| `init` | `/etc/init.d/sing-box` | sing-box init script |
| `config` | `/etc/sing-box/config.json` | The running config: `checks.whitelist.dns` takes the DNS server by tag from it, `checks.upstream.outbound` the proxy server and credentials |
| `killswitch.init`, `killswitch.table` | `/etc/init.d/killswitch`, `killswitch` | Init script and the `inet` nftables table of the kill switch |
| `watch` | `5s` | Watchdog period |
| `down_alert` | `1m` | Notify if sing-box is down for longer |

## Secrets

`api.secret`, `admin.token`, `notify.telegram.token` and `subscriptions[].url` give access to sing-box,
the admin panel, the bot and the subscriptions. Replace them before publishing a config fragment.
