**English** | [Русский](README.ru.md)

# scripts/router

The scripts connect the Python stages with the working sing-box configuration on the router. They are responsible
for the schedule, rules, merging, the candidate check, the restart and the rollback.

The OpenWrt package provides the `nodes-tester` wrapper, so calling the files manually is usually
not needed.

## Main pipeline

```sh
nodes-tester pipeline router --dry-run
nodes-tester pipeline router
```

The `router` mode runs:

```text
nodes_fetch                       (all subscriptions of both branches)
    -> raw/router.json            (pipeline.sh fetch: only this step)
    -> nodes_config
    -> nodes.json
    -> update-rules.sh
    -> apply-nodes.sh
```

If the fetch guard returned code `2`, the pipeline keeps the previous raw and continues the build with
it. Other fetch or config errors stop the process before the working sing-box is changed.

`--dry-run` still fetches the sources and writes the intermediate raw, but the resulting fragment
goes to a separate file. Rules are not updated in this mode. `apply-nodes.sh` merges the
fragment with the base and runs `sing-box check` without replacing the working config.

A dry run is exactly what to use to check XHTTP and AWG. The regular sing-box will not accept
`sing-box-lx` extensions unless a filter removes them. Details:
[`docs/SING_BOX_COMPATIBILITY.md`](../../docs/SING_BOX_COMPATIBILITY.md).

## Rule presets and the `apply` mode

The scheme of the whole assembly: [`docs/CONFIG_ASSEMBLY.md`](../../docs/CONFIG_ASSEMBLY.md).

Routing and DNS rules can be moved out of `base.json` into presets: separate JSON files
`singbox/presets/*.json`. A preset is a fragment of a sing-box config (any sections) with metadata
in the `_preset` key: `title`, `description`, `enabled`, `priority`, `priorities` and
`requires.groups`.

