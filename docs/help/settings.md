**English** | [Русский](settings.ru.md)

# Settings

This page edits the tester part of `config.json`: the connection to sing-box, checks, switching, the admin panel, storage and
notifications. It needs the administrator token. Fields not shown in the forms (thresholds and weights of the rating, test addresses,
check parameters) are edited in “Advanced JSON”; all of them are in the [config.json reference](../CONFIG.md).

The editing toolbar, “Review changes”, conflicts and the automatic restart work as on “Subscriptions and build”:
[Editing and saving](subscriptions.md#saving). An empty field means the default value shown in grey. After saving, the tester restarts
by itself: monitoring and checks stop for a few seconds, user connections stay.

<a id="connection"></a>
## Connection to sing-box

| Field | Meaning |
|---|---|
| sing-box API address | `api.url`, the address of the sing-box API service (`services[]` with `type: api`), `http://127.0.0.1:9090` |
| sing-box API secret | `api.secret`, the secret of the API service |
| API timeout | `api.timeout`, 5 s |
| Tester SOCKS | `tester.socks`, the SOCKS5 inbound that carries test traffic, `socks5h://127.0.0.1:2080`; with `socks5h` the node resolves names itself |
| Test selector | `tester.selector`, the flat selector with all nodes the tester switches, `nodes-tester` |

The admin panel does not check the connection when saving. If the tester cannot reach the API or the SOCKS, measurements stop: see
the [log](runs.md#log).

<a id="tester"></a>
## Tester passes

| Field | Meaning |
|---|---|
| Gap between requests to one host | `tester.host_gap`, 2 min: at most one check of a host with another port or protocol in this time ([why](checks.md#host-gap)) |
| Check timeout | `tester.request_timeout`, 10 s: the timeout of one test request |
| Reserve candidates per group | `selection.reserve`, 3: the default [reserve](groups.md#reserve) size; a group can set its own |

The tester measures all nodes; each group chooses from them by its own checks.

<a id="switching"></a>
## Switching and monitoring

| Field | Meaning |
|---|---|
| Rotation | `selection.rotation.enabled`: scheduled [rotation](switching.md#rotation) on or off |
| Rotation interval | `selection.rotation.interval`, 6 h |
| Minimum time on a node | `selection.rotation.min_dwell`, 30 min |
| Active node monitor tick | `monitor.interval`, 10 s ([monitor](switching.md#monitor)) |
| Quarantine duration | `selection.quarantine.duration`, 72 h ([quarantine](restrictions.md#quarantine)) |
| Quarantine after skipped connection periods | `selection.quarantine.after_skips`, 32 ([pause](restrictions.md#pause)) |

Turning rotation on may change the live choice of the groups after the tester restarts.

<a id="admin-storage"></a>
## Admin panel and storage

| Field | Meaning |
|---|---|
| Embedded admin panel | `admin.enabled` |
| Listen address, Admin panel port | `admin.listen`, `admin.port` (8088). Listen only on the LAN address; do not expose the admin panel to the internet |
| Administrator token | `admin.token`; empty turns editing and control off |
| Viewing without a token | `admin.public_read`: off — everything needs the token |
| SQLite file | `storage.path`, `results/stats.db`, relative to the data directory |
| Keep history for | `storage.retention`, 30 days: results, rating history, traffic and archived nodes older than this are deleted |
| Collect user traffic | `storage.traffic.enabled` ([traffic](traffic.md)) |
| Database VACUUM every, VACUUM time | `storage.vacuum.every` (30 days; `0s` — only by hand) and `storage.vacuum.at` (04:00) — see [Database maintenance](#vacuum) |

Changing the token, the address or the port takes effect after the restart; with a new address or port, open the admin panel at
the new address.

<a id="notifications"></a>
## Notifications

Messages to Telegram and/or a webhook.

| Field | Meaning |
|---|---|
| Send notifications | `notify.enabled` |
| Router signature | `notify.name`, put at the start of each message: useful with several routers |
| Telegram bot token, Recipient chat_id | `notify.telegram.token`, `notify.telegram.chat_id` |
| Webhook URL | `notify.webhook.url`: POST JSON `{event, text, ts}` |
| Proxy for sending | `notify.proxy`; empty — directly |
| Warn about the subscription end in advance by | `notify.expiry_warning`, 3 days |

**Events** (`notify.events`):

| Event | When |
|---|---|
| Node switch | a switch with a reason selected below |
| Fallback group | a group went to `failsafe-out`, `block-out` or `direct-out` |
| sing-box config rollback | a new configuration gave no connectivity and the previous one came back |
| Pipeline error | a pipeline run ended with an error or a timeout; also a rebuild on degradation |
| Subscription failure | a subscription failed to download (with the time left before its previous nodes are dropped); Amnezia country changes |
| Subscription and traffic end | the subscription ends within the warning period or 90 % of its traffic is used (once a day) |
| sing-box stop and failure | sing-box stopped or started from the admin panel, the killswitch toggled, sing-box down longer than `singbox.down_alert` |

**Node switches: reasons** — which [switch reasons](switching.md#reasons) are reported. Rotations are frequent; by default only
emergencies are sent.

The router itself often cannot reach Telegram directly (providers block it). If sending directly fails, the message goes through the
tester SOCKS on the best healthy node (`notify.via_tester`). Repeats of the same message are suppressed for an hour, and at most 20
messages are sent per hour.

**Send a test message** uses the settings of the running tester: save and wait for the restart first.

<a id="ip-type"></a>
## Exit IP type

The [IP type](checks.md#ip-type) is requested from ip-api.com for the exit IPs of nodes.

| Field | Meaning |
|---|---|
| Refresh the data every | `checks.ip_type.ttl`, 24 h |
| Request through the tester | `checks.ip_type.via_tester`: through the best healthy node (DNS filters on the router often block ip-api.com); on failure directly |

<a id="vacuum"></a>
## Database maintenance

Deleted history leaves free pages inside the SQLite file, and the file does not shrink by itself. `VACUUM` repacks the database and
returns the space to the disk. The tester runs it by the schedule `storage.vacuum` (every 30 days at 04:00, counted from the first
start) in a background thread, then truncates the write-ahead log.

The block shows the current database size, the last `VACUUM` (when, by schedule or by hand, size before → after, duration, or the
error) and the next scheduled run. **Run now** starts it at once. While `VACUUM` runs (seconds to a few minutes), writing to the
database waits: measurements are delayed, not lost. After an error the next attempt comes in a day.
