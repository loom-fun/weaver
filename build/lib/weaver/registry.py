"""models/<slug>.json: what weaver knows about each checkpoint it has ported."""
import json
import time

from weaver import paths


def get(slug: str) -> dict | None:
    f = paths.registry_dir() / f"{slug}.json"
    return json.loads(f.read_text()) if f.is_file() else None


def put(slug: str, record: dict) -> dict:
    d = paths.registry_dir()
    d.mkdir(parents=True, exist_ok=True)
    old = get(slug) or {}
    record = {**old, **record, "updated": int(time.time())}
    (d / f"{slug}.json").write_text(json.dumps(record, indent=1))
    return record


def passing(slug: str) -> dict | None:
    """A finished port whose package still exists and whose gates passed."""
    r = get(slug)
    if not r or not r.get("package"):
        return None
    from pathlib import Path
    if not (Path(r["package"]) / "model.json").is_file():
        return None
    return r if r.get("gates") == "PASS" else None
