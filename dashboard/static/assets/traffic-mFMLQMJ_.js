const e=`**English** | [Русский](traffic.ru.md)

# Traffic

How much LAN users sent and received through the nodes, by provider, country, protocol and node, and the most used destinations.
The tester counts traffic exactly from the stream of sing-box connections (\`storage.traffic\`); its own test traffic is counted
separately.

<a id="period"></a>
## Period

All figures are for the whole stored period (shown at the top), **not for the last 24 hours**. The history of nodes that disappeared
is deleted \`storage.retention\` (30 days) after they were last seen, so the period is at most about that long.

<a id="user"></a>
## User traffic

Incoming, outgoing and total traffic of LAN users through the nodes over the period. Test traffic is excluded.

<a id="tester"></a>
## Tester traffic

All traffic of the tester connections over its stored period, and separately the bytes downloaded by the \`download\` and \`heavy\`
measurements in the last 24 hours (response bodies only). Use it to estimate how much the checks cost on a metered connection: the
size of the downloads is set in the \`tests\` and \`checks\` sections.

<a id="dimensions"></a>
## User traffic by dimension

The traffic grouped by one dimension — providers, countries, protocols or nodes — with outgoing, incoming and total bytes. The filters
(country, group, protocol, provider) apply before grouping, so you can see, for example, the traffic of one provider by country.
In the “nodes” dimension direct traffic (\`direct-out\`) is also shown.

<a id="destinations"></a>
## Top destinations

The 50 destinations with the most traffic: provider, source address in the LAN, destination host, transport (TCP/UDP), outgoing and
incoming bytes, number of connections. These are cumulative counters, not a sample of the period.

Traffic data is also used by [balancing](switching.md#balance) of rotation: providers, countries and protocols that carried less
traffic get more chance.
`;export{e as default};
