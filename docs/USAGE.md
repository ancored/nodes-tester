**English** | [Русский](USAGE.ru.md)

# Admin panel and everyday work

[About the project](../README.md) · [First run on OpenWrt](../openwrt/README.md) · [Help](help/README.md)

This guide is an overview. Every page and block of the admin panel, with flowcharts of the algorithms, is explained in the
[help](help/README.md); in the admin panel it opens by the **?** icon next to a page title or in the corner of a block.

The guide applies to package [`v0.11.0`](https://github.com/ancored/nodes-tester/releases/tag/v0.11.0). Updating the sources on a computer does not update the installed package.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="screenshots/nodes.png">
  <source media="(prefers-color-scheme: light)" srcset="screenshots/nodes-light.png">
  <img alt="Node list with state, rating and filters" src="screenshots/nodes-light.png">
</picture>

The screenshots were updated for version 0.3.4, including [groups, preview, presets and node actions](screenshots/README.md#groups-rules-and-actions). The full gallery is in [`docs/screenshots`](screenshots/README.md).

## Where to find what

| Task | Section |
|---|---|
| Check the state of the database, the tester and the group choice | Overview |
| Find a node, understand its restrictions, see measurements or run an action | Nodes, then the card |
| Compare the rating, results and provider quality | Subsections of “Nodes” |
| Configure sources and find out how to apply them | Subscriptions and build |
| Start an update, set the schedule, open the log | Pipeline |
| Enable a preset, check the build and apply the base with rules | Rules |
| Change the sing-box base, rules or client files | sing-box files |
| Start testing, see the tester stage and log | Checks |
| Go through the group choice history | Switches |
| See user traffic over the stored period | Traffic |
| Configure the sing-box API, SOCKS, tests, switching, storage and database maintenance | Settings |
| Open the official sing-box API service panel | sing-box dashboard (last menu item) |

Search, sorting, filters and table pages are kept in the address. Auto-refresh does not reset them. The node card is tied to results and events by the `node_crc.cred_crc` key, so renaming a tag does not break the history; the key parts are shown in different colours: server settings and credentials.

The interface is in English and Russian. By default it follows the browser language; the choice in the “Language” list at the bottom of the menu is remembered on the device.

## Access

Press “Log in to manage” and enter the token from `admin.token` of the working `config.json`. The package installation creates it automatically. It is not the sing-box API secret. The admin panel checks the token on the server; typing a string alone does not give access to actions.

The token is kept only until the page is closed unless “Remember on this device” is on. This option stores it in localStorage. “Log out” deletes the stored token and closes the editor. Do not enable remembering on someone else's computer; use a trusted network or HTTPS, do not expose the admin panel directly to the internet.

The “sing-box dashboard” menu item opens the official panel of the sing-box API service in a new tab. The admin panel serves its static files from `{api.url}/dashboard/` at `/sing-box-dashboard/`, so the API service must have `dashboard` set. After the administrator logs in, the admin panel writes the `api` address and secret into the panel settings in the browser itself, and the panel opens without entering the secret. Without logging in the panel asks for the address and secret itself. The secret ends up in the browser localStorage; on someone else's computer do not log in, or delete the server in the panel settings.

An MCP client connects to `http://<router>:8088/mcp` with the header `Authorization: Bearer <admin.token>` and gets
the state, nodes, logs, the database and the same actions as the admin panel; the tools and the client configuration
are in [`dashboard`](../dashboard/README.md#mcp).

A separate `python3 -m dashboard` reads the database and lets you edit files with the token, but does not control the Runner, does not start testing and does not show the live log. The admin panel reports this mode separately.

## Settings and subscriptions

All settings are stored in one file, `config.json` (field reference: [CONFIG.md](CONFIG.md)). It is
edited on two pages: “Subscriptions and build” and “Settings”. Durations are written as strings, as in
sing-box: `30s`, `5m`, `3h`, `2d`. An empty field means the default value: it is shown in grey
and is not written to the file.

“Settings” has forms for the connection to sing-box, the tester, rotation and the monitor, the admin panel and
storage, notifications and the exit IP type. The “Database maintenance” block below them shows the
database size, the last `VACUUM` (when, size before and after) and the next run by the `storage.vacuum`
schedule, and has a “Run now” button. Other fields are available in the advanced JSON.

“Subscriptions and build” is split into the “Router” and “Clients” branches. The “Subscription sources” tab has
the subscriptions of the branch: URL/happ, file, AWG folder, Amnezia Premium (the tester issues and replaces the
country files, see [CONFIG.md](CONFIG.md#amnezia-premium)) or own nodes (sing-box outbounds), the “Branch” field
(router, clients or both) and excluding nodes by a name substring. URLs and secrets are hidden by default.
The “Build and groups” tab has the schedule and filters of the branch, sing-box group parameters and, for the router,
groups with checks and regions.

A group is a selector `{tag}-auto-out` with all nodes; the tester picks the active node. Below the group settings its current state is shown: the active node or the fallback outbound, the number of candidates, the reserve. The same is in the “Groups” block of the “Overview”, and the “Groups” tab of the node card shows for each group whether the node is a candidate, and the check verdicts with the age of the result. For each
check (exit country, IP type, country match by databases, Google AI, OpenAI, Claude, heavy
download) the group sets “required”, “preferred” or “ignored”. A node without a fresh success of a
required check cannot become active; preferred ones move the node up the queue. A strict group
without a suitable node blocks its traffic (`block-out`), a non-strict one goes to `failsafe-out`.

XHTTP and AWG are meant for `sing-box-lx`. The regular core will not accept the config created for them. For
the regular sing-box exclude the `xhttp` and `wg` protocols in the branch filters. Details are in the
[compatibility guide](SING_BOX_COMPATIBILITY.md).

Before saving press “Review changes”, check the list of fields and confirm the write. The button shows the list of fields; the server checks the content when saving. If the file was changed in another window or over SSH, saving is rejected until you reload. Navigation, reloading and closing the page warn about an unsaved draft; the draft is not a backup.

After saving, the admin panel on OpenWrt restarts the tester itself and reports when it responds again: monitoring and the checks stop for a few seconds, user connections are not dropped. While the pipeline runs there is no restart; the admin panel asks to do it later. Manually:

```sh
/etc/init.d/nodes-tester restart
```

Each subscription shows the state of the last fetch: the number of nodes, the time of the last successful fetch, the error, and the difference with the active config: how many subscription nodes are in sing-box now and how many the next build gives (new and gone accesses). If the panel sends metadata, the title, used traffic, expiry and the provider message are shown. The router HWID for panels with a device limit is shown below the list of subscriptions; happ subscriptions always send it, URL subscriptions only with the “Send the HWID” checkbox. `happ://crypt…` links need the keys of the Happ app: they are not part of nodes-tester and are downloaded from the source in the “Happ keys” panel (by default `src/keys.rs` of the hpwnr project on GitHub). The source can be replaced with your own URL or a path to a file on the router.

Saving does not fetch subscriptions and does not apply the configuration to sing-box. After changing subscriptions, filters or the list of groups open “Pipeline”, run a check of the branch, read the log and apply the result. If the embedded pipeline is unavailable, use the commands on the router from the section below. Changes of group checks, the rating and switching do not need a rebuild: a tester restart applies them. Changes of the client branch are applied by running the “Clients” mode and do not touch the router sing-box.

<table>
  <tr>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="screenshots/subscriptions.png">
        <source media="(prefers-color-scheme: light)" srcset="screenshots/subscriptions-light.png">
        <img alt="Subscription source editor" src="screenshots/subscriptions-light.png">
      </picture>
    </td>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="screenshots/config.png">
        <source media="(prefers-color-scheme: light)" srcset="screenshots/config-light.png">
        <img alt="Tester and connection settings" src="screenshots/config-light.png">
      </picture>
    </td>
  </tr>
  <tr>
    <td><strong>Subscriptions and build</strong>: sources and reviewing changes.</td>
    <td><strong>Settings</strong>: connection, checks, automation and storage.</td>
  </tr>
</table>

## How subscriptions and configs are updated

The router branch runs the steps in order:

1. Fetches the subscriptions of the `router` branch from `config.json` into `<data_dir>/raw/router.json`. If a source is temporarily unavailable, the loader can keep its last working nodes (last-good). The `min_ratio` guard does not let a sharply shrunk set replace the raw file: the pipeline continues with the previous raw and notes it in the log.
2. Builds `<data_dir>/nodes.json` by the `pipeline.router` and `groups` sections. Then updates the rules from `singbox/rules.json`: binary `.srs` are downloaded by URL, local JSON sources are taken from `singbox/rules/`.
3. Merges the fragment with `singbox/base.json` and checks the candidate with `sing-box check`. An error leaves the working config in place.
4. Replaces the resulting config and restarts sing-box only if the resulting file changed. sing-box 1.10 and newer rereads changed `.srs` and local source sets without a restart. The connectivity check after applying brings back the previous config if the working group does not respond.

While the router branch is being applied, the tester pauses checks and switching. After the pipeline finishes, the pause lasts another 120 seconds. When you work through the same proxy, a sing-box restart may briefly interrupt access to the admin panel and SSH.

The client branch fetches no subscriptions: the router branch fetches all of them, and the tester checks the nodes of client subscriptions too (in the router sing-box they are only in the tester selector). The client branch takes from `nodes.json` the nodes that passed the checks of the client groups on the router, builds `whnodes.json` and merges it with the `singbox/clients/base_<name>.json` bases of the [clients](help/subscriptions.md#clients) and the enabled client presets `singbox/clients/presets/` (a client without a base gets its ready file from `singbox/clients/publish/` as is). Each config passes `sing-box check` before publishing. The ready files go to `/etc/sing-box-clients/<name>.json` and `by-token/<token>.json`; a separately configured web server serves `by-token/` to clients by links (an example is in the [router scripts guide](../scripts/router/README.md#clients)). The client list and the tokens are edited in “Subscriptions and build” → “Clients”. The client branch does not restart the router sing-box.

## Pipeline

After logging in with the token open “Pipeline”, choose the `router` or `clients` branch and press “Check” (`--dry-run`). The check downloads the sources and writes intermediate files to the data directory. For the router it builds the candidate and runs `sing-box check`, but does not update the rules, does not replace the working config and does not restart sing-box. For the clients the result is written separately, without publishing; the client check also takes no subscriptions: it builds from the current `nodes.json` and the tester data. Study the log, then press “Apply” or run on the router:

```sh
nodes-tester pipeline router --dry-run
nodes-tester pipeline router
nodes-tester pipeline clients --dry-run
nodes-tester pipeline clients
```

The same section sets the time of day for each branch by the router clock (`pipeline.router.at`, `pipeline.clients.at`). Both jobs are off initially. To run every three hours set `00:00, 03:00, 06:00, 09:00, 12:00, 15:00, 18:00, 21:00`. After enabling, the new schedule takes effect from the next start time. When the tester restarts, a missed job runs once. Only one pipeline can run at a time, including runs from the terminal. Without the built-in schedule the pipeline can be run from cron (`nodes-tester pipeline router` in `/etc/crontabs/root`), but not together with it.

The history shows the branch, manual or scheduled start, start, end and result; open a record for the full log. `ok` means success, `error` reports an error, `busy` shows that the pipeline was busy (code 75), `timeout` means the time limit was exceeded. The `interrupted` status appears if there is no confirmed exit code after a process failure. The time limit is `pipeline.timeout`, `30m` by default. Router subscriptions are polled more often than the build: `pipeline.fetch.every` (`3h` by default, `0s` turns it off; the “Poll router subscriptions every” field in “Subscriptions and build”) updates only raw nodes and the subscription state, sing-box is not touched; in the history such runs are marked as “subscription fetch”. A scheduled build that comes during a fetch waits for it to finish. Logs are in `<data_dir>/pipeline/runs/`, the history is stored in `<data_dir>/pipeline.db`. Keep `data_dir` on persistent storage.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="screenshots/pipeline.png">
  <source media="(prefers-color-scheme: light)" srcset="screenshots/pipeline-light.png">
  <img alt="Pipeline: manual run, schedule and history" src="screenshots/pipeline-light.png">
</picture>

The tester checks nodes whose test results have expired. “Run testing now” in the “Checks” section checks all nodes available to the tester without waiting for the deadlines; this button does not update subscriptions. While on-demand testing runs, another request is unavailable.

## sing-box files

In the “sing-box files” section upload or edit `base.json`, `rules.json`, local `rules/*.json` and the client bases `clients/base_alice.json`, `clients/base_bob.json`. Before writing `base.json` the server merges it with the existing `nodes.json` and runs `sing-box check`; build `nodes.json` first if there is none yet. `rules.json` sets which rules to update: binary `.srs` are allowed only by HTTP(S) URL, and a local source must be a JSON file in the source rule-set format from `rules/`. Ready files in `clients/publish/` are published without a build.

The editor shows previous versions of the file and lets you restore the chosen one. Before saving it checks whether the file changed after the page was opened. Saving a source does not start the pipeline: check the needed branches with a dry run and apply them separately. If only the rules change, sing-box 1.10 and newer needs no restart; an old core reads new local rule sets at the next restart.

<table>
  <tr>
    <td width="50%"><picture><source media="(prefers-color-scheme: dark)" srcset="screenshots/singbox.png"><source media="(prefers-color-scheme: light)" srcset="screenshots/singbox-light.png"><img alt="sing-box source file editor" src="screenshots/singbox-light.png"></picture></td>
    <td width="50%"><picture><source media="(prefers-color-scheme: dark)" srcset="screenshots/singbox-rules.png"><source media="(prefers-color-scheme: light)" srcset="screenshots/singbox-rules-light.png"><img alt="sing-box rule source editor" src="screenshots/singbox-rules-light.png"></picture></td>
  </tr>
  <tr><td>Base, client files and version history.</td><td>Rule sources and local JSON files.</td></tr>
</table>

## sing-box rules

How the resulting config is assembled: [CONFIG_ASSEMBLY.md](CONFIG_ASSEMBLY.md).

Routing and DNS rules are built from presets: separate `presets/*.json` files in
“sing-box files”. A preset holds everything the base lacks: its own route and DNS rules,
DNS servers and rule sets. For example, the `zone-us` preset sends the `us` user and the
`us-domains` domains to the `us-out` outbound, and their DNS to `dns-us`; the `us-out` outbound is declared by the `out-us` preset
from the `us-auto-out` group. It needs the `us` group and an inbound with the `us` user in the base.

The “Rules” section shows the presets by priority: title, description, required groups and
a switch. When switched, the server builds the resulting config from the base, `nodes.json` and
the enabled presets and runs `sing-box check`; if the check fails, the file does not
change and the reason is shown on the page. Only the “Apply” button changes the running sing-box:
it copies the base, builds the config, restarts sing-box on changes and
checks connectivity; the log is shown on the page. “Check the build” does the same without
changes. When you come back to the page it shows the status and log of the last `apply`
run; the full history is in “Pipeline”.

The “Clients” tab in the same section shows the `singbox/clients/presets/` presets, shared by
all enabled clients. The switch checks the config of each client (base,
`whnodes.json`, enabled presets) with `sing-box check`. “Build clients” publishes the configs
from the current `whnodes.json` without fetching subscriptions (`nodes-tester pipeline apply-clients`);
“Check the build” builds them into a separate directory without publishing. A client gets the local rule sets
of presets from the distribution address: set it in `clients/settings.json`
(`{"rules_url": "https://…/"}`), otherwise such a preset will not build; details are in
[config assembly](CONFIG_ASSEMBLY.md#client-presets).

The rule order is set by `priority` (lower goes first); `priorities` overrides it for
a single path, for example `{"dns.rules": 150}` puts the DNS rules of the preset elsewhere.
A preset may contain any config sections and declares the DNS servers and rule sets it needs
itself: identical objects from different presets collapse, and the same tag with different content
is an error. Metadata is kept in the `_preset` key and does not get into the resulting config. How
merging works is in [`CONFIG_ASSEMBLY.md`](CONFIG_ASSEMBLY.md#merging). Ready presets are in
`/usr/share/nodes-tester/examples/singbox/presets/`, their catalogue is in the
[preset library](CONFIG_ASSEMBLY.md#preset-library).

## How to read the state and act

The country in tables is shown as a flag, the code is in the tooltip. In “Results” the “IP” column shows with an icon who owns the exit IP of the node: a building is a data centre, a phone a mobile network, a house a residential provider; a mask means the IP is known as a VPN or proxy. A flag below the icon means the database puts the IP in a different country than the connection test showed. Red icons are worse for AI services; hover to see the IP and the network. The last columns are Google AI, OpenAI and Claude AI: a tick means the service is available, a cross that it is not, “?” a network failure, the previous result stays; below the Google AI mark is the country in which Google sees the node. The protocol is shown in parts, each on its own line.

On the “Overview” the “sing-box” block shows whether sing-box is running and lets you stop and start it. Stopping is an emergency exit if sing-box broke the network. What happens to LAN devices depends on the router killswitch service: if it is loaded, the devices protected by its rules stay without internet until sing-box is started; without it, traffic goes directly through the provider. The block shows whether the killswitch rules are loaded and how many packets were blocked, and lets you turn the service on or off. While sing-box is stopped from the admin panel, the pipeline does not apply a new config. Telegram or webhook notifications are configured in “Settings”.

Tables are filtered by drop-down lists: country, group, protocol, provider and fields of the particular table (state, status, reason). Groups are taken from the current `nodes.json`: each group has all nodes, a node becomes active by the group checks. The tester list, quality, restrictions and the switcher choice show different sides of the node state. Missing measurements do not mean a failure. The rating is on a scale from 0 to 100, its factors are normalised from 0 to 1; see the physical values in the results with the date of their own measurement. “Results” shows the latest result of each test, measurement times are in the node card.

“All nodes” are the nodes of the current router subscriptions. New nodes appear within a minute after the config is applied, without measurements, and the tester checks them right away. Nodes gone from subscriptions stay only in the “Archive”; their card opens from there. The “Name in the subscription” column and the card show the original node name at the provider; a dash means there is no name (for example, AWG from files).

The node card has actions with confirmation:

- “Exclude”: skip the next checks indefinitely until the exclusion is removed. It is not a removal from the subscription and not an immediate change of the current selector.
- “Quarantine”: postpone checks for the configured period `selection.quarantine.duration`.
- “Lift restriction”: put the node back into the check queue. It does not confirm that the node works.
- “Select in group”: make the node active in the chosen group now. The list has only groups where the node is a valid candidate (positive rating, passed required tests of the group, no restrictions). Automation keeps working and may change the choice later.

The overview reflects the switcher state, not an independent check of the sing-box selectors. The history has the last 200 records with the date, group, reason and previous node; the icon by the reason distinguishes an emergency replacement, an emergency without a replacement, a change by quality, rotation, the first and the manual choice, failsafe. The last record of a group does not necessarily show its current active node; an emergency without a replacement is not a successful switch.

User traffic is shown for the available stored period; test traffic is excluded from it. A separate block shows the tester usage for the last 24 hours: the measured bytes of download tests and a sample of live connections. These figures may overlap, so they must not be added up. Aggregates by providers, countries, protocols and nodes are built from all stored rows and are filtered by country, group, protocol and provider; the top of destinations has up to 50 rows. Destination counters are cumulative. The log can be paused and filtered; after a long pause some old lines may drop out of the server buffer.

<table>
  <tr>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="screenshots/node-card.png">
        <source media="(prefers-color-scheme: light)" srcset="screenshots/node-card-light.png">
        <img alt="Node card with check results" src="screenshots/node-card-light.png">
      </picture>
    </td>
    <td width="50%">
      <picture>
        <source media="(prefers-color-scheme: dark)" srcset="screenshots/lifecycle.png">
        <source media="(prefers-color-scheme: light)" srcset="screenshots/lifecycle-light.png">
        <img alt="Node restrictions and quality" src="screenshots/lifecycle-light.png">
      </picture>
    </td>
  </tr>
  <tr>
    <td><strong>Node card</strong>: measurement freshness, traffic, events and actions.</td>
    <td><strong>Restrictions and quality</strong>: pause, quarantine and provider state.</td>
  </tr>
</table>

## If something does not work

| Symptom | What to check |
|---|---|
| The admin panel does not open | The service, `admin.enabled`, address/port, LAN access, `logread -e nodes-tester` |
| The token is not accepted | The token of the active config; a change needs a restart |
| The database is missing or off | The `storage.path` path, the tester start |
| Read error or old data | The error in the header; a stale snapshot does not confirm the current state |
| No measurements | The test selector, the log |
| No new nodes after saving | Whether fetch, build and apply to sing-box were done |
| Pipeline busy, code 75 | Wait for the current run; check the history and the log. Simultaneous runs are blocked |
| `timeout` | Check the last stage in the log and the `pipeline.timeout` limit; the orchestrator ends the process group after a safety wait |
| `sing-box check` error | Check the base, the resulting candidate and protocol compatibility with the installed core. The working config was not replaced |
| Connectivity lost after applying | Read the log of the check and the rollback. The pipeline brings back the previous config if a request through the working group fails |
| Selecting in a group is unavailable | A verified token, the embedded Runner, switching, the node is a candidate of at least one group |

Before updating the package use `nodes-tester backup --stable`. If you need the previous version, check the snapshot with `nodes-tester rollback --check`, then run `nodes-tester rollback`. The order and contents of the snapshot are described in the [update guide](../openwrt/README.md#6-update-the-package).

In [Issues](https://github.com/ancored/nodes-tester/issues) give the OpenWrt, package and sing-box versions, the action and a short anonymised error. Remove subscription URLs, keys, tokens and private addresses.
