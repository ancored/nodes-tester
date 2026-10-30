**English** | [Русский](SECURITY_MODEL.ru.md)

# Security model of a router with sing-box

[About the project](../README.md) · [Config assembly](CONFIG_ASSEMBLY.md) · [sing-box compatibility](SING_BOX_COMPATIBILITY.md) · [Admin panel](USAGE.md)

This document describes the setup nodes-tester is written for: a home OpenWrt router with sing-box, through
which both local network devices and remote clients go out. nodes-tester does not create these
rules for the user: they live in the sing-box base and presets (see [config assembly](CONFIG_ASSEMBLY.md)).
The document explains what the setup protects against, which assumptions it relies on and what stays open.

The examples use `192.168.1.0/24` for the LAN, `eth0` for the LAN and `eth1` for the WAN. Put in your own values.

## Topology

```text
Clients with their own sing-box (TUN, own DNS, own zone rules)
   │  authenticated tunnel (naive, vless+reality)
   ▼
OpenWrt router + sing-box (TUN, auto_route/auto_redirect)  ◄── LAN devices without a client
   ├── foreign nodes from subscriptions: {group}-auto-out → node
   └── direct-out (WAN) and direct-lan (LAN)
```

- Each hop resolves names itself (TUN + `hijack-dns`, no FakeIP): the DNS query of an application
  is captured and the engine resolves it by `dns.rules`; the application connects by IP, and the next hop gets
  the IP, not the name. The route is chosen by the domain the engine learns from the connection itself (`sniff`: TLS,
  QUIC, HTTP) or, for traffic without one (calls, games, other UDP), from its own DNS answers: `dns.reverse_mapping`
  in the `core` preset keeps the “IP — domain” pairs for the answer TTL. Without it such traffic is routed by IP
  only, and domain rules do not apply to it. The pairs live in memory (up to 1024), and a shared CDN address maps to
  the domain resolved last. `action: resolve` and `domain_strategy` are not needed for this. DNS servers have `detour` set, so
  the query exits where the zone traffic goes, and the answer matches the exit country.
- The exception is the tester: it goes through SOCKS (`socks5h`) and passes the host name, which the already
  chosen node resolves. So the measurement reflects what a user of this node gets, including its DNS and geo.
- The domains of the nodes and DNS servers themselves are resolved through a separate `bootstrap` (DoH to an IP address).
- `route.final` is a foreign group (`global-auto-out`); the direct outbound is used only by explicit rules.

## Trust model

The starting principle: foreign nodes are an expendable resource. Their addresses “burn” if they are exposed
to blocking systems, so access to them is given only to trusted sources, and everything
unknown goes directly.

| Traffic source | Trust | Where it goes |
|---|---|---|
| Authenticated inbound (`naive-in`, `vless-in`, by `auth_user`) | trusted | to the nodes; the user sets the zone (`auth_user` → group) |
| LAN from the trusted segment (static DHCP addresses, e.g. `192.168.1.0/25`) | trusted | by zone rules, `final` is the nodes |
| Other LAN (dynamic pool, e.g. `192.168.1.128/25`) | untrusted | `direct-out` |

- **Authentication is a strong boundary**, a LAN address is a weak one. The trusted segment is for devices
  that need the nodes but cannot authenticate (a TV, a PC without a client).
  Phones and laptops with their own sing-box stay in the untrusted pool: they go out through their own
  tunnel, and their “bare” traffic (for example, of an isolated Android profile) goes directly.
- **The safe default for an unknown LAN device is the direct outbound.** A guest or
  a new device does not get access to the paid nodes and does not expose their addresses.
- The untrusted LAN rule is in the routes and mirrored in DNS; otherwise the DNS of an untrusted device
  would go through a node:

```json
{
  "type": "logical", "mode": "and",
  "rules": [
    { "inbound": "tun-in" },
    { "source_ip_cidr": "192.168.1.0/25", "invert": true }
  ],
  "outbound": "direct-out"
}
```

  In `dns.rules` it is the same condition with `"server": "<direct DNS>"`.
