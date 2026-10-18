const e=`**English** | [Русский](subscriptions.ru.md)

# Subscriptions and build

This page edits the part of \`config.json\` that decides **which nodes get into sing-box and how they are grouped**: the
subscriptions, the build parameters, the groups and their checks. It has two tabs: “Subscription sources” and “Build and
groups”, and two branches: “Router” and “Clients” (the chips at the top).

Saving the file does **not** fetch subscriptions and does not change the running sing-box. New subscriptions, filters and
groups reach sing-box with the next [pipeline](pipeline.md) run; see [How changes reach sing-box](#apply).

<a id="branches"></a>
## Router and Clients branches

| Branch | Result | Used by |
|---|---|---|
| Router | \`nodes.json\` | the router sing-box and the tester |
| Clients | \`whnodes.json\` | client configs built on the [“Rules”](rules.md#clients) page |

A subscription belongs to the router, the clients or both (the “Branch” field). The branch chips only filter the form: the
document is the same, and unsaved edits in another branch are kept. More about branches: [concepts](concepts.md#branches).

<a id="sources"></a>
## Subscription sources

Each subscription is a block with the fields:

| Field | Meaning |
|---|---|
| Provider name (unique tag) | the first part of the tags of its nodes; Latin letters without spaces are best |
| Use the subscription | off — the subscription stays in the file but is not fetched |
| Branch | Router, Clients or Both branches |
| Source | the kind of the source, see below |
| Exclude nodes by a name substring | nodes whose provider name contains one of these substrings are skipped (for example \`promo, test\`) |

URLs and secrets are hidden; “Show secrets and subscription URLs” above the form shows them. Do not send screenshots with them.

<a id="kinds"></a>
### Source kinds

| Kind | What to enter |
|---|---|
| URL / link | a subscription URL (\`https://…\`) or a single share link (\`vless://\`, \`vmess://\`, \`trojan://\`, \`ss://\`, \`hy2://\`, \`sub://\` …). Base64 lists, Clash/Mihomo YAML, sing-box JSON and Xray JSON are recognised |
| Happ · encrypted link | a \`happ://crypt…\` link (crypt–crypt5) of the Happ app; see [Happ keys](#happ-keys) |
| File | the path to a subscription file on the router, relative to \`config.json\` |
| AWG folder | a folder with WireGuard/AmneziaWG \`.conf\` files: the format \`awg\` and the extension \`.conf\` |
| Amnezia Premium · vpn:// link | the \`vpn://\` link of an Amnezia Premium subscription; the tester issues country files itself, see [Amnezia Premium](#amnezia) |
| Own nodes (sing-box outbounds) | a JSON array of your own sing-box outbounds; their tags are set by the build, the provider is the subscription tag |

XHTTP and AWG nodes need the \`sing-box-lx\` core; for the regular sing-box exclude the \`xhttp\` and \`wg\` protocols in the
[branch filters](#build). Details: [sing-box compatibility](../SING_BOX_COMPATIBILITY.md).

<a id="user-agent"></a>
### User-Agent

For URL subscriptions you can choose the User-Agent of the download: the standard browser one (Safari, by default), \`curl\`,
\`clashmeta\` or your own. Providers often choose the response format by it. It is sent only on HTTP downloads, not for share
links and files. On an empty HTTP response the loader repeats the request with \`clashmeta\`.

<a id="hwid"></a>
### Device identifier (HWID)

Some subscription panels limit the number of devices and require the device identifier. “Send the HWID and device description”
turns it on for a URL subscription; happ subscriptions always send it. The router HWID is shown under the list of
subscriptions; it is stored in \`device.json\` next to \`config.json\`. A custom HWID for one subscription is set with the
\`X-Hwid\` header.

<a id="state"></a>
### Fetch state

After the subscriptions are fetched, each block shows:

- “Fetched” or “Failure: previous nodes are in use” / “Failure: no nodes”, the number of nodes and the time of the last successful
  fetch; the error text on a failure;
- **Nodes in the active config / by the last poll** — how many nodes of this subscription are in sing-box now and how many the next
  build will give; “new” and “gone” reach sing-box with the next build;
- if the panel sends metadata: the subscription title, used and total traffic, the expiry date with the days left (3 days and less
  are highlighted), and the provider message.

The end of a subscription and 90 % traffic usage can be sent as an \`expiry\` [notification](settings.md#notifications).

<a id="happ-keys"></a>
### Happ keys

\`happ://crypt…\` links are encrypted with the keys of the Happ app. The keys are not part of nodes-tester: they are downloaded
from the source in the “Happ keys” block (by default \`src/keys.rs\` of the hpwnr project on GitHub), stored in
\`happ-keys.json\` next to \`config.json\` and refreshed when no key fits a link. The source can be your own URL or a path to a
\`keys.rs\` or JSON file on the router. “Happ HTTP headers” of a subscription edits the headers sent with the download,
including its own User-Agent; a separate \`user_agent\` is ignored for this kind.

<a id="amnezia"></a>
### Amnezia Premium

For an Amnezia Premium subscription the tester itself issues and replaces AmneziaWG country files through the Amnezia gateway,
using the rating of their nodes.

| Field | Meaning |
|---|---|
| vpn:// link | the subscription link from the Amnezia app; it contains the account key, keep it secret |
| Folder for country files | where the \`<country code>.conf\` files are kept |
| Gateway key file (PEM) | the public key of the Amnezia gateway (it is not part of the project: it is taken from the official AmneziaVPN app, see the [reference](../CONFIG.md#amnezia-premium)) |
| Gateway proxy | the proxy for the gateway requests, for example \`socks5h://127.0.0.1:2080\` (through a node, if the gateway is blocked); empty — the download proxy |
| Country files to keep | how many countries to hold; empty — all slots of the subscription. Slots above the limit stay free for the app |
| Replace a country rated below | the rating threshold, 40 by default |
| Do not issue countries | country codes never issued, \`ru\` by default |

Once every 3 hours (10 minutes after the tester starts) the tester compares the files with the rating:

\`\`\`mermaid
flowchart TD
    start["Every 3 hours"] --> low["For each country file: find the node<br>by the server address and port and its rating"]
    low --> all{"All rated countries<br>below the threshold?"}
    all -->|"yes (more than one)"| skip["The protocol or the network is the cause:<br>replace nothing"]
    all -->|no| hold{"A country below the threshold<br>for 6 hours?"}
    hold -->|yes| revoke["Revoke the file on the gateway,<br>delete it, remember the country as blocked"]
    hold -->|no| upd
    revoke --> upd{"The server of a file updated<br>after it was issued?"}
    upd -->|yes| reissue["Issue the file anew<br>(same slot, new key)"]
    upd -->|no| free
    reissue --> free{"Free places<br>(slots and free subscription slots)?"}
    free -->|no| done["Done; results in the log<br>and the subscription notification"]
    free -->|yes| untried{"Untried countries left?"}
    untried -->|yes| rnd["Issue a random untried country"]
    untried -->|no| oldest["Issue the longest-blocked country again"]
    rnd --> done
    oldest --> done
\`\`\`

- A country blocked in the same check is not issued again at once.
- Countries issued elsewhere (in the app; there is no file in the folder) are never touched: a new issue would break that file.
- The memory of tried and blocked countries is \`state.json\` in the folder; delete a country from \`blocked\` to allow it again.
- New files reach sing-box with the next subscription poll and router build.

<a id="fetch"></a>
### Download protection

Fields of \`pipeline.fetch\` that protect the router from a bad or missing subscription:

| Field | Meaning |
|---|---|
| Download timeout | the time limit of one download (\`2m\`) |
| Minimum share of remaining nodes | if a subscription suddenly gives fewer nodes than this share of the previous count (0.5), the previous nodes are kept: a broken answer does not wipe out the configuration |
| HTTP retries | retries of a failed download (3) |
| Keep previous nodes on failure | how long the last working nodes of a failed subscription stay in the build (\`48h\`); after that they are dropped |
| Poll router subscriptions every | a separate poll of router subscriptions that only refreshes the raw nodes and the state shown here, without building and applying (\`3h\`; \`0s\` — only during a build). It also feeds the [rebuild on degradation](switching.md#rebuild) |
| Download proxy | a proxy for downloads, for example \`socks5://127.0.0.1:2080\` (through sing-box); empty — directly |

The result of each fetch is in the [pipeline log](pipeline.md#log).

<a id="build"></a>
## Build and groups

<a id="schedule-filters"></a>
### Schedule and filters

| Field | Meaning |
|---|---|
| Branch enabled (scheduled build) | the branch is built by schedule; off — only manually |
| Start time | times of the day \`HH:MM\`, comma-separated; the same as the schedule on the [“Pipeline”](pipeline.md#schedule) page |
| Exclude outbound types | sing-box outbound types that are dropped, for example \`shadowsocksr\` |
| Exclude protocols / transports | protocol descriptors that are dropped: \`xhttp\`, \`wg\`, \`reality\`… — by the protocol descriptor of the node |
| Node DNS resolver tag | the sing-box DNS server used to resolve node addresses (\`domain_resolver\`, \`bootstrap\` by default) |

Bare \`vless\` and \`vmess\` without TLS and Reality are always dropped: they would send traffic in the clear.

<a id="selector"></a>
### Selector groups

“Interrupt existing connections when the node changes” (\`selector.interrupt_exist_connections\`): on — connections of the old
node are closed at once and reopen through the new one; off — they continue through the old node until they end. Without a value
the sing-box default applies.

<a id="failsafe"></a>
### failsafe-out group

\`failsafe-out\` is a sing-box \`urltest\` over all nodes — the shared fallback of non-strict groups
([fallback outbounds](groups.md#fallback)). Its fields go to sing-box as they are: the check URL, the check interval, the latency
tolerance in ms (a node is replaced only if another is faster by more than this), the idle timeout, interrupting connections.

<a id="groups"></a>
### Node groups

The group editor (router branch only). Every group block has:

- the current state of the group in the running tester: the active node or the fallback outbound, candidates of members, the reserve,
  the first candidates with their rating;
- **Group tag** — lowercase Latin letters, digits and \`_\`; not \`global\`, \`nodes\`, \`failsafe\`. The selector is \`{tag}-auto-out\`;
- **Reserve candidates** — the group's own reserve size; empty — as in the settings (\`selection.reserve\`);
- **Strict**, **Allow direct outbound**, **Part of global-auto-out** — see [fallback outbounds](groups.md#fallback);
- a table of checks: for each check “ignored”, “required” or “preferred”; the exit country and the IP type have their own
  conditions ([details](groups.md#checks)). Unknown regions and country codes that are not two letters are highlighted;
- “Up”, “Down” change the order of groups, “Delete group” removes it.

Changes of group checks need no rebuild: the tester picks them up after its restart (it restarts by itself after saving). Adding,
removing or renaming a group changes the selectors and needs a router pipeline run.

<a id="regions"></a>
### Regions

A JSON object “region → list of countries”, for example \`{"asia": ["jp", "sg", "kr"]}\`. Built-in regions (\`eu\`, \`us\`, \`ru\`,
\`other\`) can be overridden here. [More about regions](groups.md#regions).

<a id="saving"></a>
## Editing and saving

The toolbar above the form is common to this page and [“Settings”](settings.md):

- **Reload file** — read \`config.json\` anew (unsaved edits are lost after a confirmation).
- **Advanced JSON** / **To the form** — edit the whole file as JSON. The JSON contains secrets and full subscription URLs.
- **Review changes** — the list of changed fields (values are hidden to protect secrets) and the “Confirm and save the file”
  button.

How saving works:

- the server checks the file with the same loader as the tester: an unknown field or a wrong value is rejected with the field path;
- the form saves only the changed sections, so an edit of subscriptions does not conflict with an edit of groups in another window;
  if the same section was changed over SSH or in another window, saving is blocked until you reload;
- an empty field means the default (shown in grey) and is not written; unknown fields and overrides in the file are kept;
- after saving, the tester restarts by itself and the page reports when it is back with the new settings: checks stop for a few
  seconds, user connections are not dropped. While the pipeline runs, the restart is postponed;
- leaving the page, reloading or closing it with unsaved edits asks for confirmation; a draft is not a backup.

<a id="apply"></a>
## How changes reach sing-box

1. Subscriptions, build parameters and groups are stored in \`config.json\`; the pipeline and the tester read it.
2. The pipeline runs by the schedule of the branch (\`pipeline.router.at\`, \`pipeline.clients.at\`) or manually on the
   [“Pipeline”](pipeline.md) page: it fetches subscriptions, builds \`nodes.json\`, checks the result with \`sing-box check\` and
   applies it. sing-box restarts only if the configuration changed; without connectivity the previous configuration comes back.
3. The tester picks up new nodes within a minute; changes of groups and tester settings after its restart.

Fetched subscriptions are \`raw/router.json\` and \`raw/clients.json\`, the build result \`nodes.json\` and \`whnodes.json\` in the data
directory (\`/opt/nodes-tester\` in the OpenWrt package). The same steps by hand on the router:

\`\`\`sh
nodes-tester fetch -c config.json --branch router -o raw/router.json
nodes-tester config -c config.json --branch router --raw raw/router.json -o nodes.json
\`\`\`
`;export{e as default};
