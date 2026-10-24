**English** | [Русский](STORAGE.ru.md)

# SQLite storage

[`nodes_tester`](README.md) links the data by the access key from the node tag
`[node_crc.cred_crc]` (the `crc` column): the server settings CRC and the credentials CRC (see
[`naming`](../naming/README.md#node-key)). The history is kept when the displayed
name changes; a new access of the same server has the same `node_crc` (the `nodes.node_crc` column).

## Operating mode

Storage opens one SQLite connection with `check_same_thread=False`, enables WAL and
protects compound operations with a lock. The tester, the reserve and the traffic collector write; the dashboard
reads the database in parallel.

The schema version is stored in `meta.schema`. When opening a database of another version, Storage
renames it to `<file>.v<version>-<time>` and starts the history anew: there are no migrations.

## Tables

| Table | Contents |
|---|---|
| `nodes` | key and `node_crc`, provider and protocol, measured country, payload, address, presence and manual ban |
| `endpoints` | destinations of real connections and traffic volume |
| `results` | raw test results |
| `traffic` | time series of incoming and outgoing traffic |
| `scores` | current rating, components, EWMA, heavy veto, results of the required group tests and the exit IP |
| `score_history` | rating dynamics by measurement |
| `garbage` | pause, quarantine, streak and the time of the next attempt |
| `activations` | history of applied switches |
| `switch_state` | active node, deadlines and counters of the region |
| `switch_recent` | recently used candidates |
| `switch_activations` | cumulative number of times a node was chosen |
| `node_events` | addition, removal, pause, quarantine and recovery |
| `ip_info` | country, network (ASN) and type of the entry and exit IPs by ip-api.com |
| `entry_dns` | IPv4 addresses of node server names (the entry) and the time of the answer |
| `meta` | service values, for example the schema version and the result of the last `VACUUM` |

## `nodes` and presence

`nodes.json` from the data directory is reread when its mtime changes. Storage reads
`outbounds` and `endpoints`, excludes service groups and compares the key from the tag with the recomputed one
(`crc_ok`). The node country is written not by the build but by the conn test: a successful measurement updates `country` for all
accesses of the same `node_crc`.

`title` is the node name in the subscription (`title` from `raw/router.json`: by the access key, and if the credentials
changed since the last build, by `node_crc`; for duplicates, the first one). It is updated on each raw change, including after a subscription poll without a build; a node
gone from the subscription keeps its last name. A node with `detour` has a different key in the build and has no
name.

`present = 1` means that the node is in the current file. Disappearing from the subscription changes
presence but does not delete the history right away. `banned = 1` means a manual exclusion from the
next checks; a ban does not change `present`.

## Results and rating

One test creates a `results` row with the time, key, test name, `ok`, URL, JSON metrics and
error. Tests have their own lifetimes, so the latest results of one node may be from different
times; the dashboard shows the time for each.

`scores` holds the current row of a node. `score_history` is appended after each measurement and is used
for charts and the analysis of a node leaving.

The formula is described in [`SCORING.md`](SCORING.md).

## Traffic

The TrafficCollector holds a `SubscribeConnections` subscription of the sing-box API service. Events
come for each connection: opening, byte increments every `storage.traffic.poll`
and closing with the total. So short connections that closed
between updates are counted too. Tester traffic (a chain with `nodes-tester`) is written separately
(`is_tester=1`). Every `storage.traffic.flush` the deltas go to `traffic`, and destination
aggregates to `endpoints`. After reconnecting to the API the totals of already open connections
serve as the starting point and are not counted again.

The counters show the available retention period, not automatically “today” or the last
24 hours. On a write error the accumulated deltas go back to the buffer.

## Pause and quarantine

`garbage` holds the current restriction:

- `reason = backoff`: a pause after a GATE failure until `until`;
- `reason = garbage`: a quarantine until `until`;
- `streak`: the number of consecutive failures.

The state survives a restart. A successful GATE clears the restriction.

## Cleanup

`cleanup()` deletes nodes missing for longer than `storage.retention` and their rows
in all tables. An actively tested or present node must not disappear because of an
old file mtime.

WAL and freeing logical rows do not shrink the file right away. Physical compaction
uses `VACUUM`. The tester runs it itself by the `storage.vacuum` schedule (by default every
30 days at 04:00, counted from the first start), in a separate thread on its own connection: writes to the
database wait meanwhile. After `VACUUM` the WAL journal is flushed into the file and truncated. The result goes to
`meta['vacuum']` (time, reason, size before and after, duration or error; after an error the
retry comes in a day); in the admin panel (“Settings”) it is shown together with the “Run now” button.

Manually, with the tester stopped:

```sh
python3 -m nodes_tester -c config/config.json --vacuum
```

On the package installation: `nodes-tester vacuum`.

## Backup

Do not copy just `stats.db` of a running process: part of the data may be in
`stats.db-wal`. Safe options:

1. stop the tester and copy the database files;
2. use the SQLite backup API;
3. restore the copy separately and run an integrity check.

The database may contain node addresses, connection history and operational data. Do not
publish it with a bug report.
