**English** | [Русский](README.ru.md)

# web

`web` contains the sources of the Vue 3 interface. The Python backend is in
[`dashboard`](../dashboard/README.md), and the user guide is in
[`docs/USAGE.md`](../docs/USAGE.md) and the built-in [help](../docs/help/README.md).

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../docs/screenshots/mobile-overview.png">
    <source media="(prefers-color-scheme: light)" srcset="../docs/screenshots/mobile-overview-light.png">
    <img alt="nodes-tester mobile overview" src="../docs/screenshots/mobile-overview-light.png" width="360">
  </picture>
  &nbsp;
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../docs/screenshots/mobile-nodes.png">
    <source media="(prefers-color-scheme: light)" srcset="../docs/screenshots/mobile-nodes-light.png">
    <img alt="Node list on a mobile screen" src="../docs/screenshots/mobile-nodes-light.png" width="360">
  </picture>
</p>

Other screens: [`docs/screenshots`](../docs/screenshots/README.md).

## Development

```sh
cd web
npm install
npm run dev
```

Vite forwards `/api` to the local Python server according to `vite.config.js`. A full check of
actions needs the embedded dashboard with a live Runner; a separate server
is fine for viewing and working with files.

## Build

```sh
cd web
npm run test
npm run build
```

`npm run test` runs the Node unit tests and the render check (each page in both languages). `npm run build` writes the files
not to `web/dist` but straight into `dashboard/static`. After changing the interface commit
the sources and the updated static files together.

## Structure

| Path | Purpose |
|---|---|
| `src/router.js` | sections, nested node navigation and the node card |
| `src/store.js` | the shared data snapshot and auto-refresh |
| `src/api.js` | HTTP, token, session and error handling |
| `src/i18n.js`, `src/locales/` | interface language and the Russian catalog |
| `src/query.js` | the state of filters, sorting and the page in the URL |
| `src/status.js` | the single dictionary of node statuses |
| `src/ux.js` | shared user checks and messages, form defaults from the schema |
| `src/help.js`, `src/markdown.js` | the built-in help: loading `docs/help`, rendering Markdown, the side panel state |
| `src/diagrams/` | flowcharts of the help as ready SVG (`npm run diagrams`) |
| `src/components/` | table, node actions, the help icon and panel and other blocks |
| `src/views/` | admin panel pages |

The `Pipeline.vue`, `Singbox.vue` and `Presets.vue` pages are available in the embedded admin panel with
the token and the orchestrator. The pipeline separates a check from applying, shows the history
and logs. The editor checks the sing-box base before writing and keeps previous file versions;
saving does not start applying.

The router uses hash history, so the static server does not need to know the SPA routes.
Filters and sorting are kept in the URL and survive the data auto-refresh.

The country in tables is shown as a flag, the code is in the tooltip. The flags are drawn by the
“Twemoji Country Flags” font from the `country-flag-emoji-polyfill` package (code: MIT, flag
images: [Twemoji](https://github.com/twitter/twemoji), CC-BY 4.0); it is part of the build and
is loaded only by browsers that cannot draw flags themselves (Windows).

## Help

The help text is Markdown in [`docs/help`](../docs/help/README.md): one file per page of the admin panel and pages about how
nodes-tester works, `name.md` in English and `name.ru.md` in Russian. The build includes them (each page is a separate chunk loaded on
demand), together with `docs/CONFIG.md`; `src/markdown.js` renders the subset of Markdown the help uses. Links between help files open
inside the help, links to other documents go to GitHub.

- Sections that pages and blocks refer to have explicit anchors `<a id="…"></a>` before the heading, the same in both languages.
- `<HelpButton topic="page#anchor" />` puts the **?** icon into a block heading; the page icon comes from the `help` field of the route
  in `src/router.js`. The icon opens the section in a side panel (`HelpPanel`); the “Help” page has contents and search.
- Explanations are kept in the help, not on the pages: the interface has field labels, short placeholders and state messages.
- Flowcharts are `mermaid` blocks in the Markdown: GitHub draws them itself, and the admin panel shows ready SVG without the mermaid
  library. After changing a flowchart run `npm run diagrams` (Chromium via `playwright-core`: `CHROMIUM_PATH` or the installed
  Chrome); the SVG take the colours of the admin panel theme.

`tests/help.test.js` checks that every icon and route points to an existing anchor in both languages, that the contents list every
help page and that every flowchart has its SVG; `tests/test_docs.py` checks the links and that both languages have the same anchors
and the same number of flowcharts.

## Two languages

Strings in the code are in English and go through `t()` from `src/i18n.js`; the translation is the
`src/locales/ru.js` catalog, where the key is the English string. Parameters are written as `{name}`:
`t('Snapshot {time}', { time })`. Markup inside a translated phrase goes through the `Msg` component with
named slots. A missing translation shows the English text. The language follows the browser by
default, the choice in the menu is stored in `localStorage` (`nt_lang`); dates and sorting use the
locale of the chosen language.

`tests/i18n.test.js` checks that every string passed to `t()` has a translation, that the catalog has no
stale keys or duplicates and that the parameters match, and that there is no Cyrillic in the code outside the catalog (except
comments and lines marked `i18n-skip`). When you add a string, add its Russian translation to
`ru.js` in the same change.

## Interface rules

- missing data must not be shown as a zero value;
- the rating and physical metrics are shown separately;
- dangerous actions require confirmation and explain the consequences;
- the interface distinguishes a separate dashboard from the embedded Runner;
- the administrator token is not put into the URL and can be stored only by an explicit choice;
- saving subscriptions is not presented as applying new nodes to sing-box;
- an empty settings field shows the default value from `config.schema.json` in grey and is not written to the file;
- explanations are in the help: every page and block has a **?** icon to its section.
