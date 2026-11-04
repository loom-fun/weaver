"""CHECKPOINT -> a running loom-serve. Shared by `weaver serve` and `weaver agent -m`.

Resolution order (the first that applies):
  1. a published Loom package repo (it has a model.json, e.g. huggingface://loom-fun/...-loom):
     loom-fetch it, no agent;
  2. a catalog model in hrx-loom-library (its card names this repo, or its origin checkpoint is
     this repo): loom-fetch it, no agent;
  3. a passing port in weaver's registry;
  4. otherwise: `weaver port` first (agent-driven).
"""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from weaver import jobs, paths, registry, servers
from weaver.checkpoint import Checkpoint
from weaver.config import Config
from weaver.runtime import Runtime


@dataclass
class Servable:
    kind: str            # "package" | "catalog" | "port"
    model_json: Path
    memory_bytes: int
    model_id: str | None = None


def _hf_has_model_json(repo: str) -> bool:
    try:
        from huggingface_hub import file_exists
        return file_exists(repo, "model.json")
    except Exception:
        return False


def find_catalog(library: Path | None, ck: Checkpoint) -> Path | None:
    if not library or not ck.repo or not (library / "model").is_dir():
        return None
    direct = library / "model" / ck.repo / "model.json"
    if direct.is_file():
        return direct
    for card in (library / "model").glob("*/*/model.json"):
        try:
            origins = json.loads(card.read_text()).get("origins") or {}
        except ValueError:
            continue
        if any((o or {}).get("repository") == ck.repo for o in origins.values()):
            return card
    return None


def _fetch(rt: Runtime, source: str, out: Path) -> Path:
    jobs.log(f"loom-fetch {source} -> {out}")
    subprocess.run([str(rt.loom_root / ".venv" / "bin" / "loom-fetch"), source, f"--output={out}", "--no-input"],
                   env=rt.env(), check=True)
    return out / "model.json"


def _weights_bytes(pkg: Path) -> int:
    d = pkg / "parameter"
    return sum(p.resolve().stat().st_size for p in d.iterdir()) if d.is_dir() else 0


def resolve(rt: Runtime, cfg: Config, ck: Checkpoint, *, allow_port: bool = True,
            confirm=None, no_spec: bool = False, verbose: bool = False) -> Servable:
    budget = jobs.target_budget_gib(cfg, agent_running=False) * 2**30
    if ck.repo and _hf_has_model_json(ck.repo):
        mj = _fetch(rt, f"https://huggingface.co/{ck.repo}/resolve/main/model.json",
                    paths.home() / "packages" / ck.slug)
        return Servable("package", mj, _memory_for_fetched(mj.parent, budget))
    card = find_catalog(rt.library, ck)
    if card:
        mj = _fetch(rt, str(card), paths.home() / "packages" / ck.slug)
        return Servable("catalog", mj, _memory_for_fetched(mj.parent, budget))
    rec = registry.passing(ck.slug)
    if not rec:
        if not allow_port:
            raise SystemExit(f"{ck} is not ported yet: run `weaver port {ck}`")
        if confirm and not confirm(f"{ck} is not a Loom package yet. Port it now with the local agent "
                                   "(roughly 30 min to a few hours)?"):
            raise SystemExit("not ported")
        rec = jobs.port(rt, cfg, ck, no_spec=no_spec, verbose=verbose)
        if rec.get("status") != "ok" or not rec.get("package"):
            raise SystemExit(f"the port failed: {rec.get('summary') or 'see ' + rec['work']}")
    pkg = Path(rec["package"])
    mem = int(rec.get("memory_bytes") or servers.memory_bytes_for(pkg))
    return Servable("port", pkg / "model.json", mem, rec.get("model_id"))


def _memory_for_fetched(pkg: Path, budget: int) -> int:
    """A published card states its own memory budget only in prose; size it from the files:
    weights + a KV/workspace allowance, capped at the budget."""
    w = _weights_bytes(pkg)
    want = int(w * 1.4 + 8 * 2**30)
    return int(min(max(want, w + 4 * 2**30), budget))


def serve(rt: Runtime, name: str, port: int, s: Servable) -> dict:
    """Start it and return its /v1/status model record."""
    if servers.running(name):
        servers.stop(rt, name)
    try:
        servers.start(rt, name, port, s.memory_bytes, [s.model_json])
    except RuntimeError as e:
        if s.kind in ("package", "catalog") and "BACKEND/" in str(e):
            import importlib.metadata as md
            raise RuntimeError(f"{e}\n\nThis package does not compile with hrx-loom {md.version('hrx-loom')} "
                               "(the SDK in this weaver). It was probably built with a newer SDK; port the "
                               "original checkpoint instead (weaver port ORG/REPO).") from None
        raise
    st = servers.status(port) or {}
    data = st.get("data") or []
    if not data:
        raise RuntimeError(f"{name} reports no models:\n{servers.log_tail(name)}")
    return data[0]
