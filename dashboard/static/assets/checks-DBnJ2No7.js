const e=`**English** | [Русский](checks.ru.md)

# Tests and checks

The tester learns everything about a node by sending real traffic through it. There are two kinds of
results:

- **measurements** (tests) give numbers — latency, speed, loss — and feed the [rating](rating.md);
- **checks** give a verdict “yes / no / unknown” and decide which [groups](groups.md) may use the node.

Both have a **lifetime** (\`ttl\`): a fresh result is trusted, an expired one is repeated. There are no
“passes” over all nodes: each result expires on its own, and the tester always works on whatever has expired.

<a id="how-a-node-is-checked"></a>
## How one node is checked

The tester never connects to a node directly. It switches its own selector \`nodes-tester\` in the running
sing-box to the node through the sing-box API service and sends the test requests through the SOCKS inbound
\`tester.socks\`. So the measurements go exactly the way user traffic goes.

\`\`\`mermaid
flowchart TD
    pick["Take the next node from the queue"] --> sel["Switch the nodes-tester selector<br>to the node (sing-box API)"]
    sel --> conn["connectivity: exit IP and country"]
    conn --> ok{"Connection works?"}
    ok -->|no| fail["Gate failure: rating 0,<br>pause of the node"]
    ok -->|yes| due["Run the other measurements<br>whose lifetime is close to the end"]
    due --> save["Save raw results"]
    fail --> save
    save --> rate["Update the rating"]
    rate --> groups["Re-evaluate the groups<br>where the node is active or in the reserve"]
    groups --> pick
\`\`\`

- \`connectivity\` always runs first. Without a connection the other measurements are pointless and are not run.
- A visit runs every measurement of the node that is closer to its deadline than its spread, so one visit
  usually refreshes several results at once.
- A measurement that hit a sing-box restart (the pipeline applies a config, the core is updated) is not
  counted: the tester notices that the sing-box API stopped answering or the process changed, waits a minute
  and repeats.
- A node that disappeared from the selector between the queue and the visit is skipped without a failure.

<a id="lifetimes"></a>
## Result lifetimes and the queue

Each measurement lives \`tests.<name>.ttl\` (3 hours by default), each check \`checks.<name>.ttl\` (from 6 to 24
hours). The lifetime is shortened by a random share \`ttl_jitter\` (a tenth of \`ttl\` by default). The share is
constant for a node, so nodes checked one after another expire at different times and do not come back as a
batch.

The queue order:

1. A new node without measurements is checked right away.
2. Active nodes of groups and reserve candidates whose measurements expired.
3. All other nodes, starting from the oldest result.
4. With equal deadlines, the node of the host that has not been contacted for the longest goes first.

If the queue cannot keep up with \`ttl\` (many nodes, slow nodes), every node is still refreshed once per full
round, and active nodes and candidates keep their own deadlines.

<a id="host-gap"></a>
### Pauses between requests to one host

Nodes of one host (different accesses, ports, protocols of one server) are checked with a gap of at least
\`tester.host_gap\` (2 minutes by default) between them. Frequent connections to one server from one address with
different protocols look suspicious to providers and to DPI, and lead to blocks.

<a id="on-demand"></a>
### Testing on demand

The “Run testing now” button on the [“Checks”](runs.md) page puts all nodes into the queue without waiting for
the deadlines. It does not download subscriptions and does not change the configuration. While on-demand
testing runs, a second request is not accepted; the page shows how many nodes are left.

<a id="measurements"></a>
## Measurements

| Test | What it does | Result |
|---|---|---|
| \`connectivity\` | requests \`https://www.cloudflare.com/cdn-cgi/trace\` through the node | exit IP, exit country, Cloudflare data centre; the **gate**: without it the node is considered dead |
| \`latency\` | one request, time to the first byte (TTFB) | latency, ms |
| \`jitter\` | several requests in a row (\`samples\`, 5 by default, every \`interval\`) | mean, minimum, maximum latency, jitter, loss % |
| \`download\` | downloads a file for up to \`duration\` (20 s), 5 MB from Cloudflare by default | speed Mbit/s; throttling (the speed of the last 2 MB window to the first); hold (share of the file received) |
| \`reachability\` | opens several sites in parallel (Google, Cloudflare, YouTube by default) | how many of them answered |

Addresses, sizes and timeouts are set in the \`tests\` section ([reference](../CONFIG.md#tests)). Nodes with an
exit in a region can use their own addresses (\`for_region\`): for example, speed test servers in Russia for
Russian exits.

A refusal of a test server (HTTP 429, 5xx) is not the node's fault: such a measurement is marked as limited and
is neutral for the rating. A dropped connection or a timeout counts against the node.

<a id="exit-country"></a>
### Exit country

The node country is only the one measured by \`connectivity\`: the country of the exit IP as Cloudflare sees it. The
name the provider gave the node (with a flag or a city) is not used: providers often name nodes by the country
of their marketing, not of their exit. The measured country also selects the test addresses (\`for_region\`).

If the last connection failed, the admin panel shows the previous country with a pale flag and the time of that
measurement.

<a id="checks"></a>
## Checks

A check answers one question about a node. Groups refer to checks by name: in \`require\` (required) or in
\`prefer\` (preferred). The exit country, the IP type and the whitelist verdict are known for every node; the
AI checks, \`upstream\` and \`heavy\` run only for the groups that use them.

| Check | Question | Who runs it and how often |
|---|---|---|
| \`country\` | Is the exit country allowed for the group? | the \`connectivity\` measurement; \`ttl\` 24 h |
| \`ip_type\` | What kind of exit IP: residential, mobile, data centre, known proxy? | the tester every 5 minutes for new exit IPs (ip-api.com); \`ttl\` 24 h |
| \`geo_consistent\` | Does the IP country in the databases match the measured one? | together with \`ip_type\` |
| \`gemini\` | Does Google Gemini let the node in, and from which country does Google see it? | the [reserve](groups.md#reserve); \`ttl\` 24 h |
| \`openai\` | Does OpenAI let the node in? | the reserve; \`ttl\` 24 h |
| \`anthropic\` | Does Claude (Anthropic) let the node in? | the reserve; \`ttl\` 24 h |
| \`upstream\` | Does an upstream proxy work through the node? | the reserve; \`ttl\` 6 h |
| \`heavy\` | Does a long heavy download keep the speed? | the reserve; \`ttl\` 6 h |
| \`whitelist\` | Would the node pass the Russian mobile internet whitelist mode? | the tester every 5 minutes, locally; \`ttl\` 24 h |

The verdict of a check in the admin panel: ✓ passed, ✗ failed, ? no fresh result (never checked, expired or
the network failed during the check).

<a id="cheap-first"></a>
### Cheap checks first

The AI checks and the heavy download take time and traffic, so they run only for nodes that already passed the
cheap ones: the gate, the exit country and the IP type required by the group. They are run by the reserve
process of the group, not by the regular queue (see [the reserve](groups.md#reserve)).

\`\`\`mermaid
flowchart TD
    node["Node"] --> gate{"Gate passed,<br>rating above 0?"}
    gate -->|no| skip["Not a contender"]
    gate -->|yes| cheap{"Country and IP type<br>fit the group?"}
    cheap -->|no| skip
    cheap -->|yes| ai["Reserve: missing required checks<br>(AI services, upstream)"]
    ai --> passed{"All passed?"}
    passed -->|no| skip
    passed -->|yes| heavy["Heavy download"]
    heavy --> hv{"Speed kept?"}
    hv -->|no| skip
    hv -->|yes| reserve["Reserve candidate"]
\`\`\`

<a id="ip-type"></a>
### Exit IP type and country by the databases

Every 5 minutes the tester collects exit IPs without fresh data and asks ip-api.com about them in one batch
request. The answer gives the network owner (ASN), signs of a data centre, a mobile network and a known
proxy/VPN, and the country of the IP in the database. The request goes through the tester SOCKS on the best
healthy node (DNS filters on the router often block ip-api.com); if that fails, directly.

- \`ip_type\` passes when the type is in the allowed list of the group (\`kinds\`: \`residential\`, \`mobile\`,
  \`hosting\`, \`proxy\`).
- \`geo_consistent\` passes when the database country equals the measured exit country. AI services and
  streaming sites look at the databases: a node whose IP is listed in another country is treated as that country.

<a id="ai"></a>
### AI services

**Google Gemini** (\`gemini\`). The check opens gemini.google.com \`attempts\` times (3 by default) and reads from
the page the code of the country in which Google placed the client.

\`\`\`mermaid
flowchart TD
    start["Attempt 1…attempts:<br>open gemini.google.com"] --> res{"Result of the attempt"}
    res -->|"HTTP 200, allowed country"| okc["counts as allowed"]
    res -->|"forbidden country<br>(RU, BY, CN, HK, MO, IR, KP, CU, SY)"| bad["✗ failed"]
    res -->|"timeout or drop"| net["no information"]
    okc --> all{"All attempts done"}
    net --> all
    all -->|"every attempt allowed"| pass["✓ passed"]
    all -->|"some allowed, some network failures"| keep["? inconclusive:<br>the previous verdict stays"]
    all -->|"all attempts failed on the network"| bad
\`\`\`

One attempt is not enough: some nodes exit sometimes over IPv4 and sometimes over IPv6 of Cloudflare WARP, and
Google then sees a foreign country one time and Russia another time. The page is heavy, so the check has its
own \`timeout\` (20 s).

**OpenAI** (\`openai\`) and **Claude** (\`anthropic\`) work the same way (\`attempts\`, \`timeout\`), but look at the
decision of the service itself instead of a country: \`openai\` requests \`api.openai.com/compliance/cookie_requirements\`
(from a forbidden region it answers \`unsupported_country\`), \`anthropic\` requests \`api.anthropic.com/v1/models\`
without a key (from a forbidden region \`forbidden\`, from an allowed one \`authentication_error\`). An unrecognised
answer counts as a failure.

<a id="upstream"></a>
### Upstream proxy

\`upstream\` is needed when traffic leaves through a chain: the node of a group and behind it one more proxy, for
example a SOCKS server with a static IP (\`"detour": "ai-auto-out"\` in sing-box). A node may be alive but block
the proxy port, or the proxy may refuse the node's IP; then the chain is dead although every other check
passes. The reserve connects through the node to the \`checks.upstream.outbound\` server with its credentials from
the running sing-box config, requests \`url\`, and the node passes only if all \`attempts\` succeed.

<a id="heavy"></a>
### Heavy download

\`heavy\` downloads a big file (20 MB from Cloudflare by default, up to \`duration\` 30 s) and fails if the speed is
below \`min_mbps\` (5 Mbit/s). Some providers and DPI let a short test through and throttle long transfers; this
check catches them. A refusal of the download server (429, 5xx, 401/403/404) gives no verdict.

<a id="whitelist"></a>
### Whitelists

\`whitelist\` tells whether the node would work when the mobile operator allows only whitelisted addresses: the server
IP must be in the IP list and the TLS name (SNI) in the domain list (a domain or its subdomain); a node without
TLS needs only the IP. The lists are the text lists of
[ru-mobile-whitelist](https://github.com/ancored/ru-mobile-whitelist).

The check is local: no traffic goes through the node. The server domain is resolved with a DoH request to the
\`checks.whitelist.dns\` server from the running sing-box config, never through the router system resolver (that
would give the node name to the provider). This check also covers client-branch nodes that the router does not
have.

<a id="see-also"></a>
## See also

- [Rating](rating.md): how measurements turn into the score.
- [Groups and candidates](groups.md): how checks decide where a node may work.
- [\`tests\` and \`checks\` sections](../CONFIG.md#tests) of the reference.
`;export{e as default};
