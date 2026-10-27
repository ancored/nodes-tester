**English** | [Русский](README.ru.md)

# nodes_tester

`nodes_tester` checks the leaf nodes already loaded into sing-box, computes the rating,
controls automatic switching and keeps the history in SQLite.

The package does not fetch subscriptions and does not create the sing-box configuration. That is the job of
[`nodes_fetch`](../nodes_fetch/README.md) and [`nodes_config`](../nodes_config/README.md).

## How a check works

It needs:

1. a running sing-box 1.14 or newer with the API service (`services[type=api]`);
2. a SOCKS5 inbound that carries the test traffic;
3. a flat selector `nodes-tester` with the leaf nodes as direct members;
4. [`config.json`](../docs/CONFIG.md) with the API service address (`api`), the tester SOCKS5 (`tester.socks`)
   and the groups (`groups`).

There are no passes: each measurement has its own lifetime, and the Runner checks only nodes whose
measurements have expired. For such a node it runs the cycle:

```text
select the node through the sing-box API service
    -> send the checks through SOCKS5
    -> save the raw results
    -> update the rating and restrictions
    -> switch the working selector if needed
```

The tester does not start its own proxy core. All traffic goes through the sing-box
it controls through its API service.

## Running

```sh
python3 -m nodes_tester -c config/config.json
python3 -m nodes_tester -c config/config.json --once
python3 -m nodes_tester --list-tests
python3 -m nodes_tester -c config/config.json --vacuum
```

The OpenWrt package uses these commands:

```sh
nodes-tester tester
nodes-tester tester --list-tests
nodes-tester vacuum
```

`--once` checks all nodes once without waiting for the deadlines and exits. `--vacuum` repacks the
database manually; by the `storage.vacuum` schedule (by default every 30 days at 04:00) the tester
does it itself; the result and the start button are in the admin panel (“Settings”).

## Node choice and visiting order

The Runner reads the members of the `tester.selector` selector (`nodes-tester` by default): it has all
nodes. A leaf node must have the `[node_crc.cred_crc]` key at the end of its tag; groups and entries without a key
are skipped.

The node country is only the measured one: `connectivity` shows it on every measurement. It also
sets the node region for test addresses (`for_region` in the `tests` section); until the country is
measured, tests use the default addresses. The provider's node name is not used.

A new access of an already known node (the provider reissued the credentials, `node_crc` is the same)
gets its rating, country and check results right away, but passes the gate on its own; activity in the
selector is not inherited.

The node host is the server address (`server`, for WireGuard the address of the first peer), without the port and the provider.
Nodes of one host (different accesses, TLS settings, ports) fail and get blocked together: the
group reserve has one node per host and not from the host of the active node, rotation and replacement by quality
do not take a node of the active host, and an emergency takes one only if there are no other candidates.

The queue takes the host into account: of the nodes due for a check, the next one is a node of the
host that has not been contacted for the longest time, and `tester.host_gap` limits how often
one host is contacted with different ports or protocols. This lowers the chance of
restrictions by the provider and TSPU.

## Result lifetimes

Each measurement (`tests.<name>.ttl`, `3h` by default) and each check (`checks.<name>.ttl`) lives
for its own period. The period is shortened by a random share `ttl_jitter` (a tenth of `ttl` by default):
the share is constant for the node and the measurement time, so nodes of one host checked one after another
spread out in time and do not expire in a batch.

- A node without measurements (a new one) is checked right away.
- Queue: first the active nodes of groups and reserve candidates with expired measurements, then
  the rest, starting from the oldest result. With equal deadlines (new nodes) the node of the host
  that has not been contacted for the longest goes first. If the queue cannot keep up with `ttl`, each
  node is refreshed once per full round, and the active ones and candidates by their own deadline.
- One visit to a node runs all its measurements that are closer to their deadline than their spread;
  `connectivity` always, and first. Without connectivity the rest is not run.
- A node no group needs (a fresh failure of a required check, usually the exit country, in every router
  group it belongs to and in every client group of its subscription) gets only `connectivity`, an hour
  before its country expires (`checks.country.ttl`).
- The “Run testing now” button in the admin panel and `--once` check all nodes without waiting for
  the deadlines.

## Measurements and checks

Rating measurements (the `tests` section) run by their lifetimes:

