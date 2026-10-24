**English** | [Русский](nodes.ru.md)

# Nodes

The “Nodes” section has five views of the same nodes: the list with the state, the rating factors, the latest
measurements, restrictions with provider quality, and the archive of nodes gone from the subscriptions. The chips under the
page title switch between them. Every table works as described in [Tables](access.md#tables); a click on a node key opens the
[node card](node-card.md).

<a id="all-nodes"></a>
## All nodes

The nodes of the current router subscriptions, as the tester sees them in sing-box. New nodes appear here within a minute
after the configuration is applied, without measurements, and the tester checks them right away. Nodes gone from the
subscriptions move to the [archive](#archive).

| Column | Meaning |
|---|---|
| Provider | the `tag` of the subscription |
| Protocol | the protocol and transport, one part per line: `vless` / `grpc` / `reality` |
| Node | the node key `node_crc.cred_crc` ([what it is](concepts.md#nodes)) |
| Country | the measured exit country |
| Chain | for a [chain](checks.md#entry) — entry country → exit country, an orange cloud — exit through Cloudflare WARP; a dash — the entry and exit IPs are the same or the entry is not known yet. Hover for the IPs |
| Reserve | the groups whose [reserve](groups.md#reserve) has the node |
| Rating | 0–100 ([how it is computed](rating.md)); “No measurements” — not measured yet |
| State | presence, restrictions and the switcher choice (see below) |
| Name in the subscription | the node name given by the provider; a dash — there is none (for example, AWG files) |

The “State” filter selects one of the overlapping states: active, no measurements, paused, quarantine, excluded, client (WH).
A node can be in several at once.

Client-branch nodes that the router does not have are marked “Whitelists only” / “client (WH)”: the tester does not measure
them, they only get the [whitelist check](checks.md#whitelist) and have no card.

<a id="rating"></a>
## Rating

The rating and its factors for every measured node; the active nodes are highlighted.

| Column | Meaning |
|---|---|
| Country or chain | the exit country; for a [chain](checks.md#entry) — entry country → exit country, as in “All nodes” |
| rating | 0–100; green 70 and above, red 20 and below |
| selected | active in at least one group |
| reserve | groups whose reserve has the node |
| reliability … speed | the six [components](rating.md#components) normalised from 0 to 1: higher is better. These are not milliseconds or Mbit/s |
| history | the trajectory of the rating over the last 24 values |
| samples | how many measurements the rating is built from |

The physical values are on the [Results](#results) page.

<a id="results"></a>
## Results

The latest result of each measurement and check of every node. The time of each result is in the node card, “Results” tab.

| Column | What it shows |
|---|---|
| Country or chain | the exit country; for a [chain](checks.md#entry) — entry country → exit country, as in “All nodes”. Pale — from a previous measurement: the last connection failed (hover for the time) |
| IP | the exit IP type by ip-api.com: a building — data centre, a phone — mobile network, a house — residential provider; a mask — the IP is known as a VPN/proxy; a flag below — the database puts the IP in another country than the measured one. Red icons are worse for AI services. Hover for the IP and the network (ASN) |
| Link | `connectivity`: ✓ or ✗ |
| Latency | time to the first byte, ms |
| Jitter / loss | jitter in ms / loss in % |
| Speed, Mbit/s | download speed; `×0.85` — the throttling ratio (the speed at the end of the download to the speed at the start; 1 — no slowdown); “(ограничение сервера)” — the test server refused, the measurement is neutral |
| Sites | how many sites of `reachability` answered, of how many |
| Heavy download | Mbit/s of the long download (reserve candidates only) |
| Google AI, OpenAI, Claude AI | ✓ available, ✗ not available, ? network failure (the previous verdict stays); under Google AI — the country in which Google sees the node. Only candidates of groups that require these checks are checked |
| Proxy through the node | the [upstream](checks.md#upstream) check |
| Whitelist | ✓ — the whitelist mode lets the node through: its IP and SNI are in the lists ([details](checks.md#whitelist)); hover for the addresses and SNI |

“—” means the measurement has not been made. A red value is a failure: click it to see the error text. The AI, link and
whitelist columns hold only a mark; the full name of a column is in the sorting list.

<a id="restrictions"></a>
## Restrictions and quality

Four tables about the health of nodes and providers. Only nodes present in the tester list are counted; missing ones are in
the [archive](#archive).

<a id="provider-quality"></a>
### Provider quality

| Column | Meaning |
|---|---|
| total nodes | measured nodes of the provider |
| share with rating 0 | how many of them are dead now; above 50 % is highlighted |
| avg. rating (without zeros) | the average rating of the working nodes |

A provider with a high share of zeros is either blocked in your network or sells dead nodes.

<a id="paused"></a>
### Paused and quarantined

Nodes the tester has set aside after failures ([how it works](restrictions.md)).

| Column | Meaning |
|---|---|
| in config since | when the node first appeared |
| status | checks paused or quarantine |
| failures in a row | consecutive gate failures |
| × quarantined | how many times over its history the node went to quarantine |
| last quarantine | when it last went there |
| in status | how long the current pause or quarantine lasts |
| next probe | when the restriction expires and the node is checked again |

<a id="long-lived"></a>
### Long-lived

Nodes in service sorted by age: the oldest first. Besides the age and the rating it shows how many times the node was in
quarantine and in pauses: a provider whose nodes live long and rarely fail is a reliable one.

<a id="dropouts"></a>
### Quick dropouts

Nodes sorted by how soon they first went to quarantine after appearing. Many quick dropouts of one provider mean its new
nodes are blocked quickly.

<a id="archive"></a>
## Archive

Nodes that disappeared from the tester list: the provider removed them from the subscription, a filter of the build excluded
them, or the subscription failed and its nodes were dropped. Their history stays in the database for `storage.retention`
(30 days) after the last presence and is then erased. The card of an archived node opens from here.

<a id="archive-kpis"></a>
### Counters

| Counter | Meaning |
|---|---|
| In the archive | all archived nodes |
| Gone in 7 days | how many disappeared in the last week |
| Median lifetime | the median time from the first appearance to the disappearance |
| Left while working | removed while the rating was still positive |

<a id="losses"></a>
### Losses by provider

How many nodes each provider lost, their median lifetime, the average peak rating, and how many were removed while still
working. A provider that removes working nodes rotates its servers by itself.

<a id="history"></a>
### History of missing nodes

| Column | Meaning |
|---|---|
| first seen, removed on, lived | the lifetime of the node |
| rating | the rating trajectory over the whole lifetime |
| peak, avg., at the end | the maximum, average and last rating |
| × quarantined | quarantines over the lifetime |
| how it left | the state at the last presence: positive rating, rating 0, was quarantined, was excluded, no measurements |
| erased in | when the record is deleted from the database (`storage.retention`) |
