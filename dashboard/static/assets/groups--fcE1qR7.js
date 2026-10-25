const e=`**English** | [Русский](groups.ru.md)

# Groups and candidates

A group is how you tell nodes-tester what a kind of traffic needs. “European nodes”, “a US exit”, “AI services
from a country they allow” — each is a group with its own checks. Routing rules in sing-box send traffic to the
group; the tester keeps the best fitting node selected in it.

<a id="selector"></a>
## A group in sing-box

Every group becomes a sing-box selector \`{tag}-auto-out\`: for a group \`eu\` it is \`eu-auto-out\`. The selector
contains **all** router nodes, the fallback outbound and, with \`direct_fallback\`, \`direct-out\`. Which node is
selected is decided by the tester through the sing-box API. Because every node is in every group, a group change
does not need a new configuration: the choice changes on the fly. The exception is “Except subscriptions”
(\`exclude_subscriptions\`): nodes of the marked subscriptions are not in the group at all. A subscription excluded
by every group is left out of \`failsafe-out\` and \`nodes-tester\` too, so the tester does not check it.

The build also creates:

- \`global-auto-out\` — a selector over the groups marked “Part of global-auto-out” (\`in_global\`, on by default);
- \`failsafe-out\` — a sing-box \`urltest\` over all nodes: sing-box itself keeps the fastest responding node there;
- \`nodes-tester\` — the tester's own selector for measurements.

Without a \`groups\` section in \`config.json\` a single group \`all\` is built. Client groups are set separately
([client node groups](subscriptions.md#client-groups)): there is no tester on a client, so a client group is a urltest over the
nodes that passed its checks on the router.

<a id="checks"></a>
## Required and preferred checks

For every [check](checks.md#checks) a group sets one of three modes:

| Mode | Effect |
|---|---|
| ignored | the check does not matter for the group |
| **required** (\`require\`) | without a fresh success of the check the node cannot be a candidate |
| **preferred** (\`prefer\`) | the node may be a candidate without it, but nodes that passed go first |

Two checks have conditions:

- **Exit country** (\`country\`): \`countries\` (ISO codes), \`regions\`, and the exclusions \`exclude_countries\`,
  \`exclude_regions\`. For example, “any country except Russia”: \`exclude_countries: ["ru"]\`.
- **Exit IP type** (\`ip_type\`): \`kinds\` — the allowed types: \`residential\`, \`mobile\`, \`hosting\` (data centre),
  \`proxy\` (an IP known as a VPN or proxy).

A typical AI group requires an exit outside Russia, a match of the country by databases and all three AI
services, and prefers residential or mobile IPs:

\`\`\`json
{
  "tag": "ai",
  "strict": true,
  "require": { "country": { "exclude_countries": ["ru"] }, "geo_consistent": {},
               "gemini": {}, "openai": {}, "anthropic": {} },
  "prefer": { "ip_type": { "kinds": ["residential", "mobile"] } }
}
\`\`\`

<a id="candidates"></a>
## Who is a candidate

\`\`\`mermaid
flowchart TD
    n["Router node"] --> g{"Gate passed and<br>rating above 0?"}
    g -->|no| out["Not a candidate"]
    g -->|yes| r{"Pause, quarantine<br>or exclusion?"}
    r -->|yes| out
    r -->|no| req{"Fresh successes of all<br>required checks?"}
    req -->|no| out
    req -->|yes| hv{"Fresh failure of<br>the heavy download?"}
    hv -->|"yes, and there are<br>other candidates"| out
    hv -->|"no (or no one else)"| cand["Candidate"]
    cand --> tier["Tier: how many preferred<br>checks are not passed"]
\`\`\`

A failed heavy download excludes a node only while there are other candidates: if every candidate failed it, the
group still chooses among them rather than staying empty.

The candidates of a group are always visible:

- the [“Groups”](overview.md#groups) block of the Overview: the active node, the number of candidates and the reserve;
- the [“Groups”](node-card.md#groups) tab of a node card: for each group whether the node is a candidate and the
  verdicts of every check with their age;
- under each group in the group editor on [“Subscriptions and build”](subscriptions.md#groups).

<a id="tiers"></a>
## Order of candidates: tiers and rating

Candidates are ordered by **tier**, and inside a tier by rating:

1. tier 0 — all preferred checks passed; tier 1 — one is not passed; and so on;
2. a node rated below \`tier_floor\` (75 by default, \`selection.tier_floor\` or the group's own \`tier_floor\`) goes
   after all other tiers: a weak node that passed the preferred checks should not overtake strong nodes that did
   not;
3. inside a tier — by rating, higher first.

The active node is always taken from the best available tier. A replacement by quality never moves the group to a
worse tier.

<a id="reserve"></a>
## Reserve

The reserve is a short list of candidates checked in advance, so that the active node can be replaced instantly. Its
size is \`selection.reserve\` (3) or the group's own \`reserve\`. The reserve is kept by a separate background process,
independent of the regular measurements:

\`\`\`mermaid
flowchart TD
    pool["Contenders: by tier, then by rating<br>(below rotation.min_score — last)"] --> ex["Skip the active node, the last<br>avoid_recent nodes and the hosts<br>already in the reserve"]
    ex --> miss["Run the missing required checks<br>(AI, upstream), then the heavy download"]
    miss --> pass{"All passed?"}
    pass -->|no| next["Knock out, take the next contender"]
    next --> miss
    pass -->|yes| pref["Run the preferred AI checks:<br>a failure only lowers the tier"]
    pref --> add["Add to the reserve"]
    add --> full{"Reserve full?"}
    full -->|no| pool
    full -->|yes| watch["Watch the deadlines of reserve checks;<br>look for a better node"]
    pool -->|"no contenders left"| lift["Lift a pause or quarantine<br>one node at a time"]
\`\`\`

Details of how the reserve works:

- A fresh result is not repeated: a node that passed Gemini an hour ago is not checked again.
- After the active node changes (rotation, emergency, quality), the reserve is refilled at once.
- When a check of a reserve member expires, it is repeated. The required checks of the active node are repeated 5
  minutes before they expire; a failure means a replacement from the reserve.
- When the reserve is full, after every measurement the tester looks for a node better than the worst reserve
  member: a better tier or a rating higher by \`selection.reserve_margin\`. Such a node runs the same checks and takes
  the place; if it fails, it is not tried again for 15 minutes.
- A reserve has at most one node per host and no node of the host of the active node: nodes of one server fail
  together.

<a id="lifting"></a>
### When there are no contenders

If the contenders run out and the reserve is not full, the reserve process lifts the [pause or
quarantine](restrictions.md) from the nodes restricted the longest, one at a time, and checks them right away.
It does not lift nodes that certainly will not fit (a known wrong exit country, a fresh failure of a required check),
nodes of the hosts already in use or penalised, and manual exclusions. At most three lifts per group per round;
a node that fails the gate again goes back to quarantine and is lifted no earlier than in 6 hours. With nobody to
lift, a new attempt comes in 15 minutes or when new nodes appear.

<a id="fallback"></a>
## No candidates: fallback outbounds

\`\`\`mermaid
flowchart TD
    none["The group has no candidates"] --> strict{"Strict group?"}
    strict -->|no| fs["failsafe-out:<br>the fastest node by sing-box urltest"]
    strict -->|yes| dir{"direct_fallback?"}
    dir -->|no| block["block-out:<br>the group traffic is blocked"]
    dir -->|yes| direct["direct-out:<br>traffic goes directly"]
    fs --> mon{"The monitor: failsafe-out<br>failed monitor.fails times<br>and direct_fallback is on?"}
    mon -->|yes| direct
    mon -->|no| fs
    block --> back["As soon as a candidate appears,<br>the group returns to it"]
    fs --> back
    direct --> back
\`\`\`

| Group settings | Without candidates | When to use |
|---|---|---|
| non-strict | \`failsafe-out\` | general traffic: any working node is better than nothing |
| non-strict + \`direct_fallback\` | \`failsafe-out\`, and if it fails too, \`direct-out\` | traffic that may go directly in an emergency |
| **strict** | \`block-out\` | traffic that must never leave through an unsuitable node (AI services, accounts tied to a country) |
| strict + \`direct_fallback\` | \`direct-out\` | traffic that may go directly but not through an unsuitable node |

\`block-out\` must exist in \`base.json\` (\`{"type": "block", "tag": "block-out"}\`). A switch to a fallback outbound
is recorded in the [switch history](switches.md) with the reason “No candidates: fallback outbound” and can be sent as a \`failsafe\`
[notification](settings.md#notifications).

<a id="regions"></a>
## Regions

A region is a named list of countries for the \`country\` check and for \`for_region\` of tests. Built-in regions:

| Region | Countries |
|---|---|
| \`eu\` | European countries |
| \`us\` | \`us\` |
| \`ru\` | \`ru\` |
| \`other\` | every country not in \`eu\`, \`us\` and \`ru\` |

A built-in region can be overridden, and your own regions are added in the “Regions” block of the group editor or
in the \`regions\` section: \`{"asia": ["jp", "sg", "kr"]}\`.

<a id="order"></a>
## Order of groups

The order of groups in the editor is their order in the sing-box configuration and in \`global-auto-out\`. It does not
affect which node a group chooses.

<a id="see-also"></a>
## See also

- [Switching](switching.md): when and how the active node changes.
- [\`groups\` section](../CONFIG.md#groups) of the reference.
`;export{e as default};