| Test | What it measures |
|---|---|
| `connectivity` | internet access, the external IP, country and colo |
| `latency` | TTFB |
| `jitter` | mean, minimum and maximum latency, jitter and loss |
| `download` | speed, stability and signs of throttling |
| `reachability` | availability of a set of targets |
| `google_country` | the country in which Google sees the exit (the `X-Country` header of the Chrome variations server, once a day); not part of the rating |

`connectivity` is the GATE: its failure zeroes the total score.

Checks (the `checks` section) give a “yes / no / unknown” verdict with a lifetime `ttl`:

| Check | What it checks | Who runs it |
|---|---|---|
| `country` | the exit country fits the group | the `connectivity` measurement |
| `ip_type` | exit IP type: residential, mobile, data centre, proxy | the tester every 5 minutes (ip-api) |
| `geo_consistent` | the IP country by the database matches the measured one | the tester every 5 minutes (ip-api) |
| `gemini`, `openai`, `anthropic` | the AI service lets the node in | the reserve |
| `upstream` | an upstream proxy works through the node (an outbound with `detour` to the group) | the reserve |
| `heavy` | a heavy control download | the reserve |
| `whitelist` | the server IP and SNI are in the Russian mobile internet whitelists (without TLS, the IP) | the tester every 5 minutes, locally |

