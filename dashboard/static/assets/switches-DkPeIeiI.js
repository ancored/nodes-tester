const e=`**English** | [Русский](switches.ru.md)

# Switches

The history of how the active node of each group was chosen. How the switcher decides is explained in [Switching](switching.md).

<a id="active"></a>
## Active nodes by group

The node currently selected in each group by the running tester. If the tester is not connected (standalone admin panel), this block is
unavailable, but the history below is still shown.

<a id="history"></a>
## History

The last 200 records over all groups, newest first.

| Column | Meaning |
|---|---|
| Date and time | when the switch happened |
| Group | the group |
| Selected node | the node that became active (or the fallback outbound) |
| Previous node | the node before it; the link opens its card (the full tag is shown while the node is in the list or the archive) |
| Reason | why (see below); the icon helps to tell them apart |
| Active node | “● Active” — the latest record of the node that is active in the group now; its row is green |

| Reason | Meaning |
|---|---|
| First choice | the group had no active node (tester start, a new group) |
| Emergency replacement | the active node died, was restricted or lost a required check |
| Emergency: no replacement found | the active node is dead and there is nothing to switch to; no switch happened |
| Change by quality | a better node held its advantage |
| Scheduled rotation | the rotation deadline came |
| Manual choice | an administrator chose the node |
| No candidates: fallback outbound | the group went to \`failsafe-out\`, \`block-out\` or \`direct-out\` |

The filters select a group and a reason. The last record of a group does not always show the current active node: an emergency
without a replacement is not a switch. Frequent emergencies in one group mean its nodes are being blocked: look at the
[provider quality](nodes.md#provider-quality) and the [restrictions](nodes.md#paused).

Which switches are sent as notifications is set in [Settings → Notifications](settings.md#notifications): by default only
emergencies.
`;export{e as default};
