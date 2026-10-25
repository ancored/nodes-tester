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
always require the `X-Admin-Token` header (or `Authorization: Bearer`) equal to `admin.token`. An empty token turns off
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
| Pipeline | `/api/pipeline`, `/api/pipeline/run`, `/api/pipeline/runs`, `/api/pipeline/runs/{id}`, `/api/pipeline/schedule` | schedule, start and history; all routes require the token |
| sing-box files and presets | `/api/singbox/files/...`, `/api/singbox/presets` | a limited list of JSON files, a check of the resulting build when the base and presets are written, revision and 20 previous versions, deleting presets |

`/api/data` is kept as a shared aggregate for compatibility. Separate read routes are more convenient
for individual screens and diagnostics.

The `traffic` slice gives `traffic_test_download_24h`: the sum of `downloaded` from the results of
`download` and `heavy_download` over the last 24 hours. These are the measured bytes of response bodies,
without service traffic and other tests. `traffic_tester_totals` is all tester traffic over the
stored period from the exact connection accounting (`storage.traffic`).

API messages and server errors are in Russian for now; the interface itself is translated on the client.

## MCP

`POST /mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server (Streamable HTTP,
JSON-RPC 2.0) for MCP clients. It works in the embedded mode and needs the administrator token as
`Authorization: Bearer <admin.token>` (or `X-Admin-Token`): the client gets the same rights as the
administrator in the admin panel. The server keeps no sessions and answers every request with one JSON
response; `GET /mcp` (an SSE stream) is not offered. A request with an `Origin` that does not match
`Host` is refused.

Client configuration (the usual `mcpServers` format):

```json
{
  "mcpServers": {
    "nodes-tester": {
      "type": "http",
      "url": "http://192.168.1.1:8088/mcp",
      "headers": { "Authorization": "Bearer <admin.token>" }
    }
  }
}
```

The tools call the same API routes, so checks, revisions and errors are as in the interface. Long lists
are cut by `limit` with a `_truncated` count.

On connection the server sends the client instructions: how to work with the tools safely, the main rules of the
sing-box config assembly and of the security model, and links to the documentation of the running versions: nodes-tester by
its tag, the sing-box configuration of the running core (for `sing-box-lx` its copy of the upstream documentation and the
description of its extensions). The core version comes from `sing-box version`. The package carries the English
documentation (`docs/`, module READMEs), and the `docs` tool returns it without internet access.

| Tool | Kind | What it does |
|---|---|---|
| `overview` | read | capabilities, core version and build tags, tester status, groups with candidates and reserve, sing-box and kill switch, pipeline |
| `list_nodes` | read | nodes with filters by group, provider, country, protocol, state and text |
| `get_node` | read | one node: identity, rating components, latest result of every test, group checks, restrictions, events, activations, score history; `include_config` adds the outbound JSON |
| `get_view` | read | a ready-made view: `rating`, `history`, `results`, `traffic`, `lifecycle`, `graveyard` |
| `tester_log`, `singbox_log` | read | the last lines of the tester log and of the sing-box log file (`log.output`), with a regular expression filter |
| `pipeline` | read | schedule and runs, or one run with its log |
| `get_config`, `config_schema` | read | `config.json` with its revision; the schema of a section with descriptions and defaults |
| `singbox_files` | read | the list of sing-box source files and presets, a file with its versions |
| `docs` | read | documentation of this version: the list with sections, a document or only the sections matching a heading |
| `query_db` | read | one `SELECT` over `stats.db`: read-only connection, no `ATTACH` or `PRAGMA` except the schema ones, 5 s and up to 5000 rows |
| `node_action` | action | ban, unban, quarantine, lift quarantine |
| `switch_group` | action | activate a candidate in a group |
| `run_tests` | action | measure all nodes now |
| `run_pipeline` | action | start the pipeline (`dry_run` builds and checks only), optionally wait for the end and return the log |
| `update_config` | action | replace top-level sections of `config.json` (the tester restarts) |
| `edit_singbox_file` | action | write, restore a version of or delete a sing-box source file by revision |
| `singbox_control` | action | stop and start sing-box, turn the kill switch service on and off |
| `call_api` | action | any `/api/...` route not covered above |

Read tools are marked `readOnlyHint`, those that can break connectivity or restart a service —
`destructiveHint`, so a client can ask for confirmation before them.

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
the pipeline runs; the PUT answer contains `restarting` and, if there is no restart, `restart_note`. In the OpenWrt package the raw is stored in `/opt/nodes-tester/raw/router.json`, the build
result is `nodes.json` and `whnodes.json` in the same data directory. The subscription options also return the client
state (the config source and when it was published by its token, without the token) and the number of nodes in each client
group from `client-candidates.json`.

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
| `mcp.py` | MCP server `/mcp`: JSON-RPC and tools over the API |
| `data.py` | SQLite queries and aggregates |
| `api_read.py` | reading routes |
| `api_control.py` | actions on nodes, groups, the Runner and `VACUUM` |
| `api_config.py` | reading, checking and writing `config.json`, form options |
| `api_session.py` | mode capabilities and session check |
| `api_pipeline.py` | pipeline start, schedule and history |
| `api_singbox.py` | a limited editor of the sing-box source files |
| `static/` | the built Vue SPA |
