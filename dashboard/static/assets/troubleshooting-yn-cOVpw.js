const e=`**English** | [Русский](troubleshooting.ru.md)

# Troubleshooting

Start from the symptom, then check the listed places. The tester log on the router: \`logread -e nodes-tester\`; the pipeline logs are on
the [“Pipeline”](pipeline.md#log) page.

<a id="admin"></a>
## Admin panel

| Symptom | What to check |
|---|---|
| The admin panel does not open | the service \`/etc/init.d/nodes-tester status\`, \`admin.enabled\`, the address and port (\`admin.listen\`, \`admin.port\`), access from the LAN, \`logread -e nodes-tester\` |
| The token is not accepted | the token of the running config (\`admin.token\`); after changing it the tester must restart |
| “No fresh data”, old snapshot | the server does not answer: the network, the service; a stale snapshot does not show the current state |
| The database is missing or off | the \`storage.path\` path; whether the tester has started and created it |
| Settings, pipeline and files are unavailable | log in; the standalone admin panel has no pipeline and no live tester |
| The page in the wrong language | the “Language” list at the bottom of the menu |

<a id="nodes"></a>
## Nodes and checks

| Symptom | What to check |
|---|---|
| No measurements at all | the [Checks](runs.md) page: the stage and the log; \`api.url\` and \`api.secret\`; the \`nodes-tester\` selector and the SOCKS inbound \`tester.socks\` in the sing-box config |
| New nodes do not appear | whether the subscriptions were fetched and the router pipeline applied ([how changes reach sing-box](subscriptions.md#apply)); the [fetch state](subscriptions.md#state) |
| All nodes of a provider have rating 0 | the provider is blocked in your network or the subscription is dead; [provider quality](nodes.md#provider-quality) |
| The node shows a different country than in its name | normal: the country is measured ([exit country](checks.md#exit-country)) |
| AI checks show “?” | network failures during the check; the previous verdict stays. Only candidates of groups that require AI are checked |
| A node is stuck in a pause or quarantine | the [restriction rules](restrictions.md); “Lift restriction” in the card queues a check |

<a id="groups"></a>
## Groups and switching

| Symptom | What to check |
|---|---|
| A group is on \`failsafe-out\` / \`block-out\` | no candidates: the [Groups tab](node-card.md#groups) of a few nodes shows which check fails; the requirements may be too strict for your nodes |
| A strict group blocks traffic | it has no candidates and no \`direct_fallback\`; that is the intended protection ([fallback outbounds](groups.md#fallback)) |
| Frequent emergency switches | nodes are being blocked; look at the [switch history](switches.md) and [restrictions](nodes.md#paused); a rebuild may help ([rebuild on degradation](switching.md#rebuild)) |
| “Select in group” is unavailable | the node is not a candidate of any group; the token; the embedded tester |
| The group stays on one node for hours | rotation is off or the reserve is empty: [rotation](switching.md#rotation), [reserve](groups.md#reserve) |

<a id="pipeline"></a>
## Pipeline and sing-box

| Symptom | What to check |
|---|---|
| “busy”, code 75 | another run was in progress, possibly from cron; see the history; remove the cron line if the built-in schedule is on |
| \`timeout\` | the last stage in the log; the \`pipeline.timeout\` limit; a slow subscription (\`pipeline.fetch.timeout\`) |
| \`sing-box check\` error | the base, the presets and the protocol compatibility with the installed core: XHTTP and AWG need \`sing-box-lx\` ([compatibility](../SING_BOX_COMPATIBILITY.md)). The working configuration was not replaced |
| Connectivity lost after applying | the log of the connectivity check and the rollback: the pipeline returns the previous configuration if a request through the working group fails |
| A preset cannot be enabled | the reason is shown on the [Rules](rules.md#list) page; often a required group is missing in \`nodes.json\` |
| The network is broken and the admin panel is reachable | [stop sing-box](overview.md#singbox) from the Overview; with the killswitch loaded, the protected devices stay without internet until sing-box starts |

<a id="update"></a>
## Updating and rolling back

Before updating the package make a snapshot: \`nodes-tester backup --stable\`. To go back: check the snapshot with
\`nodes-tester rollback --check\`, then \`nodes-tester rollback\`. Details: [updating the package](../../openwrt/README.md#6-update-the-package).

<a id="report"></a>
## Reporting a problem

In [Issues](https://github.com/ancored/nodes-tester/issues) give the OpenWrt, package and sing-box versions, what you did and a short
error. Remove subscription URLs, keys, tokens and private addresses first.
`;export{e as default};
