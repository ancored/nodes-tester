const e=`**English** | [Русский](overview.ru.md)

# Overview

The Overview is the first page: is the tester working, is sing-box running, how many nodes there are and which node each
group uses right now.

<a id="system-state"></a>
## System state

| Line | Meaning |
|---|---|
| Tester stage | what the tester is doing now: “Checking a node”, “Waiting for test deadlines” (all results are fresh), “Waiting for the config to be applied” (the pipeline is applying a configuration), “Preparing”, “Stopped” |
| Current node | the node being checked right now |
| Last measurement in the database | the time of the newest saved result; if it is old, the tester is not measuring |
| Settings and control | whether the administrator token is verified |

The tester checks only nodes whose results have expired ([lifetimes](checks.md#lifetimes)), so “Waiting for test deadlines” is
the normal state of a healthy system. When on-demand testing runs, the block shows how many nodes are left.

This page being available does not prove that sing-box forwards traffic: the admin panel is served by the tester process,
not by sing-box. Real connectivity is shown by the measurements of the nodes and by the [monitor](switching.md#monitor).

<a id="singbox"></a>
## sing-box

The block is shown on OpenWrt, where the admin panel can control the sing-box service.

**State**: “running”, “not running” or “stopped from the admin panel”.

**Stop sing-box** is an emergency exit when a new configuration or a core update broke the network and you need the internet
back now. What happens to LAN devices depends on the killswitch:

| Killswitch | While sing-box is stopped |
|---|---|
| rules loaded | the devices protected by the killswitch rules have no internet; the router itself, the admin panel and SSH stay available |
| not loaded or not installed | LAN traffic goes directly through the provider, without a proxy |

While sing-box is stopped from the admin panel, the pipeline does not apply a new configuration (the stop flag lives in memory).
A router reboot clears the flag and starts sing-box. **Start sing-box** starts it again.

**Killswitch** is a separate router service that does not let LAN devices reach the internet bypassing sing-box, for example
while sing-box restarts. Its rules (which devices are protected) are set in the service itself; nodes-tester does not create
them. The block shows whether the rules are loaded and how many packets were blocked. The checkbox turns the service on
(start and autostart) or off; rules that are already loaded stay in force until the service stops. The service and its setup
are described in the [security model](../SECURITY_MODEL.md#kill-switch).

Stops, starts and the killswitch changes can be sent as \`singbox\` [notifications](settings.md#notifications), as well as
sing-box being down longer than \`singbox.down_alert\`.

<a id="nodes"></a>
## Nodes: count and state

| Counter | What is counted |
|---|---|
| In the tester list | router nodes present in the current sing-box configuration |
| With rating measurements | of them, nodes that already have a rating |
| Group candidates | nodes that are a [candidate](groups.md#candidates) of at least one group |
| With restrictions | nodes in a pause, in quarantine or excluded |

The node list is on the [“Nodes”](nodes.md) page.

<a id="traffic"></a>
## Traffic over the stored period

- **User traffic**: incoming, outgoing and total traffic of LAN users through the nodes, over the whole stored period (shown
  above the counters), not over a day.
- **Downloaded by tests in 24 hours**: the bytes the \`download\` and \`heavy\` measurements received. These are response bodies
  only, without connection overhead and other tests.
- **Tester traffic**: all traffic of the tester connections over the stored period.

The user and tester counters come from exact connection accounting (\`storage.traffic\`). Details by provider, country and node
are on the [“Traffic”](traffic.md) page.

<a id="groups"></a>
## Groups

One line per group of \`config.json\`:

- the group tag and its kind: strict or non-strict, whether \`direct-out\` is allowed, whether it is outside \`global-auto-out\`;
- **Active** — the node selected now (click it to open the card), or “No candidates: the group is on …” with the
  [fallback outbound](groups.md#fallback); \`block-out\` and \`direct-out\` are highlighted as a problem;
- **Nodes / candidates / reserve** — how many router nodes the group has, how many of them are candidates and how many are
  checked in the reserve;
- **required / preferred** — the checks of the group.

This is the state of the switcher in the tester. The live selectors of sing-box are not read here separately; the monitor
returns them to the switcher choice if they drift. Groups are configured on the
[“Subscriptions and build”](subscriptions.md#groups) page, “Build and groups” tab. If the tester is not running (standalone
admin panel), the block is unavailable.
`;export{e as default};