`apply-nodes.sh` merges the base, `nodes.json` and the enabled presets (`PRESETS_DIR`) with its own
merger (`python3 -m nodes_admin.presets assemble`): the base is above all, presets go by
`priority`, and for the paths from `priorities` by that; inside a preset the order does not change.
Identical elements collapse, the same tag with different content is an error. Details:
[`docs/CONFIG_ASSEMBLY.md`](../../docs/CONFIG_ASSEMBLY.md#merging).

```sh
nodes-tester pipeline apply --dry-run
nodes-tester pipeline apply
```

The `apply` mode does not fetch subscriptions: it copies `singbox/base.json` and applies the base and
presets to the current `nodes.json`. `--dry-run` builds the candidate from the editable base and
presets and runs `sing-box check` without changing anything. The “Rules” page of the admin panel
runs the same mode. Preset examples: `/usr/share/nodes-tester/examples/singbox/presets/`.

## Apply and rollback

`apply-nodes.sh` runs the following sequence:

1. Merging the base, `nodes.json` and the enabled presets into a temporary file (`nodes_admin.merge`).
2. `sing-box check` with the binary installed on the router.
3. Comparing the list of tags and the resulting file.
4. Saving the previous `config.json`.
5. Replacing the config and restarting only when the resulting config changed. sing-box 1.10 and newer
   rereads updated `.srs` and source sets in `/etc/sing-box/rules/` without a restart.
6. A connectivity check: a URL test of `failsafe-out` (all nodes, `HEALTH_GROUP`) through the sing-box API service
   (`services[type=api]`); it does not depend on which nodes the groups have chosen.
7. An automatic return of the previous config when connectivity is lost.

If sing-box was stopped from the admin panel (the `STOP_FLAG` flag exists, by default
`/var/run/nodes-tester/singbox-stopped`), the apply ends with an error without replacing the config and
without starting sing-box.

An invalid candidate does not replace the working file. A restart may briefly drop the network and SSH
if access goes through the same proxy.

From the moment `config.json.prev` is saved the script ignores TERM, INT and HUP until the
connectivity check or the rollback is over. On a timeout the orchestrator waits for the whole process group for up to
300 seconds after TERM and only then sends SIGKILL to the remaining processes. The apply
window closes after the whole group has finished. Before installing on a router check
this on a test configuration: start an apply with a deliberately broken candidate,
send TERM to the pipeline group during the connectivity check and make sure that the script
returned the previous `config.json`, restarted sing-box and kept connectivity. There is no automatic
local test of this section: the script has the absolute path
`/etc/init.d/sing-box`, and `pidof sing-box` and the API service check the real service.

## Backup and rollback

```sh
nodes-tester backup [--stable] [--pkg FILE]
nodes-tester rollback [-y] [--keep-db] [--check] [SNAPSHOT_DIR]
```

`backup-nodes-tester.sh` reads `config_dir` and `data_dir` from `/etc/config/nodes-tester` and
creates `/root/backups/nodes-tester-<date>/`. The service is not stopped.

| Snapshot file | Contents |
|---|---|
| `files.tar.gz` | `config_dir`, `/etc/config/nodes-tester`, `data_dir` without the database, `/etc/sing-box` |
| `code.tar.gz` | files of the installed package without configs |
| `stats.db.gz` | a copy through the SQLite backup API with `integrity_check` |
| `nodes-tester-<version>.apk` | the package from `--pkg` or `/root/packages/` |
| `crontab.txt`, `enabled.txt` | nodes-tester jobs and autostart |
| `MANIFEST`, `SHA256SUMS`, `rollback.sh` | description, checksums and the rollback script |

`--stable` moves the `/root/backups/nodes-tester-stable` link to the new snapshot. The rollback
uses it by default.

`rollback-nodes-tester.sh` checks the checksums and archives. `--check` stops
there. A full rollback:

1. Stops the service and saves the current files, crontab and version to
   `/root/backups/failed-<date>/`.
2. Reinstalls the package from the snapshot. Without a package file it restores the code from `code.tar.gz`,
   but the package manager keeps showing the new version.
3. Restores the configs, data and, without `--keep-db`, the snapshot database.
4. Replaces only the nodes-tester lines in crontab and restores autostart.
5. Restores the sing-box config if it changed and passes `sing-box check`; otherwise it leaves sing-box
   alone.
6. Starts the service.

Old snapshots are not deleted automatically.

<a id="clients"></a>
## Client branch

```sh
nodes-tester pipeline clients --dry-run
nodes-tester pipeline clients
```

The client branch fetches no subscriptions: the router pipeline fetches all of them, and the tester checks the nodes of client
subscriptions too (in the router sing-box they are only in the `nodes-tester` selector). The `clients` mode runs:

```text
nodes.json + client-candidates.json     (the tester writes the nodes of each client group every 5 minutes)
    -> nodes_config --branch clients    (client filters and domain_resolver; a group without nodes or
                                         data older than 30 minutes stop the run)
    -> whnodes.json                     (urltest {group}-auto-out, selector global-auto-out)
    -> build-clients.sh
```

`build-clients.sh` takes the clients from `clients` in `config.json` (only enabled ones). For each it merges the base
`singbox/clients/base_<name>.json` with `whnodes.json` and the enabled presets `singbox/clients/presets/*.json` (local rule sets in
them are replaced with `remote` with the address from `singbox/clients/settings.json`) or takes the ready file
`singbox/clients/publish/<name>.json` as is. Each config passes `sing-box check`; one that fails stays as before. A ready config
goes to `<output>/<name>.json` and `<output>/by-token/<token>.json` (`pipeline.clients.output`, `/etc/sing-box-clients` by
default). Files in `by-token/` of deleted and disabled clients and of old tokens are deleted: their links stop working. A dry run
builds into `$DATA/clients-dry` and compares with the published configs.

The web server serves only `by-token/`, with the token as the file name. Example for Caddy:

```caddyfile
router.example:8443 {
    @client path_regexp client ^/([A-Za-z0-9_-]{16,})$
    handle @client {
        root * /etc/sing-box-clients/by-token
        rewrite * /{re.client.1}.json
        file_server
    }
    handle {
        respond 404
    }
}
```

Adding a client or changing a token needs no web server reload. Do not turn on the access log: the tokens would get into it.

The `apply-clients` mode rebuilds the clients from the current `whnodes.json`:

```sh
nodes-tester pipeline apply-clients --dry-run
nodes-tester pipeline apply-clients
```

## Files

| Script | Responsibility |
|---|---|
| `pipeline.sh` | the fetch, config and apply sequence of the router; the client build from the checked nodes |
| `apply-nodes.sh` | merge, check, diff, restart, health check and rollback |
| `update-rules.sh` | updating rule sets before building the working config |
| `build-clients.sh` | building client configurations |
| `backup-nodes-tester.sh` | a snapshot of the package, configs, data and database before an update |
| `rollback-nodes-tester.sh` | rolling the package and data back to a snapshot |

## Environment variables

`pipeline.sh` uses:

- `PROJECT_DIR`: the root of the installed code;
- `DATA`: the directory of raw files and candidates;
- `CFG_ROOT`: the directory of `config.json` and `singbox/`;
- `CONFIG_DIR`: the sing-box base, rules and client sources in `singbox/`;
- `SINGBOX`: the directory of the sing-box sources (by default `$CFG_ROOT/singbox`), presets are in `presets/`.

The package command fills the main paths from UCI. Without it the package paths apply:
`DATA=/opt/nodes-tester`, `CFG_ROOT=/etc/nodes-tester`.
