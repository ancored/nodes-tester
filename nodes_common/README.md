**English** | [Русский](README.ru.md)

# nodes_common

`nodes_common` holds the shared file contract between pipeline stages and safe
write operations. The module knows nothing about particular protocols or grouping rules.

## `raw.py`

`raw_nodes.json` is the output of [`nodes_fetch`](../nodes_fetch/README.md) and the input of
[`nodes_config`](../nodes_config/README.md).

```json
{
  "version": 1,
  "generated_at": "2026-09-27T12:00:00Z",
  "providers": "providers-main",
  "nodes_hash": "sha256:...",
  "sources": [],
  "nodes": []
}
```

`validate` runs the required structural check without depending on `jsonschema`,
so that it works on a minimal OpenWrt. `nodes_hash` is computed from the canonical JSON of the node
array and does not depend on the fetch time or source statuses.

`make_node` separates the original name from the outbound settings: `tag` goes to `title`, and
service `_` fields do not get into the artefact.

## `fileio.py`

Shared operations:

- `read_json`: reading UTF-8 JSON;
- `dumps_json`: a single JSON format with a trailing newline;
- `atomic_write_text`: writing to a temporary file, `flush`, `fsync` and `os.replace`;
- `file_lock`: an inter-process lock `<target>.lock` on Linux and Windows;
- `parse_iso`: the dates of the contract.

An atomic write guarantees that a reader sees the old or the new file, not a partially
written JSON. The lock does not replace atomicity: it protects against two regular writers, and the
atomic replacement protects the reader and the result on a failure.

## Invariants

- change the raw version on an incompatible change of the structure;
- do not add computed names and groups to the raw;
- keep the order of providers and nodes;
- do not include time and source statuses in `nodes_hash`;
- new fields must be reflected in `schemas/raw_nodes.schema.json` and the tests of both stages.
