**English** | [Русский](access.ru.md)

# Access and interface

This page explains what is common to all pages of the admin panel: access, data refresh, tables, the language and the
help itself.

<a id="modes"></a>
## Viewing and administration

The admin panel opens in **view mode**: all state, measurements and history are visible (if `admin.public_read` is on, the
default), but nothing can be changed. Settings, the pipeline, sing-box files and actions on nodes need the administrator
token.

| Without the token | With the token |
|---|---|
| Overview, nodes, rating, results, switches, traffic | everything in view mode |
| — | settings and subscriptions, the pipeline and its logs, rules and sing-box files |
| — | actions on nodes: exclude, quarantine, select in a group |
| — | the tester log, testing on demand, stopping sing-box, `VACUUM` |
| — | the node JSON in the card |

The pipeline history, its logs and the sing-box source files are never shown without the token: they may contain
subscription addresses.

<a id="login"></a>
## Logging in

Press “Log in to manage” in the header and enter the token from `admin.token` of `config.json` on the router (the package
installation creates it; over SSH: `grep token /etc/nodes-tester/config.json`). It is **not** the sing-box API secret.
The token is checked by the server; typing it alone gives no access.

- Without “Remember on this device” the token lives until the page is closed.
- With it, the token is stored in the browser (localStorage). Do not use it on someone else's computer.
- “Log out” deletes the stored token and closes the editors. If there are unsaved changes, the admin panel asks first.
- If the server has no token (`admin.token` is empty), editing and control are off; the login form says so.

Send the token only over a trusted network or HTTPS: the admin panel uses plain HTTP inside the LAN. Do not expose the
admin panel to the internet. See the [security model](../SECURITY_MODEL.md).

<a id="standalone"></a>
## Embedded and standalone admin panel

Usually the admin panel runs inside the tester process (embedded). It can also be started on its own
(`python3 -m dashboard`): then it reads the database and files and edits `config.json`, but has no live tester —
testing on demand, the log, live selector state and actions on nodes are unavailable. The admin panel shows a notice in
this mode.

<a id="refresh"></a>
## Data refresh

The data is a snapshot of the database: the server takes a new one every `admin.refresh` (10 s), and the page asks for it
every 15 seconds and on the “Refresh” button. The header shows the time of the snapshot.

- If the server does not answer, the header says “No fresh data”, and the page keeps showing the last snapshot with a
  warning and its time. A stale snapshot does not confirm the current state.
- If the database is unavailable (wrong `storage.path`, the tester has not created it yet), the page says so: empty lists
  then do not mean that there are no nodes.

<a id="tables"></a>
## Tables

All tables work the same way:

- **Search** looks for the text in every column of a row: a name, a provider, a country code, a part of the key.
- **Filters** are drop-down lists: country, reserve group, protocol, provider and the fields of the particular table
  (state, status, reason). “Reset filters” clears them.
- **Sorting**: click a column header (once — ascending, twice — descending, three times — the original order) or choose it in
  the “Sort” list.
- **Pages**: the arrows under the table.
- Search, filters, sorting and the page are kept in the page address: a link opens the same view, and auto-refresh does not
  reset it.

The node key in tables is shown in two lines and two colours: the first part (blue) is the checksum of the server settings, the
second (purple) of the credentials ([Nodes and their keys](concepts.md#nodes)). Click it to open the [node card](node-card.md).
The country is shown as a flag; hover to see the code. The protocol is split into parts, one per line.

<a id="language"></a>
## Language

The interface is in English and Russian. By default it follows the browser language; the choice in the “Language” list at the
bottom of the menu is remembered on the device. The help follows the same language. Messages that come from the server (some
errors, the tester and pipeline logs) are in Russian for now.

<a id="dashboard"></a>
## sing-box dashboard

The “sing-box dashboard” menu item opens the official panel of the sing-box API service in a new tab: live connections,
selectors, logs of sing-box itself. The admin panel serves it at `/sing-box-dashboard/` from `{api.url}/dashboard/`, so the API
service must have `dashboard` set. After the administrator logs in, the admin panel writes the API address and secret into the
panel settings in the browser, and it opens without asking. On someone else's computer do not log in, or delete the server in the
panel settings: the secret stays in the browser.

<a id="help"></a>
## Help

- The **?** icon next to the page title opens the help of the page; the icon in the corner of a block opens the section about
  that block. The help opens in a side panel over the page; Esc or ✕ closes it.
- “Open the full help” in the panel, or the “Help” menu item, opens the help page with contents on the left and search across
  all sections.
- The help is part of the package and works without internet access. The same text is in the `docs/help` folder of the
  repository.
