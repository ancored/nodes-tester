**English** | [Русский](README.ru.md)

# nodes_admin

The orchestrator runs inside the `nodes_tester` process.
It does not import the Runner: on creation it gets only the hooks of the apply window.

`orchestrator.py` runs the allowlisted command `nodes-tester pipeline router|clients`, keeps
the history in `pipeline.db` and the logs in `pipeline/runs/` of the data directory. By default the jobs
in `config.json` (`pipeline.router`, `pipeline.clients`) are off. After a restart a missed run executes once;
a new schedule enabled in the admin panel takes effect from a future slot.

`rules.py` takes the list of URLs and local sources from `singbox/rules.json` of the configuration
directory. Files are replaced atomically only after a successful non-empty download and when
the bytes changed. An error of one rule keeps its previous version and does not stop
the others. `base.json`, `rules.json`, local `rules/*.json` and client bases are created
or uploaded in the “sing-box files” section. Binary `.srs` are set by URL.
A change of `base.json` sets a restart marker for the next apply; sing-box 1.10 and newer rereads changed `.srs`
and source sets itself.

`merge.py` merges sing-box configs on plain JSON instead of `sing-box merge`: the base is above all,
presets go by priority, identical elements collapse, a tag conflict is an error. `presets.py`
checks the presets and builds the resulting config from them, the base and `nodes.json`; for the client branch
(`assemble --client-settings`) it merges the client base, `whnodes.json` and client presets,
replacing local router rule sets with `remote` with the address from `clients/settings.json`. `clients.py` gives
`build-clients.sh` the enabled clients from `config.json` (`list`: name and token) and the publishing directory (`output`), and
the admin panel the client state.

The HTTP routes are in `dashboard/api_pipeline.py` and `dashboard/api_singbox.py`.
Starting and editing require the token; the history and logs are also behind the token, since
they may contain subscription addresses. File writes use a revision and version history.