- **AI services never get the direct outbound.** It shows the home IP, so right above the untrusted LAN rule
  the same condition plus the AI sets (Claude, OpenAI, Grok, Perplexity domains and the Anthropic addresses
  `160.79.104.0/21`) → `reject`, in DNS → `NXDOMAIN` (the `untrusted-lan` preset). The router's own connections
  match it too: their source is the WAN address, outside the trusted segment. The tester is not affected: it
  enters through its own SOCKS inbound, whose rule stands higher. Google is left out on purpose: an isolated
  profile with its own Google account uses it directly.
- Trust by `source_ip` depends on static address assignment and can be spoofed in the LAN; a MAC↔IP
  binding on the firewall is stricter. sing-box 1.14 has `source_mac_address`; switching to it removes
  the dependency on DHCP, but a MAC in the LAN can be spoofed too.
- Rules that must apply only to trusted sources (for example, a check address like
  `ifconfig.me` through a separate outbound) go **after** the untrusted LAN rule, otherwise they
  bypass the model.
- IPv6 in the LAN without capture is off: the trust rules are written for IPv4, and `strategy: ipv4_only`
  limits only DNS answers, not the transport.
- Nodes of subscriptions meant only for clients (for example, public whitelist nodes) are in the router config
  only in the tester selector `nodes-tester`: the tester measures them, router groups and `failsafe-out` do not take
  them, so router traffic never goes through such nodes. The clients get only the nodes that passed the checks of
  their groups on the router.

## Kill switch

While sing-box runs, the trusted LAN traffic is captured before forwarding (TCP by redirect, UDP by
TUN). When sing-box stops, restarts or crashes, it removes its rules, and the same traffic would go
directly through the provider with the real IP. The kill switch closes this window: a separate
nftables table, loaded before the network, forbids forwarding the trusted segment and all IPv6 from the LAN to the WAN.

`/etc/killswitch.nft`:

```nft
table inet killswitch
delete table inet killswitch
table inet killswitch {
	chain forward_guard {
		type filter hook forward priority filter - 1; policy accept;
		iifname "eth0" oifname "eth1" ip saddr 192.168.1.0/25 limit rate 6/minute burst 5 packets log prefix "killswitch: " level info
		iifname "eth0" oifname "eth1" ip saddr 192.168.1.0/25 meta l4proto tcp counter reject with tcp reset
		iifname "eth0" oifname "eth1" ip saddr 192.168.1.0/25 counter reject with icmpx type admin-prohibited
		iifname "eth0" oifname "eth1" meta nfproto ipv6 counter reject with icmpx type admin-prohibited
	}
}
```

`/etc/init.d/killswitch`:

```sh
#!/bin/sh /etc/rc.common
# Starts before the network (network=20) and sing-box (99), so there is no window at boot.
START=18
STOP=91

start() {
	nft -f /etc/killswitch.nft
}

stop() {
	nft delete table inet killswitch 2>/dev/null
	return 0
}
```

```sh
chmod +x /etc/init.d/killswitch
/etc/init.d/killswitch enable
/etc/init.d/killswitch start
```

