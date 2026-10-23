const e=`**English** | [Русский](switching.ru.md)

# Switching

The switcher decides which node is active in each [group](groups.md). It reacts to failures at once, moves to a
clearly better node when the advantage holds, and rotates nodes on a schedule so that no single node carries all the
traffic for long. Every change is recorded in the [switch history](switches.md) with its reason.

<a id="when"></a>
## When a group is evaluated

- after every measurement of the active node or of a reserve node of the group;
- for a group without an active node or stuck in an emergency — after a measurement of any of its nodes;
- when the rotation deadline of the group comes;
- when the [monitor](#monitor) finds the active node dead;
- when a node is restricted, excluded or chosen manually in the admin panel.

<a id="algorithm"></a>
## How a group is evaluated

\`\`\`mermaid
flowchart TD
    start["Evaluate the group"] --> c{"Are there candidates?"}
    c -->|no| fb["Fallback outbound:<br>failsafe-out / block-out / direct-out"]
    c -->|yes| a{"Is there an active node?"}
    a -->|no| init["First choice:<br>the best reserve node"]
    a -->|yes| bad{"Active node dead, restricted<br>or failed a required check?"}
    bad -->|yes| em{"Is there a replacement<br>of another host?"}
    em -->|yes| emg["Emergency replacement:<br>away from the failed provider,<br>balanced choice"]
    em -->|no| stuck["Emergency without a replacement:<br>recorded once, the group waits"]
    bad -->|no| rot{"Rotation due and the reserve<br>has a node of the same<br>or a better tier?"}
    rot -->|yes| rotate["Rotation: balanced choice<br>from the reserve"]
    rot -->|no| q{"The best reserve node is<br>of a better tier or rated higher<br>by the margin?"}
    q -->|no| keep["Keep the active node"]
    q -->|yes| cd{"Cooldown passed?"}
    cd -->|no| keep
    cd -->|yes| conf{"Advantage confirmed by<br>confirm_cycles measurements?"}
    conf -->|no| count["Count +1, keep for now"]
    conf -->|yes| quality["Change by quality"]
\`\`\`

The pool for the choice is the group [reserve](groups.md#reserve) without nodes of the active host and of penalised
hosts. If the reserve is empty in an emergency, any candidate of another host is taken, and if there is none, any
other candidate.

<a id="reasons"></a>
## Reasons of switches

| Reason | In the history | When |
|---|---|---|
| \`init\` | First choice | the group has no active node yet (tester start, a new group) |
| \`emergency\` | Emergency replacement | the active node failed the gate, the monitor found it dead, it was restricted or it lost a required check |
| \`emergency-stuck\` | Emergency: no replacement found | the active node is dead and there is no other candidate; recorded once per episode, no switch happens |
| \`quality\` | Change by quality | a better node held its advantage for \`confirm_cycles\` measurements |
| \`rotation\` | Scheduled rotation | the rotation deadline came |
| \`manual\` | Manual choice | an administrator chose the node in its card |
| \`failsafe\` | No candidates: fallback outbound | the group lost all candidates and went to its fallback outbound |

<a id="emergency"></a>
## Emergency replacement

An emergency happens when the active node fails the gate, the monitor finds it dead, the node is paused, quarantined or
excluded, or it no longer passes a required check of the group (the exit country changed, an AI service started
refusing, the result expired). The replacement is immediate, without cooldown and confirmation.

- The new node is taken from another **provider** if possible: blocks often hit a whole provider or subnet.
- The choice is weighted and [balanced](#balance), not just the top-rated node.
- If the monitor removed the node, its host is penalised for \`selection.host_penalty\` (3 h): neither the reserve
  nor a replacement takes nodes of that host.
- Connections left on the failed node are closed (also when the group goes to its fallback outbound), and applications
  reopen them through the new one. A scheduled rotation does not close them if \`selector.interrupt_exist_connections\`
  is off.
- If there is no replacement, the group stays on the dead node and the history gets one “Emergency: no replacement
  found” record. As soon as a candidate appears, the group moves to it.

<a id="quality"></a>
## Change by quality

A group moves to a better node only when the advantage is real and stable:

- the best reserve node must be of a better tier, or rated higher than the active node by
  \`selection.quality_margin\` (15 points);
- if the active node is rated below \`selection.comfort_floor\` (50), any higher rating counts — a weak active node is
  replaced sooner;
- the advantage must hold for \`selection.confirm_cycles\` (2) measurements in a row of the active node or of the
  reserve leader;
- at least \`selection.cooldown\` (5 minutes) must pass since the previous switch of the group.

<a id="rotation"></a>
## Rotation

Rotation changes the active node on a schedule even when it works, so that traffic is spread over several nodes
and providers and a single node does not attract attention.

- Each group has its own deadline: \`rotation.interval\` (6 h) ± \`rotation.jitter\` (15 min) from its last switch.
- Scheduled rotations of different groups are at least \`rotation.spread\` (10 min) apart: a group whose deadline
  came waits.
- A node stays active at least \`rotation.min_dwell\` (30 min).
- The new node comes from the reserve, of the same or a better tier, rated at least \`rotation.min_score\` (55),
  and is not one of the last \`rotation.avoid_recent\` (2) active nodes.
- If rotation did not happen (one candidate, an API failure), the deadline moves forward the same way.
- \`selection.rotation.enabled: false\` turns scheduled rotation off; emergency and quality switches keep working.

<a id="balance"></a>
### Balanced choice

Rotation and emergencies choose randomly with weights rather than taking the top node:

- the base weight is the rating divided by (1 + how many times the node was already active);
- with \`rotation.balance\` on, the weight is also divided by the number of nodes of the same provider, country and
  protocol in the pool, so a provider with 100 nodes gets as much chance as a provider with 10;
- and multiplied by an “underuse” factor: providers, countries and protocols that carried less traffic in the last
  \`balance.window\` (24 h) get more weight.

The strength of each axis is \`balance.provider\` (1.0), \`balance.country\` (1.0) and \`balance.protocol\` (0.3); 0 turns
an axis off.

<a id="manual"></a>
## Manual choice

The “Select in group” action in a [node card](node-card.md#actions) makes the node active now. It is available only
for groups where the node is a candidate. Automation keeps working: an emergency, quality or rotation may change the
choice later.

<a id="monitor"></a>
## Monitor

Between measurements (which may be hours apart) the monitor watches every active node every \`monitor.interval\` (10 s).

\`\`\`mermaid
flowchart TD
    tick["Monitor tick"] --> rule{"Connection errors through the group:<br>≥ 3 in a minute and ≥ 2 in the minute<br>before (first 10 min of a node: ≥ 2)?"}
    rule -->|yes| emergency
    rule -->|no| err{"Burst of errors within<br>a tick (error_burst)?"}
    err -->|yes| probe["Probe: download probe.size_mb<br>through the node"]
    err -->|no| traffic{"User traffic through<br>the node in the last<br>silence period?"}
    traffic -->|yes| alive["The node is alive"]
    traffic -->|no| used{"Were there connections<br>through the node?"}
    used -->|no| alive
    used -->|yes| probe
    probe --> ok{"Probe passed<br>(speed ≥ probe.min_mbps)?"}
    ok -->|yes| alive
    ok -->|no| fails{"Failures in a row<br>≥ monitor.fails?"}
    fails -->|no| retry["Retry in monitor.retry (30 s)"]
    fails -->|yes| emergency["Emergency: replace the node,<br>penalise its host"]
\`\`\`

- Connection errors of users decide first: for a proxy node such an error is the node itself (cannot connect,
  no answer, connection broken). A single-connection probe often passes through a node that drops part of its
  connections, so the rule replaces the node without a probe (\`monitor.errors\`). Errors of connections started
  before the switch belong to the previous node and are not counted.
- Live user traffic is the best proof: while it flows, no probes are made. A group nobody uses is not probed.
- Connection errors are read from the sing-box log file (\`log.output\` of the running config). Without a log file
  this signal does not work.
- The monitor also watches \`failsafe-out\` of a group that has no candidates, and brings back the selector choice if
  someone changed it behind the tester's back (\`monitor.reassert_drift\`).
- **General outage** (\`monitor.outage\`): if the direct exit past sing-box does not answer, or after 3 emergency
  replacements within 10 minutes nodes of different providers are all silent, the failure is not a node.
  Measurements, the reserve, rotations and replacements stop until the checks pass 3 times in a row, so nodes
  are not punished for someone else's failure.

<a id="rebuild"></a>
## Rebuild on degradation

Sometimes a group cannot be fixed by switching: all nodes of the configuration are dead, and the provider has already
issued new accesses that are not in sing-box yet. Then the tester starts the router pipeline by itself.

\`\`\`mermaid
flowchart TD
    t["Every monitor tick"] --> d{"A group without candidates<br>longer than degraded_for (15 min)<br>or with fewer than reserve + 1<br>longer than few_candidates_for (1 h)?"}
    d -->|no| none["Nothing to do"]
    d -->|yes| fresh{"The last subscription poll has<br>new accesses, and ≥ gone_share<br>of the dead nodes are gone<br>from the subscriptions?"}
    fresh -->|no| none
    fresh -->|yes| limit{"The router build is enabled and<br>min_interval (6 h) passed<br>since the last router build?"}
    limit -->|no| none
    limit -->|yes| run["Run the router pipeline:<br>“rebuild on degradation”"]
\`\`\`

A rebuild restarts sing-box and drops connections, so it is done rarely and only when it will help. Fresh subscription
data comes from the separate poll \`pipeline.fetch.every\`; with \`0s\` the rebuild does not trigger. Each rebuild goes to
the log and to the \`pipeline\` notification. Settings: \`pipeline.router.rebuild\` ([reference](../CONFIG.md#pipeline)).

<a id="see-also"></a>
## See also

- [“Switches” page](switches.md): the history of choices.
- [\`selection\` and \`monitor\` sections](../CONFIG.md#selection) of the reference.
`;export{e as default};