A group ([`groups`](../docs/CONFIG.md#groups)) lists checks in `require` (required) and
`prefer` (preferred). A group candidate is a node without a GATE failure, with a rating above zero, without
a pause or quarantine and with fresh successes of all required checks. Candidate order: first
fewer failed preferred checks, then by rating. An example for an AI group:

```json
{
  "tag": "ai",
  "strict": true,
  "require": { "country": { "exclude_countries": ["ru"] }, "geo_consistent": {},
               "gemini": {}, "openai": {}, "anthropic": {} },
  "prefer": { "ip_type": { "kinds": ["residential", "mobile"] } }
}
```

Without a single candidate the group switches to the fallback outbound: a non-strict one to `failsafe-out`,
a strict one to `block-out` (the group traffic is blocked), a strict one with `direct_fallback` to `direct-out`.
As soon as a candidate appears, the group comes back to it. While the group is on `failsafe-out`, the monitor
watches it as the active node (traffic through the node it chose, or a probe through `nodes-tester`):
after `monitor.fails` failures in a row a non-strict group with `direct_fallback` goes to `direct-out`,
and when the probe answers it comes back to `failsafe-out`.

The tester runs expensive checks (AI, `heavy`) only for nodes that already passed the cheap ones: the GATE,
the country and the IP type of the group.

Each group has its own rotation deadline: `interval` ± `jitter` from its last switch, so groups
do not switch at once. If a rotation did not happen (one candidate or none, an emergency without a replacement, an
API failure), the deadline moves the same way: after the group is evaluated it is always in the future.

Once a minute the tester checks the membership of its selector in sing-box. New nodes go straight into
the database (into “All nodes”, without measurements) and into the queue. Nodes gone from subscriptions disappear
from the admin panel tables and stay only in the “Archive”.

While the pipeline applies a config, the tester waits, and a measurement that hit a sing-box restart is not
counted. A measurement is also not counted if sing-box was restarted behind the admin panel's back
(a core update, a pipeline from cron): the sing-box API does not respond or the process PID changed; then
the tester waits for a minute. A node that is no longer in the selector is skipped without a GATE failure.

### AI checks

`gemini` opens gemini.google.com `attempts` times (3 by default) and takes from the page the code of
the country in which Google placed the client. The node passes only if each attempt gave
HTTP 200 and a country outside the forbidden ones (RUS, BLR, CHN, HKG, MAC, IRN, PRK, CUB, SYR). One attempt
is not enough: some nodes exit sometimes over IPv4, sometimes over IPv6 of Cloudflare WARP, and then Google sees
a foreign country one time and Russia another time. The page is heavy, so the check has its own `timeout` (`20s`).
A timeout or a drop says nothing about the country: if the other attempts gave an allowed country,
the result is inconclusive and the previous verdict of the node stays; if all attempts failed on the network,
it is a failure.

`openai` and `anthropic` work the same way (`attempts`, `timeout`, `ttl`), but do not determine the country:
OpenAI and Claude take it from Cloudflare, and `connectivity` already shows it. The checks look at the
decision of the service itself. `openai` requests `api.openai.com/compliance/cookie_requirements`:
from a forbidden region the error `unsupported_country…` comes back. `anthropic` requests
`api.anthropic.com/v1/models` without a key: from a forbidden region the error is `forbidden`, from an
allowed one `authentication_error`. The claude.ai site is not suitable for the check: data centre IPs
get a Cloudflare challenge there, which a browser passes and the tester does not. An unrecognised
answer counts as a failure.

Network tests use traffic. Sizes, URLs and timeouts are in the `tests` and `checks` sections.

## Rating

The rating is built from the normalised factors of reliability, consistency, throttling, jitter,
latency and speed. Then EWMA, availability and a flapping penalty are applied. The state
is stored in the `scores` and `score_history` tables, not in CSV.

Practical consequences:

- `score = 0` may mean a GATE failure or a lack of enough scoring measurements;
- see the physical speed and latency values in `results`;
- the instant score is computed from the latest result of each test of the node, even if the measurements
  were taken at different times;
- changing weights and thresholds affects the next rating updates.

Thresholds and weights are set in the `rating` section. Their current defaults are in
[`config.py`](config.py) and [`config/config.example.json`](../config/config.example.json).
The full formula and interpretation rules: [`SCORING.md`](SCORING.md).

## Group reserve

A background process ([`reserve.py`](reserve.py)) keeps `selection.reserve` (3 by
default; a group has its own `reserve`) checked candidates in each group to replace the active node. Tester
measurements only rate nodes, the reserve does not depend on them:

1. Contenders are taken by the tier of preferred checks and by rating; nodes with a rating below
   `selection.rotation.min_score` go last. The active node and the last `avoid_recent` ones do not get into
   the reserve.
2. A contender passes the missing required checks of the group that the reserve runs (AI),
   then `heavy`. A fresh result is not repeated. A failure knocks the node out, and the next one
   is checked. A node that passed also runs the preferred AI checks of the group: their failure only
   lowers the tier. A refusal of the `heavy` download server (429/5xx, 401/403/404) gives no verdict.
3. After the active node changes (rotation, emergency, quality) the reserve is refilled right away. For reserve members
   the deadlines are watched: when a check expires, it is repeated. The required checks of the active
   node are repeated 5 minutes before they expire; a failure or an expired result means a replacement from the reserve.
4. When contenders run out, the pause or quarantine is lifted one node at a time from the nodes that have been
   restricted the longest, and the node is checked with light tests right away. Nodes that
   certainly will not fit the group (a known wrong exit country, a fresh failure of a required
   check) and nodes of the hosts of reserve members, the active node and penalised hosts are not lifted. At most three
   lifts per group per round, other groups do not wait. A GATE failure puts the node back into quarantine;
   such a node is lifted again no earlier than in 6 h. A ban is not lifted. If there is nobody to lift and
   the reserve is not full, a new attempt comes in 15 minutes or when new nodes appear.
5. When the reserve is full, after each measurement it is checked whether there is a node better than the worst candidate: a higher
   tier or a rating higher by `selection.reserve_margin`. Such a node runs the checks of
   step 2 and takes its place; if it does not pass, the reserve does not change, and the node itself is not
   tried for 15 minutes.

The reserve flag and the group name are shown in the admin panel (rating, lifecycle, control).

## Switching

The Switcher uses the rating, restrictions, the reserve and the current active node. It supports:

- an emergency replacement of an unavailable node from the reserve (if the reserve is empty, by any passed candidate
  of another host). If the monitor removed the node, its host is not chosen for `selection.host_penalty` (3 h)
  either into the reserve or as a replacement;
- a replacement by quality with a node from the reserve when the advantage holds for `confirm_cycles` measurements
  in a row of the active node or the reserve leader;
- periodic rotation to a node from the reserve;
- a minimum holding time and cooldown;
- excluding recently used candidates;
- additional balancing by provider, country, protocol and traffic.

A group is evaluated after each measurement of its active or reserve node (and a group without an active node
or in an emergency without a replacement, after a measurement of any of its nodes), rotation by its deadline.

Automatic switching changes the live selectors in sing-box. Scheduled rotation can be
turned off: `selection.rotation.enabled: false`.

## Rebuild on degradation

On each monitor tick [`rebuild.py`](rebuild.py) checks whether it is time to build the config anew: a group
has been without candidates longer than `degraded_for` or with fewer candidates than the reserve + 1 longer than `few_candidates_for`,
and a build from the last subscription poll will fix it (there are new accesses, and the dead nodes of the config are gone
from the subscriptions). Then the tester starts the `router` pipeline marked “rebuild on degradation”, not
more often than `min_interval` and only with the router build enabled. The fields are `pipeline.router.rebuild`
([CONFIG.md](../docs/CONFIG.md#pipeline)).

## Client groups

The tester measures the nodes of client subscriptions like any other: they are in the `nodes-tester` selector, but in no
router group. Every 5 minutes it writes to `client-candidates.json` (next to `nodes.json`) the nodes of each client group
(`pipeline.clients.groups`): nodes of `clients` and `both` subscriptions with a rating above 0, without a pause, quarantine or
exclusion, and with a fresh success of every required check of the group. The client build takes the nodes from this file; the
time of the record shows that the tester works ([CONFIG.md](../docs/CONFIG.md#pipeline)).

## Amnezia Premium countries

For subscriptions with `type: "amnezia"` [`amnezia_slots.py`](amnezia_slots.py) once every 3 hours compares the
country files with the rating of their nodes (a node is found by the server address and port from the file: countries of a subscription may share one address): it revokes a
country whose node stays below `min_rating` for 6 hours, fills free places with random new countries (when none are left, with the longest-blocked ones) and issues anew a
file whose server was updated. The rules are in [CONFIG.md](../docs/CONFIG.md#amnezia-premium); the results
go to the log and to the `subscription` notification.

## Monitor, pause and quarantine

The Monitor watches the active nodes between their measurements. Connection errors of users through the group
in the sing-box log decide first: at least 3 errors in a minute and at least 2 in the minute before it
(`monitor.errors`, a group can have its own rule) replace the node at once, without a probe. During the first
10 minutes after a node is chosen 2 errors in a minute are enough; errors of connections started before the switch
are not counted. When there is user traffic, the node is considered alive. After a quiet period the monitor runs a
short SOCKS probe, but only if the node had connections: an unused group is not probed. A burst of errors within a
tick (`monitor.error_burst`) starts a probe right away. A failed probe is repeated after `monitor.retry` (30 s);
`fails` failures in a row start an emergency choice. The error text goes to the log.

[`outage.py`](outage.py) tells a general outage from a node failure: the direct exit is checked every minute past
sing-box, and after 3 emergency replacements within 10 minutes also up to 3 nodes of different providers. While
the WAN or all those nodes are down, measurements, the reserve, rotations and replacements stop
(`monitor.outage`).

After the n-th GATE failure in a row the node is paused for 2^(n−1) `connectivity` periods. When the skip
grows to `selection.quarantine.after_skips`, the node goes into quarantine for `duration`, then
gets one probe. A successful GATE lifts the restriction.

`selection.quarantine.low_score` (0 by default, off) also quarantines live but weak
nodes: a rating below the threshold after at least 3 measurements. Active nodes are not affected. After
the quarantine the node comes back if its rating by the probe rose to the threshold.

If there are no replacement candidates, the pause and quarantine are lifted one node at a time by the reserve process
(see “Group reserve”).

Manual ban and quarantine are available in the admin panel. A ban does not remove the node from the subscription and does not erase
its history.

## Storage

SQLite (`storage.path`) contains:

- `nodes` and `endpoints`: the current membership and network addresses;
- `results`: raw check results;
- `scores` and `score_history`: the current rating and its history;
- `traffic`: user traffic aggregates;
- `garbage`: pauses and quarantine;
- `activations`, `switch_state`, `switch_recent`, `switch_activations`: the choice state;
- `node_events`: lifecycle events;
- `meta`: service values.

Cleanup by `storage.retention` runs periodically, `VACUUM` by the `storage.vacuum` schedule.
Do not copy just `stats.db` from a running WAL database: stop the process or use the
SQLite backup.

The table structure, cleanup and backups are described in [`STORAGE.md`](STORAGE.md).

## Exit IP type

Every 5 minutes the exit IPs of nodes (`exit_ip` from `connectivity`) without fresh data
are checked with one batch request to ip-api.com through the tester SOCKS on the best healthy node:
DNS filters (NextDNS and blocklists) often block ip-api.com on the router; on failure the request
goes directly (`checks.ip_type`: `url`, `ttl`, `ttl_jitter`, `timeout`, `via_tester`). The data (ASN, network,
signs of a data centre, a mobile network and a known proxy) is cached in the `ip_info` table for
`ttl`. “Results” shows the IP type (DC, residential, mobile) and a mismatch between the country by the database
and the country by Cloudflare. They affect the node choice through the `ip_type` and `geo_consistent` checks of
groups.

The same request covers the entry IPs: the addresses of the node servers, resolved through the DoH servers of the
`bootstrap` group of the sing-box config and kept in the `entry_dns` table for the same `ttl`. A node whose exit IP is none
of its entry IPs is a chain; the admin panel shows it as entry country → exit country and marks an exit through
Cloudflare WARP (AS13335).

## Notifications

`notify` sends messages to Telegram (`telegram.token`, `telegram.chat_id`) and/or a
webhook (`webhook.url`, POST JSON `{event, text, ts}`). The router's own traffic often
bypasses the proxy, and Telegram is not reachable directly: if sending failed, it is repeated
through the tester SOCKS on the best healthy node (`via_tester`, on by default); `proxy`
sets your own proxy. Events (`events`):

| Event | When |
|---|---|
| `switch` | a node switch with a reason from `switch_reasons` (by default `emergency`, `emergency-stuck`) |
| `failsafe` | a group went to a fallback outbound: `failsafe-out`, `block-out` or `direct-out` |
| `rollback` | the new sing-box config gave no connectivity and the previous one came back |
| `pipeline` | a pipeline run ended with an error or a timeout |
| `subscription` | a subscription failed to fetch; it says how many hours are left before the previous nodes are lost |
| `expiry` | the subscription ends within `expiry_warning` or ≥ 90 % of the traffic is used (once a day) |
| `singbox` | sing-box stopped or started from the admin panel, the killswitch toggled, sing-box is down for longer than `singbox.down_alert` |

Sending runs in a background thread; a repeat of the same event is suppressed for `dedupe`, the overall
limit is `max_per_hour`. The “Send a test message” button in “Settings” uses the
settings of the running process.

## sing-box: stop and killswitch

The `singbox` section (OpenWrt only) gives the admin panel an emergency stop and start of sing-box. In TUN
mode sing-box removes its routes itself when it stops. While the stop flag is set (in tmpfs),
`apply-nodes.sh` does not apply a config; a router reboot resets the flag and starts sing-box.

The killswitch is a separate router service (`killswitch.init`, `/etc/init.d/killswitch` by default)
that keeps a persistent nftables table (`killswitch.table`) from boot and does not let the LAN reach the
internet bypassing sing-box. Its rules are set in the service itself; nodes-tester does not create them, it only
shows whether the table is loaded and how many packets were blocked, and turns the service on or off
(`start` + `enable`, `stop` + `disable`). Without the service the switch is not shown.

## Embedded admin panel

With `admin.enabled: true` the HTTP server starts inside the Runner and gets access to the
live status, the log and actions. A separate `python3 -m dashboard` works without the Runner:
it reads the database and files, but cannot start testing or do a live switch.

User guide: [`docs/USAGE.md`](../docs/USAGE.md). Technical description:
[`dashboard/README.md`](../dashboard/README.md).

## Code map

| File | Responsibility |
|---|---|
| `runner.py` | lifecycle, the queue by result lifetimes, rating measurements, checks for the reserve |
| `freshness.py` | result lifetime with a spread |
| `reserve.py` | group reserve: refilling and rechecking replacement candidates |
| `api.py` | selector control and reading groups through the sing-box API service |
| `proxy.py` | the SOCKS session of the tests |
| `tests/` | individual network checks |
| `scoring.py` | normalising one measurement |
| `scoreboard.py` | EWMA, availability and saving the rating |
| `switcher.py` | emergency, quality, rotation and manual choice |
| `monitor.py` | watching active nodes between measurements |
| `outage.py` | general outage: the WAN or all nodes at once; checks and switches stop |
| `rebuild.py` | rebuilding the config on degradation |
| `amnezia_slots.py` | Amnezia Premium countries by the rating |
| `traffic.py` | exact traffic accounting from the connection stream |
| `storage.py` | SQLite schema and operations |
| `vacuum.py` | `VACUUM` of the database by the `storage.vacuum` schedule and from the admin panel |
| `config.py` | loading `config.json`: sections, default values, errors with the field path |
| `logbuffer.py` | ring buffer of the log for the admin panel |
