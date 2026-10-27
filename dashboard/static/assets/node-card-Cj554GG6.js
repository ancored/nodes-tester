const e=`**English** | [Русский](node-card.ru.md)

# Node card

Everything about one node on one page: its state, the groups it fits, the latest results, traffic, events and actions. The card
opens by clicking a node key in any table; its address contains the key (\`#/nodes/8d31a426.0be1f7a2\`), so a link to the card
stays valid while the node exists. The card of a node gone from the subscriptions opens from the [archive](nodes.md#archive).

<a id="header"></a>
## Header

- the full node tag with the key in two colours ([what the key is](concepts.md#nodes));
- the name of the node in the subscription;
- for a [chain](checks.md#entry) — entry country → exit country with the IPs and networks (ASN) of the entry and the exit, and an exit
  through Cloudflare WARP; for a direct node — its single IP;
- badges: the groups of the node and its [client groups](subscriptions.md#client-groups), the groups whose reserve has the node,
  presence in the list, the rating, restrictions, “Selected by the switcher”;
- for a paused or quarantined node — until when, after that it is checked again.

“Selected by the switcher” is the choice of the tester. It does not prove that the sing-box selector really points to the node
or that traffic flows through it.

The tabs under the header are kept in the address (\`?tab=groups\`).

<a id="state"></a>
## State tab

**Rating change** — a chart of the last 24 rating values. **Rating measurements** — all saved values with the date and time; the
period can be limited with “From” and “To”.

**Technical details** (after logging in):

- the node JSON as it is in \`nodes.json\`, in two colours: blue fields go into the first part of the key (server settings),
  purple into the second (credentials); uncoloured fields (\`tag\`, \`domain_resolver\`) are not part of the key;
- **Key check**: the key recalculated from this JSON; “✓ OK” — it matches; a mismatch means the node in the database and in
  \`nodes.json\` differ (for example, after a format change);
- **CRC32 input strings** — exactly what is hashed for each part;
- if the node is no longer in \`nodes.json\`, the JSON is restored from the database, and fields outside the key except \`tag\` are
  missing;
- the restriction reason and the number of consecutive failures.

<a id="groups"></a>
## Groups tab

For every group: the state of the node in it and the verdict of each check of the group.

| State | Meaning |
|---|---|
| active (group) | the node is selected in the group now |
| in reserve | checked in advance to replace the active node |
| candidate | may become active ([conditions](groups.md#candidates)) |
| not a candidate | is in the group but does not pass a required check, is restricted or has rating 0 |
| not a member | not in the group selector |

Checks: ✓ passed, ✗ failed, ? no fresh result. Preferred checks are marked; hover to see the age of the result. A group without
checks accepts any working node. A node becomes active only if all required checks are fresh and passed; preferred ones move
it up the queue ([tiers](groups.md#tiers)).

<a id="results"></a>
## Results tab

The latest result of each measurement and check with the time it was obtained, and the ip-api data of the last exit IP: its
type with the network (ASN) and whether the country by the databases matches the measured one. A failure shows its cause in words,
and “Details” has the error text and the sing-box error for the tester connection at the time of the check:

| Cause | What happened |
|---|---|
| DNS | sing-box could not resolve the name of the node server |
| TLS | the server certificate or the Reality check did not pass |
| TCP | no TCP connection to the node server: timeout, refusal, no route |
| Transport | the node server answered the transport (WebSocket, XHTTP) with an HTTP error |
| Handshake | TCP connected, but the protocol handshake hung: blocking on the path or a dead server |
| Reset | the connection was reset or closed by the other side |
| Froze | the transfer stopped midway |
| Too slow | the speed is below the threshold |
| Service refused | an AI service refused by region or limit; the tunnel works |
| Check target | the check server answered with an error (404, 5xx): the node is not to blame |
| No answer | the request timed out without a sing-box error |

No results does not mean a failed check: the node may not have been measured yet. The meaning of each value is on the
[Results](nodes.md#results) page.

<a id="traffic"></a>
## Traffic tab

Incoming and outgoing user traffic through the node over the stored period. No saved traffic does not prove the node was not
used: check that traffic collection is on (\`storage.traffic.enabled\`) and the retention period.

<a id="events"></a>
## Events tab

- **Restrictions and presence** — node events from the last 500 records of the shared event log: pause, quarantine, recovered,
  excluded, exclusion removed, restriction lifted, disappeared from the list; who did it — automatically or the administrator.
- **Switches** — the records of the last 200 switches that involve the node.

<a id="actions"></a>
## Actions tab

Every action asks for confirmation and needs the administrator token.

| Action | What happens |
|---|---|
| Exclude / Remove exclusion | the node is excluded from checks indefinitely; if it is active, its groups switch to another node at once ([exclusion](restrictions.md#ban)) |
| Quarantine | checks are postponed for \`selection.quarantine.duration\`; an active node is replaced at once |
| Lift restriction | the pause or quarantine is lifted and the node is queued for a check; a manual exclusion stays |
| Select in group | the node becomes active in the chosen group now ([manual choice](switching.md#manual)) |

“Select in group” lists only groups where the node is a candidate: a positive rating, all required checks passed, no
restrictions. If there are none, the action is unavailable. Automation keeps working and may change the choice later.
`;export{e as default};
