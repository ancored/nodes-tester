**English** | [Русский](README.ru.md)

# Installation and first run on OpenWrt

[About the project](../README.md) · [Admin panel and everyday work](../docs/USAGE.md)

A guide for a new installation next to a running sing-box. If the router already has a manual nodes-tester installation, first save the configs and the database and stop the old service: two instances must not control the same selectors. Do not install the package on top of the old installation blindly.

The guide applies to [release `v0.16.0-r2`](https://github.com/ancored/nodes-tester/releases/tag/v0.16.0-r2). sing-box 1.14 or newer is required.

## Quick install

On a clean OpenWrt 25.x (neither sing-box nor nodes-tester installed) one script installs everything:
[`install.sh`](install.sh). The subscription is optional and can be added later in the admin panel:

```sh
wget -qO- https://raw.githubusercontent.com/ancored/nodes-tester/master/openwrt/install.sh | sh -s -- 'https://provider.example/sub/YOUR-TOKEN'
```

The script:

- downloads the latest [sing-box-lx](https://github.com/Leadaxe/sing-box-lx) release for the router
  architecture, verifies `SHA256SUMS` and installs `/usr/bin/sing-box` with the `/etc/init.d/sing-box` service;
  the version can be set: `SINGBOX_LX_VERSION=1.14.3-lx.20`; the service raises the open files limit
  to 65535;
- adds the key and the package repository and installs `nodes-tester`;
- writes a minimal base `singbox/base.json` that needs no subscriptions: TUN (`auto_route`,
  `auto_redirect`) captures router and LAN traffic, `sniff` and `hijack-dns`, the local network goes
  through `direct-lan`, a SOCKS inbound for the tester, the API service with a random secret. DNS is the
  router system resolver (`type: local`); dnsmasq is excluded from TUN (`exclude_uid`), otherwise its queries
  to the provider would loop. The sing-box log goes to `/var/log/sing-box.log`: the tester monitor
  counts group connection errors from it. The only preset `presets/direct.json` sends
  all traffic directly: there are no nodes in the routes until you add your own rules;
- checks connectivity through TUN (DNS and HTTPS); without it, stops and disables sing-box,
  and the network works as before the installation;
- does not add cron jobs: the tester itself keeps the sing-box log under 5 MB (`singbox.log_max`, `/var/log`
  on OpenWrt is in RAM) and runs `VACUUM` of its database (`storage.vacuum`);
- enables the tester and the admin panel on the LAN address (port `8088`) and prints the administrator token;
- with a subscription, runs `nodes-tester pipeline router` right away and enables the router build schedule
  (`pipeline.router`, 03:00). Without one, add a subscription in “Subscriptions”, press “Apply” in
  “Pipeline” and enable the schedule there.

If sing-box or nodes-tester is already installed, the script changes nothing: use the manual
installation below. To send traffic through nodes, turn off the `direct` preset and take the zone rules and `route.final` from the
[preset library](../docs/CONFIG_ASSEMBLY.md#preset-library); the script does not install a kill switch (see
the [security model](../docs/SECURITY_MODEL.md)).

## 1. Install the package

The OpenWrt 25.x package is signed with the project key. Add the public key and the package repository
once; after that installation and updates go through regular `apk`, without `--allow-untrusted`. Run
the commands on the router (`ssh root@192.168.1.1`, use your address):

```sh
wget -O /etc/apk/keys/nodes-tester.pem https://raw.githubusercontent.com/ancored/nodes-tester/master/openwrt/nodes-tester.pem
echo "https://ancored.github.io/nodes-tester/packages.adb" >> /etc/apk/repositories.d/customfeeds.list
apk update
apk add nodes-tester
```

The key ([`nodes-tester.pem`](nodes-tester.pem), ECDSA P-256) is a decision to trust the project
packages: `apk` checks the signature of the index and of the packages with it and refuses to install a substituted or
damaged file. Check that the downloaded key matches the key in the repository.

Without the repository you can install the file from a [release](https://github.com/ancored/nodes-tester/releases/latest):
copy it to the router (`scp -O nodes-tester-<version>.apk root@192.168.1.1:/tmp/`) and
run `apk add /tmp/nodes-tester-<version>.apk`; with the key installed the signature is
checked as well. The `.apk` cannot be installed on OpenWrt 24.10, which uses an `.ipk` of its own
build; `.ipk` packages are not signed.

Python dependencies are installed from the OpenWrt feeds; PySocks is bundled in the package. Do not mix these dependencies with copies installed through `pip`. If you used `pip` before, first remove the old installation and restore the package dependencies.

Building it yourself needs Docker: from the source root run `openwrt/build.sh` for the default SDK or `openwrt/build.sh mediatek-filogic-24.10.4` for an `.ipk`. The result appears in `dist/`. The SDK must match the target OpenWrt version. To sign the `.apk` with your own key, set `SIGN_KEY=/path/private-key.pem` (the key is created with `openssl ecparam -name prime256v1 -genkey -noout -out private-key.pem`) and put the matching public key into `/etc/apk/keys/` on the router. `openwrt/publish-repo.sh` publishes the signed packages with an index to the `gh-pages` branch.

## 2. Find the files and choose the storage

| Where | What to configure |
|---|---|
| `/etc/config/nodes-tester` | UCI: `main.config_dir`, `main.data_dir`, `tester.enabled` |
| `/etc/nodes-tester/config.json` | All settings: subscriptions, groups and checks, tester, pipeline and schedule, admin panel ([field reference](../docs/CONFIG.md)) |
| `/etc/nodes-tester/singbox/` | sing-box base, rules and client files |
| `/opt/nodes-tester/nodes.json`, `whnodes.json` | Built nodes and groups of the router and the clients; `client-candidates.json` — the client group nodes from the tester |
| `/etc/sing-box-clients/` | Published client configs; the web server serves `by-token/` (`pipeline.clients.output`) |
| `/opt/nodes-tester/results/stats.db` | Results and statistics; created while running |

These are the default paths. If you change them in UCI, adjust the commands below and the related paths in JSON. `data_dir` must be on persistent storage; `/tmp` and `/var` on OpenWrt are in RAM. On a router with a small flash use a suitable USB drive.

The installer creates `config.json` from the template with a random `admin.token`. The service is off for now. `config.json` and `singbox/` are in the package list of configs kept on update; check their content before the first apply.

## 3. Configure the subscription and build the nodes

In the `subscriptions` section of `config.json` keep your sources; remove the demo URLs and entries you do not use. A minimal example:

```json
"subscriptions": [
  { "tag": "EXAMPLE", "url": "https://provider.example/sub/YOUR-TOKEN" }
]
```

`tag` sets the unique provider name in the interface. There can be several subscriptions; `happ://`, local files, AWG folders and your own outbounds (`"type": "inline"`) are supported as well. All URLs and keys stay on the router only.

Check `pipeline.router.domain_resolver`: it is `bootstrap` by default, and such a DNS tag must exist in your sing-box configuration. Build filters are set in the same place, in `pipeline.router`.

### Choose a compatible core

The parser keeps XHTTP and AmneziaWG. These nodes require `sing-box-lx`; the regular sing-box does not
accept them. With the regular core add to `config.json`:

```json
"pipeline": { "router": { "exclude_protocols": ["xhttp", "wg"] } }
```

`wg` excludes both AWG and regular WireGuard. The options and the check order are described
in the [compatibility guide](../docs/SING_BOX_COMPATIBILITY.md). Before applying always
run `nodes-tester pipeline router --dry-run`: it runs `sing-box check` with the
core installed on the router.

Fetch the subscriptions and build the fragment without changing the running sing-box yet:

```sh
mkdir -p /opt/nodes-tester/raw
nodes-tester fetch -c /etc/nodes-tester/config.json -o /opt/nodes-tester/raw/router.json --json
nodes-tester config -c /etc/nodes-tester/config.json --raw /opt/nodes-tester/raw/router.json -o /opt/nodes-tester/nodes.json --json
```

Check the report of both commands: sources, errors and the number of built nodes. When the guard check fails, fetch returns code `2` and keeps the previous raw; this is not a new successful update. Do not continue the first run with an empty or failed subscription.

`nodes.json` contains `outbounds` and, for the protocols that need them, `endpoints`. It is not a complete sing-box configuration. It has the group selectors `{tag}-auto-out` from the `groups` section (each with all nodes), `global-auto-out`, the fallback `failsafe-out` and the flat test selector `nodes-tester`. Strict groups refer to `block-out`, and `direct_fallback` to `direct-out`: these outbounds must be in the sing-box base (`{"type": "block", "tag": "block-out"}`, `{"type": "direct", "tag": "direct-out"}`).

## 4. Connect the fragment to sing-box

Back up the working configuration. Add the `outbounds`/`endpoints` from the fragment to it without duplicate tags, or merge the fragment with your base using `sing-box merge`. Do not replace the whole working config with `nodes.json` alone.

The tester needs three things:

- The sing-box 1.14 API service with a secret, reachable by the tester. It also serves the sing-box web panel:

  ```json
  "services": [
    { "type": "api", "tag": "api", "listen": "127.0.0.1", "listen_port": 9090,
      "secret": "<secret>", "dashboard": true }
  ]
  ```

  For the panel from the LAN set `listen` to the router address, for example `192.168.1.1`.
  The tester does not need `experimental.clash_api`.
- A SOCKS inbound for the checks, for example:

  ```json
  { "type": "socks", "tag": "nodes-tester-in", "listen": "127.0.0.1", "listen_port": 2080 }
  ```

- A rule at the beginning of `route.rules` sending this inbound to the created selector:

  ```json
  { "inbound": ["nodes-tester-in"], "outbound": "nodes-tester" }
  ```

`nodes-tester` itself is already created by the builder: all checked nodes must be its direct members, without nested test selectors. Keep user traffic on the working groups; do not send it to the test selector, which switches all the time.

Check the resulting file with your sing-box version before the restart, for example `sing-box check -c /etc/sing-box/config.json`. Apply it the usual way for your installation and make sure user traffic keeps working. Protocol support depends on the installed sing-box build; the list of subscription parsers does not guarantee support in the core.

### Prepare the pipeline

`nodes-tester pipeline router` builds the config from `/etc/nodes-tester/singbox/base.json` and updates the rules by `/etc/nodes-tester/singbox/rules.json`. Upload your files in the “sing-box files” section or put them into the `singbox/` directory over SSH. Give binary `.srs` files in `rules.json` by URL. Check that the base contains your working groups and routing settings.

In the “Pipeline” section press “Check” first and study the log, then “Apply”. The same commands are available over SSH: `nodes-tester pipeline router --dry-run` and `nodes-tester pipeline router`. Saving a file in the editor does not apply it to sing-box. The schedule is off until you enable it. The detailed order for both branches is in the [guide](../docs/USAGE.md#how-subscriptions-and-configs-are-updated).

## 5. Configure the tester and enable the admin panel

In `/etc/nodes-tester/config.json` check the required fields:

| Field | What to set |
|---|---|
| `api.url`, `api.secret` | Address and secret of the sing-box API service (`services[type=api]`) |
| `tester.socks` | SOCKS from the previous step, `socks5h://127.0.0.1:2080` by default |
| `tester.selector` | `nodes-tester` (default) |
| `groups` | sing-box groups and their checks, a single group `all` by default |
| `admin.enabled` | `true` for the web interface |
| `admin.listen`, `admin.port` | Router LAN interface address and port, `8088` by default |

Keep the `admin.token` created by the installer. Restrict access to the admin port to the local network; with `public_read: true` statistics can be viewed without the token. For private viewing set `false`. Do not open the port to WAN.

The tester itself picks the active node in each group by its checks, and the monitor brings the choice back if it was changed behind its back. Scheduled rotation can be turned off: `selection.rotation.enabled: false`. Checks download test files: watch the traffic usage. The full list of settings is in the [config.json reference](../docs/CONFIG.md).

Start the service:

```sh
uci set nodes-tester.tester.enabled=1
uci commit nodes-tester
/etc/init.d/nodes-tester start
logread -e nodes-tester
```

Open `http://192.168.1.1:8088/` with your address and port. For control enter `admin.token` from your config in the header. Results appear as checks run; a large set of nodes and pauses between requests to one host take time.

## 6. Update the package

Before updating take a snapshot of the current version. Keep the files of installed packages in
`/root/packages/`: the snapshot puts the package of its version found there, and the rollback reinstalls
exactly that one.

```sh
mkdir -p /root/packages
nodes-tester backup --stable
apk update
apk fetch -o /root/packages nodes-tester
apk upgrade nodes-tester
/etc/init.d/nodes-tester restart
logread -e nodes-tester
```

The upgrade from 0.4.x to 0.5.0 is not automatic: the settings moved into a single
`config.json` ([reference](../docs/CONFIG.md)); version 0.5.0 does not read the former `config-main`/`config-wh`, `pipeline.json` and
`user_nodes.json`. Build `config.json` before restarting the tester. The rating database
starts anew: the old one stays next to it as `stats.db.v1-<date>`.

In 0.6.0 the tester works without passes, by test result lifetimes (`tests.<name>.ttl`). Before
the restart remove the `tester.follow_rotation` and `tester.pause` fields from `config.json` if they
are set: the new version does not accept them. Node pauses counted in passes are not carried over; such
nodes are checked right after the start.

Without the repository download the file from the release into `/root/packages/` and run
`apk add /root/packages/nodes-tester-<new version>.apk`.

After `apk add` always restart the tester manually: the package does not do it itself.

The snapshot goes to `/root/backups/nodes-tester-<date>/`: configs, data without the database, the sing-box
config, an online copy of `stats.db`, cron jobs and the package file. The package manager keeps working
configs on update. If it left `/etc/config/nodes-tester.apk-new`,
compare it with the current file and delete it.

Rollback to the `--stable` snapshot:

```sh
nodes-tester rollback --check
nodes-tester rollback
```

The rollback stops the service, reinstalls the package from the snapshot, restores configs, the database and
cron jobs, then starts the service. The sing-box config is restored only if it changed and
passes `sing-box check`. The state before the rollback is kept in `/root/backups/failed-<date>/`.
`--keep-db` keeps the current database. Details:
[`scripts/router/README.md`](../scripts/router/README.md#backup-and-rollback).

Next: [where to see results, how to change settings and update nodes](../docs/USAGE.md).
