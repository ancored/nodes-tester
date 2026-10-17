const e=`**English** | [Русский](singbox-files.ru.md)

# sing-box files

The source files of the sing-box configuration live on the router in \`/etc/nodes-tester/singbox/\`: the router base, rule sources,
presets and the client bases. This page edits them with checks and a history of versions. It needs the administrator token and the
embedded admin panel.

**Saving changes the source file, not the running sing-box.** The base and presets reach sing-box with “Apply” on the
[“Rules”](rules.md#apply) page or with the next router [pipeline](pipeline.md) run; client files — with “Build clients”.

<a id="files"></a>
## Files

The “File” list groups the files by purpose:

| Group | Files | Purpose |
|---|---|---|
| Main | \`base.json\`, \`rules.json\` | the router base (inbounds, DNS, basic routes, \`block-out\`, \`direct-out\`); the list of rule sets to download |
| Rule presets | \`presets/*.json\` | router presets ([Rules](rules.md)) |
| Rule sources | \`rules/*.json\` | local rule sets in the sing-box source format |
| Client presets | \`clients/presets/*.json\` | presets shared by all clients |
| Clients | \`clients/base_<name>.json\`, \`clients/settings.json\`, \`clients/publish/*\` | client bases, the rule set distribution address, files published as they are |

“New or another path” + “Open or create” opens any allowed path, for example \`presets/my-rules.json\`; the file is created on saving.

<a id="editor"></a>
## Editing and checks

Edit the JSON in the editor or load a local file with “Upload JSON into the draft” (it replaces the draft; nothing is saved yet).
“Save file” writes it.

The server checks the file before writing:

- \`base.json\` and router presets — it merges them with the current \`nodes.json\` and the enabled presets and runs \`sing-box check\`.
  If there is no \`nodes.json\` yet, run the router pipeline first;
- client bases, client presets and \`clients/settings.json\` — it checks the config of every client;
- if the file was changed in another window or over SSH after you opened it, saving is refused: reload the file. Unsaved edits are
  lost after reloading.

“Delete preset” removes a preset; the file stays in the history. The running configuration changes only after “Apply” or “Build
clients” on the Rules page.

<a id="rules-json"></a>
## Rule sources (rules.json)

\`rules.json\` lists the rule sets the router pipeline updates. For it the page shows a form above the JSON:

| Field | Meaning |
|---|---|
| Destination | the file name in \`/etc/sing-box/rules/\`, for example \`category-ru.srs\` |
| Source: URL | a binary \`.srs\` downloaded by HTTP(S) on every pipeline run |
| Source: local file | a JSON rule set from \`rules/<name>.json\`; upload it with “Upload a local JSON” (saved at once) |

After changing the list press “Save file”. sing-box 1.10 and newer rereads changed rule sets without a restart.

<a id="versions"></a>
## Previous versions

Every save keeps the previous content (up to 20 versions). “View” shows a version and the list of fields that differ from the current
file; “Restore” brings it back (the current content goes to the history too). A restored file, like a saved one, reaches sing-box
only after applying.
`;export{e as default};
