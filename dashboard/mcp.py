"""MCP-сервер админки: `POST /mcp` (Streamable HTTP, JSON-RPC 2.0).

Инструменты вызывают те же маршруты `/api/...` через `App.handle` с токеном админки: права,
проверки и сообщения об ошибках — как в интерфейсе. Сверх API — сводная карточка ноды,
хвост лога sing-box и SQL только для чтения по stats.db. Сервер без состояния: сессий и потока
SSE нет, на каждый запрос — один JSON-ответ.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit

from naming import NODE_KEY_RE, parse_node
from nodes_tester import __version__
from nodes_tester.monitor import singbox_log_path

from .api_read import _SLICES
from .api_session import SOURCE_URL
from .webapp import App, HttpError, Response

# Первая — та, что предлагаем клиенту, не назвавшему свою.
PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = """\
nodes-tester {version} runs next to {core} on an OpenWrt router. The pipeline fetches proxy subscriptions,
builds and applies the sing-box config; the tester measures every node and keeps each group's
selector (eu, us, ai, ...) on the best candidate.

- Start with `overview`. A node key is `node_crc.cred_crc` (8 hex digits each), shown in tags as
  `[...]`; tools that take `node` accept the key or the whole tag. Times are Unix seconds.
- Read tools are safe. Others change the live router of a home network: run `run_pipeline` with
  `dry_run: true` first; `singbox_control stop` cuts the internet of the whole home; a config
  write restarts the tester. Say what you are going to change before doing it.
- `query_db` answers questions the ready-made views do not (tables: nodes, scores, results,
  score_history, activations, node_events, garbage, traffic, endpoints, ip_info, meta).

Read the rules before changing configs. The `docs` tool returns the documents of this version
(also online: {docs_url}).
- config.json (`update_config`): field reference docs/CONFIG.md, defaults in `config_schema`.
- sing-box sources (`edit_singbox_file`), docs/CONFIG_ASSEMBLY.md: the config is base.json, then
  nodes.json, then the enabled presets by `_preset.priority` (lower first). Scalars: base.json wins,
  then the lower priority. Arrays are joined in that order; the same `tag` with other content and a
  reference to an outbound, DNS server or rule set nobody declares are errors. A preset declares the
  DNS servers and rule sets it uses and routes to zone outbounds (`ai-out`, `google-out`, `us-out`),
  not to node groups. A saved file has passed the merge and `sing-box check`, but takes effect only
  with `run_pipeline` (`apply` rebuilds from the fetched subscriptions without fetching them again).
- Security, docs/SECURITY_MODEL.md: the untrusted LAN goes to `direct-out`, and its rule stays above
  every rule meant for trusted sources; DNS rules mirror the route rules; `route.final` is a node
  group. Configs, node JSON and subscription URLs are secrets.
