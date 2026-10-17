**English** | [Русский](README.ru.md)

# nodes-tester help

This is the full guide to nodes-tester: how it works inside and what every page and block of the admin panel
shows. The same text is built into the admin panel: the **?** icon next to a page title or in the corner of a
block opens the matching section in a side panel, and the “Help” menu item opens the whole help with contents and
search. It works without internet access.

<a id="start"></a>
## Where to start

If you are setting up nodes-tester for the first time, follow this order:

1. **Install the package** and connect sing-box: [installation on OpenWrt](../../openwrt/README.md).
2. **Log in** to the admin panel with the administrator token: [Access and interface](access.md#login).
3. **Add subscriptions** on the [“Subscriptions and build”](subscriptions.md) page, “Subscription sources” tab.
4. **Describe groups** on the same page, “Build and groups” tab: which countries, AI services and IP types each
   group needs ([Groups and candidates](groups.md)).
5. **Run the pipeline** on the [“Pipeline”](pipeline.md) page: first a check, then apply; enable the schedule.
6. **Enable the rule presets** you need on the [“Rules”](rules.md) page and apply them.
7. **Watch** the [“Overview”](overview.md): within minutes the tester measures the nodes and picks the active node
   of every group. If something goes wrong, see [Troubleshooting](troubleshooting.md).

<a id="how-it-works"></a>
## How it works

| Page | What it explains |
|---|---|
| [How nodes-tester works](concepts.md) | the pipeline and the tester, nodes and their keys, branches, groups, data files, glossary |
| [Tests and checks](checks.md) | what is measured, result lifetimes, the check queue, every check of groups |
| [Rating](rating.md) | how the 0–100 score is computed from measurements |
| [Groups and candidates](groups.md) | required and preferred checks, candidates, tiers, the reserve, fallback outbounds |
| [Switching](switching.md) | emergency, quality and scheduled switches, the monitor, rebuilding on degradation |
| [Pause, quarantine and exclusion](restrictions.md) | how failing nodes are restricted and brought back |

<a id="pages"></a>
## Admin panel pages

| Page | Help |
|---|---|
| Access, login, tables, language | [Access and interface](access.md) |
| Overview | [Overview](overview.md) |
| Nodes: all nodes, rating, results, restrictions and quality, archive | [Nodes](nodes.md) |
| Node card | [Node card](node-card.md) |
| Subscriptions and build | [Subscriptions and build](subscriptions.md) |
| Pipeline | [Pipeline](pipeline.md) |
| Rules | [Rules](rules.md) |
| sing-box files | [sing-box files](singbox-files.md) |
| Checks | [Checks](runs.md) |
| Switches | [Switches](switches.md) |
| Traffic | [Traffic](traffic.md) |
| Settings | [Settings](settings.md) |
| When something does not work | [Troubleshooting](troubleshooting.md) |

Every field of `config.json` with its type and default value is in the [config.json reference](../CONFIG.md).

<a id="conventions"></a>
## Conventions of this help

- `selection.reserve` is a field path in `config.json`: the `reserve` field of the `selection` section. Field
  names link to the [reference](../CONFIG.md).
- Durations are written as in sing-box: `30s`, `5m`, `3h`, `2d`.
- “Node” is one proxy server with its access credentials; “group” is a sing-box selector the tester controls.
  See the [glossary](concepts.md#glossary).
- Default values are given as they are in a new installation; your `config.json` may override them.
