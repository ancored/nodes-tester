"""Update sing-box base and rule files from configured sources."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


DEST_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_rules(data: object) -> list:
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1 or not isinstance(data.get("rules"), list):
        raise ValueError("rules.json: нужны version=1 и массив rules")
    seen = set()
    for entry in data["rules"]:
        if not isinstance(entry, dict) or set(entry) not in ({"dest", "url"}, {"dest", "file"}):
            raise ValueError("rules.json: правило содержит dest и ровно одно из url/file")
        dest = entry["dest"]
        if not isinstance(dest, str) or not DEST_RE.fullmatch(dest) or dest in (".", ".."):
            raise ValueError("rules.json: dest должен быть именем файла")
        if dest in seen:
            raise ValueError("rules.json: dest повторяется: " + dest)
        seen.add(dest)
        if "url" in entry:
            url = entry["url"]
            if not isinstance(url, str):
                raise ValueError("rules.json: url должен быть строкой")
            parsed = urlsplit(url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("rules.json: разрешены только HTTP(S) URL")
        else:
            path = entry["file"]
            if not isinstance(path, str) or not path.startswith("rules/"):
                raise ValueError("rules.json: file должен лежать в rules/")
            name = path[len("rules/"):]
            if not DEST_RE.fullmatch(name) or not name.endswith(".json") or name in (".", ".."):
                raise ValueError("rules.json: file должен быть rules/<имя>.json")
    return data["rules"]


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = response.read(64 * 1024 * 1024 + 1)
    if len(payload) > 64 * 1024 * 1024:
        raise ValueError("правило больше 64 МиБ")
    return payload


def replace_if_changed(destination: Path, payload: bytes) -> bool:
    if not payload:
        raise ValueError("пустой источник")
    if destination.exists() and destination.read_bytes() == payload:
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + destination.name + ".", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, destination)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return True


def update(config_dir: Path, target_dir: Path, marker: Path, downloader=_download,
           base_only: bool = False) -> list:
    source_dir = config_dir / "singbox"
    entries = [] if base_only else validate_rules(
        json.loads((source_dir / "rules.json").read_text(encoding="utf-8")))
    changed = []
    base = source_dir / "base.json"
    if base.is_file():
        if replace_if_changed(target_dir / "base.json", base.read_bytes()):
            changed.append("base.json")
    else:
        print("[rules] нет base.json, оставляю прежний")
    for entry in entries:
        dest = target_dir / "rules" / entry["dest"]
        try:
            if "url" in entry:
                payload = downloader(entry["url"])
            else:
                source = source_dir / entry["file"]
                if source.is_symlink():
                    raise ValueError("символическая ссылка запрещена")
                payload = source.read_bytes()
            if replace_if_changed(dest, payload):
                changed.append(entry["dest"])
        except Exception as exc:
            # A failed source is isolated: the previous destination stays in place.
            print(f"[rules] не обновилось, оставляю прежний {entry['dest']}: {exc}")
    if changed:
        # Local rule-sets are reloaded by sing-box itself (1.10+); only a new base
        # needs a restart, which apply-nodes.sh performs when it sees the marker.
        if "base.json" in changed:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
        print("[rules] обновлено: " + ", ".join(changed))
    else:
        print("[rules] правила без изменений")
    return changed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", default=os.environ.get("CONFIG_DIR", "/etc/nodes-tester"))
    parser.add_argument("--target-dir", default=os.environ.get("TARGET_DIR", "/etc/sing-box"))
    parser.add_argument("--marker", default=os.environ.get("RULES_MARK", "/tmp/nodes-rules-changed"))
    parser.add_argument("--base-only", action="store_true",
                        help="только скопировать base.json, правила не загружать")
    args = parser.parse_args(argv)
    try:
        update(Path(args.config_dir), Path(args.target_dir), Path(args.marker),
               base_only=args.base_only)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"[rules] ошибка: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