- sing-box syntax of the running core: {singbox_docs}
"""


def core_info() -> dict:
    """Версия и build-теги ядра из `sing-box version`; пусто, если бинаря нет."""
    path = shutil.which("sing-box")
    if not path:
        return {}
    # Ключ кэша — mtime бинаря: после обновления ядра ответ сменится без перезапуска тестера.
    # Исключение lru_cache не запоминает: неудачный запуск повторится при следующем вызове.
    try:
        return _core_info(path, os.stat(path).st_mtime)
    except (OSError, subprocess.TimeoutExpired):
        return {}


@lru_cache(maxsize=4)
def _core_info(path: str, _mtime: float) -> dict:
    out = subprocess.run([path, "version"], capture_output=True, text=True, timeout=10).stdout
    info = {"path": path}
    for line in out.splitlines():
        if line.startswith("sing-box version "):
            info["version"] = line.split()[-1]
        elif line.startswith("Tags: "):
            info["tags"] = line.removeprefix("Tags: ").strip().split(",")
    return info


def _instructions() -> str:
    """INSTRUCTIONS со ссылками на документацию работающих версий тестера и ядра."""
    version = core_info().get("version")
    if version is None:
        core, singbox_docs = "sing-box", "https://sing-box.sagernet.org/configuration/"
    elif "-lx" in version:
        # У форка своя копия документации upstream по тегу сборки и описание расширений.
        repo = f"https://github.com/Leadaxe/sing-box-lx/blob/v{version}"
        core = f"sing-box-lx {version}"
        singbox_docs = (f"{repo}/docs/configuration/index.md, lx extensions (XHTTP, AmneziaWG, urltest "
                        f"modes, WARP, DNS server groups, chains): {repo}/docs-lx/lx-config.md")
    else:
        core = f"sing-box {version}"
        singbox_docs = f"https://github.com/SagerNet/sing-box/blob/v{version}/docs/configuration/index.md"
    return INSTRUCTIONS.format(version=__version__, core=core, singbox_docs=singbox_docs,
                               docs_url=f"{SOURCE_URL}/tree/v{__version__}/docs")


class ToolError(Exception):
    """Ошибка инструмента: уходит клиенту результатом с isError, а не ошибкой протокола."""


# --- Вызов API админки изнутри процесса --------------------------------------


def _api(app: App, method: str, path: str, *, query=None, body=None, if_match=None):
    headers = {"X-Admin-Token": app.token}
    if if_match:
        headers["If-Match"] = if_match
    if query:
        path += "?" + urlencode({k: v for k, v in query.items() if v is not None})
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    try:
        resp = app.handle(method, path, headers, payload)
    except Exception as exc:                       # noqa: BLE001 — как 500 в HTTP-сервере
        raise ToolError(f"HTTP 500: {type(exc).__name__}: {exc}") from exc
    try:
        result = json.loads(resp.body) if resp.body else None
    except ValueError:
        result = resp.body.decode("utf-8", "replace")
    if resp.status >= 400:
        message = result.get("error") if isinstance(result, dict) else result
        raise ToolError(f"HTTP {resp.status}: {message}")
    return result


def _soft(app: App, method: str, path: str, **kwargs):
    """Часть сводки: ошибка одной части (например, нет Runner) не ломает остальные."""
    try:
        return _api(app, method, path, **kwargs)
    except ToolError as exc:
        return {"error": str(exc)}


def _trim(value, limit: int):
    """Списки длиннее limit обрезаются с пометкой: ответ не должен съедать контекст клиента."""
    if isinstance(value, list):
        items = [_trim(v, limit) for v in value[:limit]]
        if len(value) > limit:
            items.append({"_truncated": len(value) - limit})
        return items
    if isinstance(value, dict):
        return {k: _trim(v, limit) for k, v in value.items()}
    return value


def _node_key(node: str) -> str:
    key = node.strip().lower()
    if NODE_KEY_RE.fullmatch(key):
        return key
    key = parse_node(node).node_id
    if not NODE_KEY_RE.fullmatch(key or ""):
        raise ToolError("node: ключ node_crc.cred_crc или тег ноды с ключом в [...]")
    return key


# --- SQL только для чтения ---------------------------------------------------

_SQL_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
_SQL_PRAGMAS = {"table_info", "table_xinfo", "table_list", "index_list", "index_info"}


def _authorizer(action, arg1, arg2, _db, _source):
    # Сверх mode=ro: ни ATTACH других файлов, ни PRAGMA, кроме описания схемы.
    if action in _SQL_ACTIONS or (action == sqlite3.SQLITE_PRAGMA and arg1 in _SQL_PRAGMAS and arg2 is None):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _select(app: App, sql: str, params=(), limit: int = 200, timeout: float = 5.0):
    """(колонки, строки, обрезано ли) одного SELECT по stats.db в режиме только чтения."""
    db = app.cfg.storage.path
    if not db or not os.path.exists(db):
        raise ToolError("база ещё не создана")
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    deadline = time.monotonic() + timeout
    try:
        con.set_authorizer(_authorizer)
        con.set_progress_handler(lambda: time.monotonic() > deadline, 10000)
        cur = con.execute(sql, params)
        columns = [d[0] for d in cur.description or ()]
        rows = cur.fetchmany(limit + 1)
    except sqlite3.Error as exc:
        if time.monotonic() > deadline:
            raise ToolError(f"запрос прерван: дольше {timeout:g} с") from exc
        raise ToolError(f"SQL: {exc}") from exc
    finally:
        con.close()
    return columns, rows[:limit], len(rows) > limit


def _dicts(app: App, sql: str, params=(), limit: int = 200) -> list[dict]:
    columns, rows, _ = _select(app, sql, params, limit)
    return [dict(zip(columns, row)) for row in rows]


# --- Таблица инструментов ------------------------------------------------------

TOOLS: dict[str, dict] = {}


def tool(name: str, description: str, properties: dict | None = None, required=(),
         *, read_only=True, destructive=False, idempotent=False):
    def deco(fn):
        TOOLS[name] = {"fn": fn, "spec": {
            "name": name, "description": description,
            "inputSchema": {"type": "object", "properties": properties or {},
                            "required": list(required), "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": destructive,
                            "idempotentHint": read_only or idempotent, "openWorldHint": False},
        }}
        return fn
    return deco


def _str(description: str, enum=None) -> dict:
    return {"type": "string", "description": description, **({"enum": list(enum)} if enum else {})}


def _int(description: str, default: int, maximum: int) -> dict:
    return {"type": "integer", "description": description, "default": default,
            "minimum": 1, "maximum": maximum}


_LIMIT = _int("Maximum list length in the answer; longer lists are cut with a `_truncated` count.", 50, 1000)
_NODE = _str("Node key `node_crc.cred_crc` or the whole node tag.")

_TYPES = {"string": str, "integer": int, "boolean": bool, "object": dict, "array": list}


def _check_args(spec: dict, args: dict) -> dict:
    schema = spec["inputSchema"]
    props = schema["properties"]
    unknown = set(args) - set(props)
    if unknown:
        raise ToolError(f"неизвестные аргументы: {', '.join(sorted(unknown))}")
    for name in schema["required"]:
        if name not in args:
            raise ToolError(f"нужен аргумент {name}")
    out = {}
    for name, prop in props.items():
        if name not in args:
            if "default" in prop:
                out[name] = prop["default"]
            continue
        value = args[name]
        expected = _TYPES[prop["type"]]
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ToolError(f"{name}: ожидается {prop['type']}")
        if "enum" in prop and value not in prop["enum"]:
            raise ToolError(f"{name}: одно из {', '.join(prop['enum'])}")
        if expected is int and not prop.get("minimum", value) <= value <= prop.get("maximum", value):
            raise ToolError(f"{name}: от {prop['minimum']} до {prop['maximum']}")
        out[name] = value
    return out


# --- Чтение --------------------------------------------------------------------


@tool("overview", "Snapshot of the router: version and capabilities, sing-box core version and build tags, "
      "tester status (active node per group, progress, outage), groups with their top candidates and "
      "reserve, sing-box and kill switch state, pipeline schedule and last runs. Start here.")
def overview(app):
    return {
        "capabilities": _soft(app, "GET", "/api/capabilities"),
        "core": core_info(),
        "tester": _soft(app, "GET", "/api/status"),
        "groups": _soft(app, "GET", "/api/groups"),
        "singbox": _soft(app, "GET", "/api/singbox/control"),
        "pipeline": _soft(app, "GET", "/api/pipeline"),
    }


@tool("list_nodes", "Nodes from the current subscriptions with score, group, state (active, banned, "
      "quarantine `gstate`/`guntil`, reserve) and the groups where the node can be activated. Filters "
      "combine with AND; text filters are case-insensitive.", {
          "group": _str("Only members of this group (eu, us, ai, ...)."),
          "provider": _str("Subscription tag, e.g. LUNA."),
          "country": _str("Two-letter exit country."),
          "protocol": _str("vless, hysteria2, awg, ..."),
          "search": _str("Substring of the tag, title, server or key."),
          "state": _str("Only nodes in this state.", ("active", "banned", "restricted", "reserve", "candidate")),
          "sort": _str("Sort order; score is descending.", ("score", "provider", "country", "protocol")),
          "limit": _LIMIT,
      })
def list_nodes(app, limit, group=None, provider=None, country=None, protocol=None, search=None,
               state=None, sort="score"):
    nodes = _api(app, "GET", "/api/data")["nodes"]

    def same(value, wanted):
        return wanted is None or str(value or "").lower() == wanted.lower()

    def matches(n):
        text = " ".join(str(n.get(k) or "") for k in ("node", "title", "server", "crc")).lower()
        flags = {"active": n.get("active"), "banned": n.get("banned"), "restricted": n.get("gstate"),
                 "reserve": n.get("reserve"), "candidate": n.get("can_activate")}
        return (same(n.get("provider"), provider) and same(n.get("country"), country)
                and same(n.get("protocol"), protocol)
                and (group is None or group in (n.get("groups") or []))
                and (search is None or search.lower() in text)
                and (state is None or bool(flags[state])))

    found = [n for n in nodes if matches(n)]
    if sort == "score":
        found.sort(key=lambda n: -float(n.get("score") or -1))
    else:
        found.sort(key=lambda n: (str(n.get(sort) or ""), -float(n.get("score") or -1)))
    return {"total": len(found), "nodes": found[:limit]}


@tool("get_node", "Everything about one node: identity and presence, rating components (scores row), "
      "the latest result of every test, group membership and group checks, restrictions, recent events, "
      "activations and score history. `include_config` adds the sing-box outbound JSON with credentials.", {
          "node": _NODE,
          "include_config": {"type": "boolean", "default": False,
                             "description": "Add the outbound JSON and key strings (contains secrets)."},
          "limit": _int("Rows of events, activations and score history.", 20, 500),
      }, required=("node",))
def get_node(app, node, include_config, limit):
    crc = _node_key(node)
    rows = _dicts(app, "SELECT * FROM nodes WHERE crc=?", (crc,), 1)
    if not rows:
        raise ToolError("нода не найдена в базе")
    info = rows[0]
    info.pop("payload", None)
    out = {
        "node": info,
        "scores": next(iter(_dicts(app, "SELECT * FROM scores WHERE crc=?", (crc,), 1)), None),
        "restriction": next(iter(_dicts(app, "SELECT * FROM garbage WHERE crc=?", (crc,), 1)), None),
        "latest_results": _dicts(app, """
            SELECT r.test, r.ts, r.ok, r.url, r.metrics, r.error FROM results r
            JOIN (SELECT test, MAX(ts) AS ts FROM results WHERE crc=? GROUP BY test) m
              ON m.test = r.test AND m.ts = r.ts
            WHERE r.crc=? ORDER BY r.test""", (crc, crc)),
        "groups": _soft(app, "GET", f"/api/nodes/{crc}/groups"),
        "events": _dicts(app, "SELECT ts, event, reason, streak FROM node_events WHERE crc=? "
                              "ORDER BY ts DESC LIMIT ?", (crc, limit), limit),
        "activations": _dicts(app, "SELECT ts, region, reason, score, prev FROM activations WHERE crc=? "
                                   "ORDER BY ts DESC LIMIT ?", (crc, limit), limit),
        "score_history": _dicts(app, "SELECT ts, region, score, s_run, gate FROM score_history WHERE crc=? "
                                     "ORDER BY ts DESC LIMIT ?", (crc, limit), limit),
    }
    for row in out["latest_results"]:
        try:
            row["metrics"] = json.loads(row["metrics"] or "null")
        except ValueError:
            pass
    if include_config:
        details = _api(app, "GET", f"/api/nodes/{crc}/details")
        details.pop("score_history", None)
        out["config"] = details
    return out


@tool("get_view", "A ready-made admin panel view: rating (all scored nodes), history (switches), "
      "results (latest tests per node), traffic (by provider, country, protocol, top nodes and "
      "destinations), lifecycle (provider quality, garbage, longevity, dropouts), graveyard (nodes gone "
      "from subscriptions).", {
          "view": _str("Which view.", _SLICES),
          "limit": _LIMIT,
      }, required=("view",))
def get_view(app, view, limit):
    return _trim(_api(app, "GET", f"/api/{view}"), limit)


def _grep(lines: list[str], pattern: str | None) -> list[str]:
    if not pattern:
        return lines
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ToolError(f"grep: {exc}") from exc
    return [line for line in lines if rx.search(line)]


_GREP = _str("Case-insensitive regular expression; only matching lines are returned.")


@tool("tester_log", "The last lines of the tester's live log (measurements, switches, monitor, pipeline).", {
    "lines": _int("How many last lines (after grep).", 200, 2000),
    "grep": _GREP,
})
def tester_log(app, lines, grep=None):
    entries = _api(app, "GET", "/api/logs", query={"tail": 2000})["lines"]
    text = [f"{time.strftime('%m-%d %H:%M:%S', time.localtime(e['ts']))} {e['line']}" for e in entries]
    return "\n".join(_grep(text, grep)[-lines:])


@tool("singbox_log", "The last lines of the sing-box log file (`log.output` of the working config): "
      "connection errors, DNS, outbound failures. `rotated` also reads the previous file `.1`.", {
          "lines": _int("How many last lines (after grep).", 200, 5000),
          "grep": _GREP,
          "rotated": {"type": "boolean", "default": False, "description": "Include the rotated `.1` file."},
      })
def singbox_log(app, lines, rotated, grep=None):
    path = singbox_log_path(app.cfg.singbox.config)
    if not path:
        raise ToolError("в конфиге sing-box лог не пишется в файл (log.output)")
    text = []
    for name in ([path + ".1"] if rotated else []) + [path]:
        try:
            with open(name, encoding="utf-8", errors="replace") as fh:
                text += fh.read().splitlines()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise ToolError(f"{name}: {exc}") from exc
    return "\n".join(_grep(text, grep)[-lines:])


@tool("pipeline", "Pipeline (fetch subscriptions, build, check and apply the sing-box config): without "
      "`run_id` — schedule, next runs and recent runs; with `run_id` — that run and its log.", {
          "run_id": _str("Run id from the list or from run_pipeline."),
          "limit": _int("Recent runs to list.", 10, 100),
      })
def pipeline(app, limit, run_id=None):
    if run_id is None:
        return {"status": _api(app, "GET", "/api/pipeline"),
                "runs": _api(app, "GET", "/api/pipeline/runs", query={"limit": limit})["runs"]}
    return {**_api(app, "GET", f"/api/pipeline/runs/{quote(run_id, safe='')}"), "log": _run_log(app, run_id)}


def _run_log(app, run_id: str) -> str:
    """Весь журнал прогона: API отдаёт его кусками по 64 КБ."""
    parts, offset = [], 0
    while True:
        chunk = _api(app, "GET", f"/api/pipeline/runs/{quote(run_id, safe='')}/log", query={"offset": offset})
        if not chunk["text"]:
            return "".join(parts)
        parts.append(chunk["text"])
        offset = chunk["offset"]


@tool("get_config", "Tester config.json (subscriptions, groups, checks, selection, pipeline, admin, "
      "notify, ...) with its `revision` for update_config. Contains secrets (subscription URLs, tokens).", {
          "sections": {"type": "array", "items": {"type": "string"},
                       "description": "Only these top-level sections; all when omitted."},
      })
def get_config(app, sections=None):
    doc = _api(app, "GET", "/api/config")
    if sections is not None:
        doc["data"] = {k: doc["data"][k] for k in sections if k in doc["data"]}
    return doc


@tool("config_schema", "JSON schema of config.json with descriptions and defaults: without `section` — "
      "the list of sections; with it — that section's schema.", {
          "section": _str("Top-level section, e.g. groups or selection."),
      })
def config_schema(app, section=None):
    props = _api(app, "GET", "/api/config/schema").get("properties", {})
    if section is None:
        return {name: (p.get("description") or "") for name, p in props.items()}
    if section not in props:
        raise ToolError(f"нет раздела {section}; есть: {', '.join(props)}")
    return props[section]


@tool("singbox_files", "Sources of the sing-box config on the router (base, rules, presets, clients): "
      "without `path` — the file list and presets of a branch with their state; with `path` — the file "
      "with its `revision` and saved versions; with `ts` — that saved version.", {
          "path": _str("File path from the list, e.g. presets/core.json."),
          "ts": _str("Saved version (19 digits) of the file at `path`."),
          "branch": _str("Preset branch for the list.", ("router", "clients")),
      })
def singbox_files(app, path=None, ts=None, branch="router"):
    if path is None:
        return {"files": _api(app, "GET", "/api/singbox/files")["files"],
                "presets": _api(app, "GET", "/api/singbox/presets", query={"branch": branch})}
    url = f"/api/singbox/files/{quote(path)}"
    if ts is not None:
        return _api(app, "GET", url + "/history", query={"ts": ts})
    return {**_api(app, "GET", url), "versions": _api(app, "GET", url + "/history")["versions"]}


_ROOT = Path(__file__).resolve().parent.parent
# Пакет кладёт docs/ рядом с модулями (/usr/lib/nodes-tester), как в репозитории; только английские.
_DOC_DIRS = ("docs", "docs/help", "naming", "nodes_common", "nodes_fetch", "nodes_config", "nodes_tester",
             "nodes_admin", "dashboard")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")


def _documents() -> dict[str, Path]:
    found = {}
    for folder in _DOC_DIRS:
        for path in sorted((_ROOT / folder).glob("*.md")):
            if not path.name.endswith(".ru.md"):
                found[f"{folder}/{path.name}"] = path
    return found


def _headings(lines: list[str]):
    """(номер строки, уровень, текст) заголовков вне блоков кода."""
    fence = False
    for number, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):     # блок кода бывает и с отступом в списке
            fence = not fence
        elif not fence and (m := _HEADING.match(line)):
            yield number, len(m.group(1)), m.group(2)


@tool("docs", "Documentation of the running nodes-tester version, offline: config.json field reference "
      "(docs/CONFIG.md), sing-box config assembly and presets (docs/CONFIG_ASSEMBLY.md), security model "
      "(docs/SECURITY_MODEL.md), sing-box compatibility, admin panel help (docs/help), module READMEs "
      "(checks, scoring, storage). Without `path` — the list with titles and sections; with `section` — "
      "only the sections whose heading contains that text.", {
          "path": _str("Document path from the list, e.g. docs/CONFIG_ASSEMBLY.md."),
          "section": _str("Case-insensitive part of a heading; the section is returned with its subsections."),
      })
def docs(app, path=None, section=None):
    if path is None and section is not None:
        raise ToolError("section — только вместе с path")
    found = _documents()
    if path is None:
        listing = []
        for name, file in found.items():
            heads = list(_headings(file.read_text(encoding="utf-8").splitlines()))
            listing.append({"path": name, "size": file.stat().st_size,
                            "title": next((text for _, level, text in heads if level == 1), name),
                            "sections": [text for _, level, text in heads if level == 2]})
        return {"documents": listing, "online": f"{SOURCE_URL}/tree/v{__version__}"}
    if path not in found:
        raise ToolError(f"нет документа {path}; список — docs без path")
    lines = found[path].read_text(encoding="utf-8").splitlines()
    if section is None:
        return "\n".join(lines)
    heads = list(_headings(lines))
    parts, taken = [], 0
    for n, (start, level, text) in enumerate(heads):
        if start < taken or section.lower() not in text.lower():
            continue
        taken = next((line for line, deeper, _ in heads[n + 1:] if deeper <= level), len(lines))
        parts.append("\n".join(lines[start:taken]))
    if not parts:
        raise ToolError(f"в {path} нет раздела с «{section}»")
    return "\n\n".join(parts)


@tool("query_db", "Read-only SQL (one SELECT or WITH statement, SQLite) over the tester database "
      "stats.db; 5 s limit. Key column `crc` = node key. Main tables: nodes (identity, present, banned, "
      "title), scores (current rating and components), results (ts, crc, test, ok, metrics JSON, error), "
      "score_history, activations (ts, region, crc, reason, prev), node_events, garbage (pause and "
      "quarantine), traffic (ts, crc, up, down, conns, is_tester), endpoints, ip_info. Schema: "
      "SELECT sql FROM sqlite_master.", {
          "sql": _str("The query; use ? placeholders with `params`."),
          "params": {"type": "array", "description": "Values for ? placeholders."},
          "limit": _int("Maximum rows.", 200, 5000),
      }, required=("sql",))
def query_db(app, sql, limit, params=()):
    columns, rows, truncated = _select(app, sql, tuple(params), limit)
    return {"columns": columns, "rows": [list(r) for r in rows], "truncated": truncated}


# --- Действия --------------------------------------------------------------------


@tool("node_action", "Ban or unban a node (excluded from checks and groups until unbanned), put it in "
      "quarantine for `selection.quarantine.duration` or lift the quarantine. An active node is replaced.", {
          "node": _NODE,
          "action": _str("What to do.", ("ban", "unban", "quarantine", "unquarantine")),
      }, required=("node", "action"), read_only=False, idempotent=True)
def node_action(app, node, action):
    return _api(app, "POST", f"/api/nodes/{_node_key(node)}/{action}")


@tool("switch_group", "Activate a node in a group now (until the next rotation or failure). The node must "
      "be a candidate of the group (see list_nodes state=candidate or overview).", {
          "group": _str("Group tag, e.g. eu."),
          "node": _str("Whole node tag."),
      }, required=("group", "node"), read_only=False, idempotent=True)
def switch_group(app, group, node):
    return _api(app, "POST", f"/api/groups/{quote(group, safe='')}/switch", body={"node": node})


@tool("run_tests", "Queue a full measurement of all nodes, ignoring result lifetimes.", read_only=False)
def run_tests(app):
    return _api(app, "POST", "/api/run/tests")


@tool("run_pipeline", "Start the pipeline. router — fetch subscriptions, build and apply the router config "
      "(restarts sing-box if it changed, checks connectivity, rolls back on failure); clients — build client "
      "configs; apply / apply-clients — rebuild from the last fetched subscriptions. `dry_run` builds and "
      "checks without applying. `wait` seconds to wait for the end and return the log.", {
          "mode": _str("Pipeline mode.", ("router", "clients", "apply", "apply-clients")),
          "dry_run": {"type": "boolean", "description": "Build and check only, change nothing."},
          "wait": {"type": "integer", "default": 0, "minimum": 0, "maximum": 600,
                   "description": "Seconds to wait for completion; 0 returns the run id at once."},
      }, required=("mode", "dry_run"), read_only=False, destructive=True)
def run_pipeline(app, mode, dry_run, wait):
    run_id = _api(app, "POST", "/api/pipeline/run", body={"mode": mode, "dry_run": dry_run})["run_id"]
    deadline = time.monotonic() + wait
    while True:
        run = _api(app, "GET", f"/api/pipeline/runs/{run_id}")["run"]
        if run.get("status") != "running" or time.monotonic() >= deadline:
            break
        time.sleep(2)
    out = {"run_id": run_id, "run": run}
    if run.get("status") != "running":
        out["log"] = _run_log(app, run_id)
    return out


@tool("update_config", "Replace top-level sections of config.json (null removes a section). The file is "
      "validated like the tester loads it; the tester restarts itself unless the pipeline is running. "
      "Pass the `revision` from get_config to refuse the write if the file changed since.", {
          "changes": {"type": "object", "description": "{section: new value or null}; whole sections, not patches."},
          "revision": _str("Revision from get_config."),
      }, required=("changes",), read_only=False, destructive=True)
def update_config(app, changes, revision=None):
    if not changes:
        raise ToolError("changes пуст")
    doc = _api(app, "GET", "/api/config")
    if revision is not None and revision != doc["revision"]:
        raise ToolError("config.json изменился после чтения: перечитайте его")
    base = {key: doc["data"].get(key) for key in changes}
    return _api(app, "POST", "/api/config/sections", body={"base": base, "set": changes})


@tool("edit_singbox_file", "Change a sing-box source file: write new JSON content (checked with the "
      "whole assembly for base and presets), restore a saved version or delete a preset. The previous "
      "content goes to the file history. Needs the file `revision` from singbox_files. Takes effect at the "
      "next pipeline run.", {
          "path": _str("File path, e.g. presets/core.json."),
          "action": _str("What to do.", ("write", "restore", "delete")),
          "revision": _str("Current revision of the file from singbox_files."),
          "content": {"type": "object", "description": "New JSON content for write."},
          "ts": _str("Saved version to restore."),
      }, required=("path", "action", "revision"), read_only=False, destructive=True)
def edit_singbox_file(app, path, action, revision, content=None, ts=None):
    url = f"/api/singbox/files/{quote(path)}"
    if action == "write":
        if content is None:
            raise ToolError("write: нужен content")
        return _api(app, "PUT", url, body=content, if_match=revision)
    if action == "restore":
        if ts is None:
            raise ToolError("restore: нужен ts")
        return _api(app, "POST", url + "/restore", body={"ts": ts}, if_match=revision)
    return _api(app, "DELETE", url, if_match=revision)


@tool("singbox_control", "Stop or start sing-box, turn the router kill switch service on or off (OpenWrt). "
      "Stopping sing-box takes the internet away from the whole home network while the kill switch is on.", {
          "action": _str("What to do.", ("stop", "start", "killswitch_on", "killswitch_off")),
      }, required=("action",), read_only=False, destructive=True)
def singbox_control(app, action):
    if action.startswith("killswitch"):
        return _api(app, "PUT", "/api/singbox/killswitch", body={"enabled": action == "killswitch_on"})
    return _api(app, "POST", f"/api/singbox/{action}")


@tool("call_api", "Any admin panel API route for what the other tools do not cover, e.g. "
      "POST /api/storage/vacuum, POST /api/notify/test, GET or PUT /api/pipeline/schedule, "
      "GET /api/config/subscription-options. Routes are listed in dashboard/README.md.", {
          "method": _str("HTTP method.", ("GET", "POST", "PUT", "DELETE")),
          "path": _str("Path starting with /api/, without the query string."),
          "query": {"type": "object", "description": "Query string parameters."},
          "body": {"description": "JSON body.", "type": "object"},
          "if_match": _str("If-Match header (revision) for writes that need it."),
      }, required=("method", "path"), read_only=False, destructive=True)
def call_api(app, method, path, query=None, body=None, if_match=None):
    if not path.startswith("/api/") or "?" in path:
        raise ToolError("path: /api/... без query; параметры — в query")
    return _api(app, method, path, query=query, body=body, if_match=if_match)


# --- JSON-RPC ------------------------------------------------------------------


def _rpc_error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _call_tool(app: App, params: dict) -> dict:
    entry = TOOLS.get(params.get("name"))
    if entry is None:
        raise LookupError(f"неизвестный инструмент: {params.get('name')}")
    args = params.get("arguments") or {}
    try:
        if not isinstance(args, dict):
            raise ToolError("arguments должен быть объектом")
        result = entry["fn"](app, **_check_args(entry["spec"], args))
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except Exception as exc:                       # noqa: BLE001 — ошибка уходит клиенту
        return {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True}
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _handle(app: App, msg) -> dict | None:
    """Ответ на одно сообщение; None — уведомление или ответ клиента (ответа не требуют)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return _rpc_error(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
    if "method" not in msg or "id" not in msg:
        return None
    msg_id, method, params = msg["id"], msg["method"], msg.get("params", {})
    if not isinstance(params, dict):
        return _rpc_error(msg_id, -32602, "params должен быть объектом")
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "nodes-tester", "version": __version__},
                  "instructions": _instructions()}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [entry["spec"] for entry in TOOLS.values()]}
    elif method == "tools/call":
        try:
            result = _call_tool(app, params)
        except LookupError as exc:
            return _rpc_error(msg_id, -32602, str(exc))
    else:
        return _rpc_error(msg_id, -32601, f"method not found: {method}")
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def register(app: App) -> None:
    @app.route("POST", "/mcp", needs_token=True)
    def mcp(app, req):
        # Защита от DNS rebinding: страница чужого сайта не должна говорить с /mcp из браузера.
        origin = req.headers.get("Origin")
        if origin and urlsplit(origin).netloc != req.headers.get("Host"):
            raise HttpError(403, "Origin не совпадает с Host")
        version = req.headers.get("MCP-Protocol-Version")
        if version and version not in PROTOCOL_VERSIONS:
            raise HttpError(400, f"неподдерживаемая версия MCP: {version}")
        try:
            message = json.loads(req.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return Response.json(_rpc_error(None, -32700, "parse error"))
        if message == []:
            return Response.json(_rpc_error(None, -32600, "пустой пакет"))
        if isinstance(message, list):
            replies = [r for r in (_handle(app, m) for m in message) if r is not None]
        else:
            replies = _handle(app, message)
        if not replies:
            return Response(202, "application/json", b"")
        return Response.json(replies)
