**English** | [Русский](runs.ru.md)

# Checks

This page shows what the tester is doing and its log, and starts testing of all nodes on demand. It needs the administrator token
and the embedded admin panel (the standalone admin panel has no live tester).

<a id="status"></a>
## Checks of the current sing-box nodes

| Line | Meaning |
|---|---|
| Stage | “Checking a node”, “Waiting for test deadlines”, “Waiting for the config to be applied”, “Preparing”, “Stopped” |
| Current node | the node being checked |
| On-demand testing | how many nodes are left, while it runs |
| Current wait until | when the next result expires; the deadline may move when new nodes appear |
| Next rotation | the nearest [rotation](switching.md#rotation) deadline over all groups |
| Active node monitor · traffic collection | whether the [monitor](switching.md#monitor) and the traffic accounting threads run |

The tester checks only nodes whose results have expired ([lifetimes and the queue](checks.md#lifetimes)). The status is updated
every 5 seconds.

<a id="run-now"></a>
### Run testing now

The button queues all nodes available to the tester without waiting for their deadlines: useful after changing tests or thresholds,
or to see the state of all nodes now. It does not fetch subscriptions, does not build or apply a configuration. While on-demand
testing runs, the button is unavailable. A full round over many nodes takes time: each node takes several seconds, and nodes of one
host are spaced by `tester.host_gap`.

<a id="log"></a>
## Tester log

The latest lines of the tester log, updated every 2 seconds; up to 1500 lines are kept on the page, the server keeps a limited buffer,
so after a long pause old lines may drop out. The full log is on the router: `logread -e nodes-tester`.

- **Search** filters lines by text.
- **Lines**: all, errors or switches. The type is guessed by the text of the line; it is not a structured log level.
- **Pause reading** stops fetching new lines (for example, to read a long error); **Follow new lines** scrolls to the end.

What the main lines mean:

| Line contains | Meaning |
|---|---|
| `[switch] <group>: EMERGENCY / QUALITY / ROTATION → <node>` | a [switch](switching.md#reasons) |
| `кандидатов нет … группа на failsafe-out` | the group lost its candidates ([fallback](groups.md#fallback)) |
| `gate-провал … пауза #n` / `карантин` | a gate failure and the resulting [restriction](restrictions.md) |
| `[reserve]` | the reserve process checks contenders |
| `[monitor]` | probes of the active nodes |
| `[rebuild]` | the tester starts the router pipeline itself ([rebuild on degradation](switching.md#rebuild)) |
| `[amnezia]` | the [Amnezia Premium](subscriptions.md#amnezia) country check |

The log of the tester is in Russian for now.