- The table is its own: fw4 rewrites only `inet fw4`, sing-box only `inet sing-box`.
- `reject`, not `drop`: applications see the refusal right away instead of waiting for a timeout.
- The untrusted segment is not covered by the rule: by the model it goes directly anyway.
- Emergency removal: `/etc/init.d/killswitch stop`. The admin panel shows whether the table is loaded and
  how many packets were blocked, and turns the service on or off (see [`nodes_tester`](../nodes_tester/README.md#sing-box-stop-and-killswitch)).
- A non-zero counter while sing-box is running is a reason to check whether some traffic bypasses
  the capture. Small numbers are normal: the tails of TCP connections after a restart and UDP “full cone”.
  When a packet from a new peer comes to a device over a UDP flow, sing-box writes it into the TUN, and the
  conntrack entry of that pair has no capture mark: the replies of the device go straight to the WAN, past
  sing-box. For the trusted segment the kill switch rejects them (DHT, WebRTC), so it works in normal
  operation too, not only while sing-box restarts.
- Remote clients are protected on their own devices: on Android with “Always-on VPN” and “Block
  connections without VPN”.

## Threats and responses

The setup is meant for a network with DPI and blocking on the provider side (TSPU in Russia).

The danger is rated by the consequences for the user, not by probability:

| Danger | Consequences |
|---|---|
| very high | account ban; the real IP is visible to a service that must not see it; loss of access to the needed services or the internet; compromise of the router, control or secrets |
| medium | financial losses; signs of VPN use; worse privacy; unstable work or temporary degradation of some routes |
| low | loss of a single node or node source when there is a reserve: the system survives it on its own |

| Threat | Danger | Response |
|---|---|---|
| Apps of Russian services compare their visible IP with the IP of other requests and report a VPN | medium | on clients such apps go `direct` entirely (a rule by `package_name` above the node rules): all their requests come from one IP, there is no mismatch |
| Apps collect the list of installed programs, including VPN clients | medium | on Android such apps are installed into an isolated profile (Knox, work profile) without a VPN client; the VPN of the main profile does not cover it, the traffic reaches the router “bare” from the untrusted pool and goes `direct`: this is expected, not a leak |
| An app excluded from the VPN (per-app proxy or an isolated profile) binds its socket to `tun0` (`SO_BINDTODEVICE`) and goes out through the tunnel; Android does not tell sing-box the owner (`INVALID_UID`), the `package_name` rule does not match, and the traffic would go to `final` with the node IP | medium; very high if the traffic must not leave with the real IP | the first client rule sends a connection without a determined package → `direct-out`: `{"package_name_regex": [".*"], "invert": true, "outbound": "direct-out"}`. Not `reject`: it does not hide the fact of a VPN; what matters is not to show the node IP and an address mismatch. The same connections to AI services → `reject` (`sniff` stands before these rules): they must not see the direct IP either. It needs a client that determines the package of connections, otherwise all traffic goes directly; enable it after checking on the particular device. The “Block connections without VPN” mode takes the network away from excluded apps entirely |
| The client tunnel went down, and traffic to a blocked or geo-dependent service went directly with the real IP | very high | on Android, “Always-on VPN” and “Block connections without VPN”; on a laptop, a local kill switch with the firewall (not implemented yet, see below) |
| A service blocks or limits access by the exit country (AI services, Google) | very high | a separate node group checked “through the eyes of the service” (Google, OpenAI, Claude), without a mismatch of the exit IP country by the database and with priority for residential and mobile IPs, AI zone presets; see [`nodes_tester`](../nodes_tester/README.md) and the presets in [`config/singbox/presets`](../config/singbox/presets) |
| A whole protocol or transport is blocked | medium | nodes of different protocols and providers in shared groups; clients enter the router through two inbounds with different transports (naive, vless+reality) |
| Separate billing of foreign traffic by the client's provider | medium | the client sends into the tunnel only what needs a proxy, and the tunnel goes to the home (Russian) IP; the router takes the traffic out to a foreign node |
| The client entry point is blocked by traffic symmetry: an intermediate multi-hop VPN node in Russia sends out as much as it receives | very high | clients enter their own home router, not an intermediate node: their traffic is mixed with the regular home traffic, so the incoming and outgoing volumes do not match |
| QUIC is not sniffed, the domain is not detected, the flow goes to `final` past the zone | medium; very high for a sensitive zone | UDP/443 to the domains and IP sets of the zone → `reject`, the client falls back to TCP. OpenAI lives on Cloudflare addresses, and a flow without a domain cannot be told from other Cloudflare sites: UDP/443 to all Cloudflare ranges → `reject`, otherwise the service sees TCP from one exit and QUIC from another at the same time |
| ECH hides the real domain: `sniff` sees only the outer name (`cloudflare-ech.com`), domain rules do not match, and the flow goes by IP past its zone or directly | very high | DNS does not return HTTPS records, where the ECH keys come from, and connections with the outer name `cloudflare-ech.com` → `reject` (the `block-ech` preset); a browser with its own DoH can still get the keys |
| A subscription gives a node whose server is the router itself (`0.0.0.0`, loopback) | low | `nodes_config` drops such nodes; private addresses stay: the connections of nodes go to the WAN, and such a node just does not answer |
| Sites are unavailable from foreign IPs (Russian, Belarusian) | very high | RU and BY `geosite`/`geoip` → `direct-out` |
| Torrents, RDP, video calls through nodes: load, complaints to the node provider, latency | medium | `protocol: bittorrent/rdp`, tracker and video call sets → `direct-out` |
| Ads, trackers, adult content | medium | sets → `reject` in routes and `NXDOMAIN` in DNS |
| The provider blocks nodes, throttles frequent checks | low | the tester visits nodes by host in a mixed order, keeps a quarantine and limits heavy checks; see [`nodes_tester`](../nodes_tester/README.md) |
| A node is alive but degrades (latency, loss, throttling), or switches happen too often | medium | measurement history, rating, quarantine; rotation not more often than `min_dwell` |
| A subscription went down or returned garbage | low; medium without reserve sources | the last-good nodes of a source are kept for `stale_max_hours` (48 h), the guard compares the total number of nodes with the previous build; see [`nodes_fetch`](../nodes_fetch/README.md) |
| A whole node supplier fails | low | several providers in shared groups, choice from the combined pool |
| A bad config breaks the internet of the whole home | very high | the pipeline checks the candidate with `sing-box check`, applies it with a connectivity check and rollback |

Research the app-side and regulatory threats are taken from:
[RKS Global — VPN Detection](https://rks.global/ru/research/vpn-detection/),
[an audit of Russian Android apps](https://habr.com/ru/amp/publications/1081580/),
[an analysis of MAX](https://habr.com/ru/articles/1006666/),
[billing of foreign traffic (The Moscow Times, 23.09.2026)](https://ru.themoscowtimes.com/2026/09/23/posle-viborov-v-dumu-vlasti-poruchili-vvesti-dlya-rossiyan-platu-za-vihod-v-mirovoi-internet-a206769).

## What the setup does not cover

- **A flat network.** On an unmanaged switch all LAN devices are in one L2 segment, traffic between
  them bypasses the router. An untrusted device (for example, a work laptop) can be isolated only
  with 802.1Q VLANs on a managed switch, a separate interface and an fw4 zone on the router plus a rule in
  sing-box: `auto_redirect` proxies connections on behalf of the router, so the fw4 forward rules do not
  see them.
- **IPv6.** The kill switch forbids all IPv6 forwarding from the LAN. The `inet sing-box` table cuts IPv6 in
  output/prerouting, so IPv6 on the WAN needs NDP and DHCPv6 exceptions. Handing out IPv6 in the LAN without
  capture is a leak path past the tunnel.
- **Application DNS.** Browsers and operating systems with their own DoH bypass the sing-box DNS engine: the name is resolved
  by an external server, the route is chosen by IP only. It is closed by a local DoH server that
  wraps such queries into sing-box, or by forbidding DoH in the browser policies.
- **Resolving node domains.** `bootstrap` over DoH hides the queries from the provider; the router system
  resolver (dnsmasq → provider DNS) sees only what the router resolves itself.
- **Trust by address.** A device that manually takes an address of the trusted segment gets its rights.
- **Admin panel and API.** The administrator token and the API service secret give full control over the exit.
  Do not expose them to the internet; access only from the LAN or through a tunnel. The MCP endpoint `/mcp`
  of the admin panel takes the same token and can do everything the administrator can, including stopping
  sing-box: give the token only to an MCP client you trust, and prefer one that asks before actions marked
  `destructiveHint`. Tool results contain secrets (configs, the node JSON) and go to the client's model.
- **Secrets in configs.** The base, client configs and subscriptions contain passwords, UUIDs, Reality keys,
  secret distribution paths. They must be replaced before publishing fragments (bug reports, issues).
- **Amnezia Premium.** The `vpn://` link holds the subscription `api_key`, the country files hold AWG private
  keys; the tester writes the files with rights 0600. The gateway is reached over plain HTTP, and the request is
  protected only by the gateway RSA key: take it only from the official AmneziaVPN app, a substituted key
  gives the `api_key` to whoever is on the path.
- **Client config distribution** by secret tokens is protected only by the token length and TLS; a leaked
  link gives the full client config. The tokens live in `config.json` (`clients`); a new token or a disabled client
  revokes the old link at the next client build. Keep the web server access log off: it would collect the tokens.
- **Laptop kill switch.** The Windows counterpart of the Android “Block connections without VPN” is
  [`scripts/windows/killswitch.ps1`](../scripts/windows/killswitch.ps1) (`on`/`off`/`status`, as administrator):
  the outgoing firewall policy is Block, allowed are the sing-box core (in the GUI, the `sing-box-daemon` service), the TUN adapter,
  DHCP and the local subnet except DNS; other allowing outgoing rules are disabled for the time being, `off`
  brings them back. While the core is not running there is no network; a guest Wi-Fi login page opens only after `off`.

## Core and operations

- **XHTTP and AmneziaWG** exist only in [Leadaxe/sing-box-lx](https://github.com/Leadaxe/sing-box-lx);
  the official sing-box does not have them. See [compatibility](SING_BOX_COMPATIBILITY.md).
- **A deadlock when switching a nested selector** with `interrupt_exist_connections: true`
  ([Leadaxe/sing-box-lx#20](https://github.com/Leadaxe/sing-box-lx/issues/20)): all new connections and
  DNS hung until a restart. Fixed in `v1.14.0-lx.38`. On older builds set
  `interrupt_exist_connections: false` in `pipeline.router.selector` of `config.json`.
- **A naive inbound panic on UDP-over-TCP over HTTP/2** (`Write called after Handler finished`) brought
  the whole process down ([SagerNet/sing-box#4249](https://github.com/SagerNet/sing-box/issues/4249)). Fixed
  in [SagerNet/sing-box#4287](https://github.com/SagerNet/sing-box/pull/4287), present in `1.14.1-lx.8` and
  newer. On older cores use `"network": "tcp"` on the naive inbound.
- **A `bootstrap` of type `local` under TUN + `hijack-dns`** gives timeouts (a loop through its own capture).
  Use DoH to an IP address.
- **A DNS loop in the connectivity check:** the check domain (`www.gstatic.com`) must be resolved through
  `bootstrap` by the first DNS rule, otherwise the check does not work when the group is dead either.
- **Outbound interface:** with one WAN an explicit `route.default_interface` (WAN) is more reliable than
  `auto_detect_interface`. The direct exit to the LAN is a separate `direct-lan` with `bind_interface` on the LAN.
  Revisit it with PPPoE, a backup WAN or when IPv6 is enabled.
- **The sing-box binary** runs as root and must be owned by root (in the release archive the owner is
  `uid 1001`).
- **Updating the core without losing connectivity:** download the release and `SHA256SUMS`, verify the sum, check the
  build tags (`with_xhttp`, `with_awg` and others in `sing-box version`), run `sing-box check` of the working
  config with the new binary, keep the previous binary, replace it atomically (`mv`), restart and on
  failure bring the previous one back. The only reliable sign that it works is real traffic through the
  tunnel: ports and the API respond even with dead routing. For risky replacements use a watchdog
  `setsid sh -c 'sleep N; [ -f /tmp/ok ] || rollback'`, armed before the replacement.
- **Order when upgrading:** first update nodes-tester and apply the config, then the core: the core
  update script checks the working config.
