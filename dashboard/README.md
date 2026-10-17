**English** | [Русский](README.ru.md)

# dashboard

`dashboard` is the HTTP backend of the web admin panel. It serves the built Vue SPA, reads SQLite,
checks the administrator token and connects the viewing, configuration and control APIs.

Everyday work with the interface is described in [`docs/USAGE.md`](../docs/USAGE.md).
This file describes the boundaries of the Python module.

Screenshots of all sections in the light and dark themes are in
[`docs/screenshots`](../docs/screenshots/README.md).

## Two modes

### Embedded

With `admin.enabled: true` the server is created inside `nodes_tester.Runner`. It has access to the
Runner, Switcher, Scoreboard and Storage. In this mode the following work:

- the tester status;
- on-demand testing of all nodes without waiting for the result lifetimes;
- the live log;
- ban, quarantine and lifting restrictions;
- manual choice of a node in a group;
- `VACUUM` of the database on demand;
- reading and writing `config.json`.

### Separate process

```sh
python3 -m dashboard -c config/config.json --host 127.0.0.1 --port 8088
```

A separate process opens the database for reading and can edit `config.json` with the token, but has no live Runner. Control of testing, the log and selectors is
unavailable in this mode.

## Access

Most reading APIs are available without the token only with `admin.public_read: true`. The pipeline
history, its logs and the sing-box source files always require the token: they may contain
subscription addresses and other working data. Writes and actions
always require the `X-Admin-Token` header equal to `admin.token`. An empty token turns off
modifying operations.

Publish the admin panel only in a trusted network or over HTTPS with additional
protection. It shows operational data, and the subscription editor works with their URLs.

## API

| Group | Routes | Purpose |
|---|---|---|
| Session | `/api/capabilities`, `/api/session` | server mode and access check |
| Data | `/api/data`, `/api/rating`, `/api/results` and other slices | reading SQLite and state |
| Config | `/api/config`, `/api/config/sections`, `/api/config/schema` | reading and atomic writing of `config.json` (subscriptions, groups, tester, pipeline in one file): the whole file by revision or only changed sections; the schema gives the default values for form hints |
| Form options | `/api/config/subscription-options` | User-Agent options, Happ headers, the installation HWID, names of checks and IP types, built-in regions, subscription state from raw (`sources`) |
| Groups | `/api/groups`, `/api/nodes/{crc}/groups` | group state (active node or fallback outbound, candidates, reserve); group checks for a node with result times |
| Card | `/api/nodes/{crc}/score-history`, `/api/nodes/{crc}/details` | rating history; node JSON, the strings for `node_crc` and `cred_crc`, credential paths (details requires the token) |
| Nodes | `/api/nodes/{crc}/...` | ban, quarantine and lifting restrictions |
| Switching | `/api/groups/{group}/switch` | manual choice of a candidate; 409 contains `code` and `allowed_groups` |
| Runner | `/api/status`, `/api/run/tests`, `/api/logs` | state, on-demand testing and the log |
| Database | `GET /api/storage/vacuum`, `POST /api/storage/vacuum` | the result of the last `VACUUM`, the next run by schedule, the database size; a manual start (token) |
| sing-box | `GET /api/singbox/control`, `POST /api/singbox/stop`, `POST /api/singbox/start`, `PUT /api/singbox/killswitch` | state, emergency stop and start, turning the router killswitch service on and off (OpenWrt only) |
| Notifications | `POST /api/notify/test` | a test message with the settings of the running process |
| Pipeline | `/api/pipeline`, `/api/pipeline/run`, `/api/pipeline/runs`, `/api/pipeline/schedule` | schedule, start and history; all routes require the token |
| sing-box files and presets | `/api/singbox/files/...`, `/api/singbox/presets` | a limited list of JSON files, a check of the resulting build when the base and presets are written, revision and 20 previous versions, deleting presets |

`/api/data` is kept as a shared aggregate for compatibility. Separate read routes are more convenient
for individual screens and diagnostics.

The `traffic` slice gives `traffic_test_download_24h`: the sum of `downloaded` from the results of
`download` and `heavy_download` over the last 24 hours. These are the measured bytes of response bodies,
without service traffic and other tests. `traffic_tester_totals` is all tester traffic over the
stored period from the exact connection accounting (`storage.traffic`).

API messages and server errors are in Russian for now; the interface itself is translated on the client.

## File handling

The editor checks `config.json` with the same loader as the tester and the pipeline
(`nodes_tester.config.parse_config`): an unknown field or a wrong value is rejected with the field
path. The write goes to a temporary file with `fsync` and an atomic replacement, permissions `0600`
are kept.

The form saves only the changed top-level sections (`POST /api/config/sections`): the server
compares them with what was opened and rejects the write only if the same sections were changed over SSH or in another
window. The advanced JSON is saved as a whole by the file version marker.

The “Subscriptions and build” and “Settings” pages edit one document. The first has subscriptions by
branch (`router`, `clients`), the `pipeline` section and groups with checks, the second the tester,
node choice, the admin panel, storage and notifications. Unknown fields and overrides are kept. An empty
field is not written: the default value from `config.schema.json` applies and is shown in grey.

For regular URLs you can choose the standard browser User-Agent, `curl`, `clashmeta`
or your own string. It is used only for HTTP downloads. For Happ crypt-crypt5
the `happ_headers` set is edited, including its own User-Agent; the default
values come directly from the loader, a separate `user_agent` is ignored.

Saving does not start fetching subscriptions, building and applying sing-box. The embedded tester
restarts itself after saving (`/etc/init.d/nodes-tester restart` as a detached process), except while
the pipeline runs; the PUT answer contains `restarting` and, if there is no restart, `restart_note`. In the OpenWrt package the raw is stored in `/opt/nodes-tester/raw/router.json` and
`clients.json`, the build result is `nodes.json` and `whnodes.json` in the same data directory.

The JSON in the card is taken from `nodes.json`; if the fragment is not there, the available
part is restored from the tag and the SQLite payload with a note about the missing fields.
The node key and JSON are shown in two colours: server settings (`node_crc`) and credentials
(`cred_crc`); fields outside the key (`tag`, `domain_resolver`) have no colour. Below are both strings that
are hashed and the key check. The rating history shows all saved measurements of the node with the date and time. The chart shows the last 24 values.

## Static files and routing

`webapp.py` implements a small HTTP framework on the Python standard library:

- routes by method and path template;
- JSON answers and a body size check;
- API authorisation;
- safe serving of files from `dashboard/static`;
- SPA fallback only for URLs without an extension.

The interface sources are in [`web`](../web/README.md). The repository and the package get
the build result `dashboard/static`.

## Code map

| File | Responsibility |
|---|---|
| `server.py` | building the application, connecting the APIs and starting the server |
| `webapp.py` | HTTP framework, routing, token and static files |
| `data.py` | SQLite queries and aggregates |
| `api_read.py` | reading routes |
| `api_control.py` | actions on nodes, groups, the Runner and `VACUUM` |
| `api_config.py` | reading, checking and writing `config.json`, form options |
| `api_session.py` | mode capabilities and session check |
| `api_pipeline.py` | pipeline start, schedule and history |
| `api_singbox.py` | a limited editor of the sing-box source files |
| `static/` | the built Vue SPA |
