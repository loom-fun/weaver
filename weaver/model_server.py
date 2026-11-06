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
    preset: str | None = None      # loom-serve --preset, for a card with several


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


PREFERRED_PRESETS = ("chat", "text", "default")


def pick_preset(presets: list[str], wanted: str | None = None) -> str | None:
    """loom-fetch needs an explicit preset when a card declares several (the catalog Qwen3.8-27B has
    chat, vision, target-only, dflash2, cdna-text): the user's choice, else a text chat preset, else the
    first."""
    if wanted:
        if wanted not in presets:
            raise RuntimeError(f"preset {wanted!r} is not in this card: {', '.join(presets)}")
        return wanted
    if len(presets) <= 1:
        return None
    return next((p for p in PREFERRED_PRESETS if p in presets), presets[0])


def _fetch(rt: Runtime, source: str, out: Path, preset: str | None = None) -> tuple[Path, str | None]:
    fetch = str(rt.loom_root / ".venv" / "bin" / "loom-fetch")
    listing = subprocess.run([fetch, source, "--list", "--json", "--no-input"], env=rt.env(),
                             capture_output=True, text=True)
    try:
        presets = list((json.loads(listing.stdout).get("presets") or {}).keys())
    except ValueError:
        presets = []
    chosen = pick_preset(presets, preset)
    name = source.split("huggingface.co/")[-1].split("/resolve/")[0] if "huggingface.co/" in source else Path(source).parent.name
    jobs.log(f"downloading the Loom package {name}{f' ({chosen} preset)' if chosen else ''}; the weights come "
             "from (and stay in) your Hugging Face cache")
    p = subprocess.run([fetch, source, f"--output={out}", "--no-input", *([f"--preset={chosen}"] if chosen else [])],
                       env=rt.env())
    if p.returncode != 0:
        raise RuntimeError(f"loom-fetch failed for {source} (exit {p.returncode}; its messages are above)")
    return out / "model.json", chosen


def _weights_bytes(pkg: Path) -> int:
    d = pkg / "parameter"
    return sum(p.resolve().stat().st_size for p in d.iterdir()) if d.is_dir() else 0


def resolve(rt: Runtime, cfg: Config, ck: Checkpoint, *, allow_port: bool = True,
            confirm=None, no_spec: bool = False, verbose: bool = False, preset: str | None = None) -> Servable:
    budget = jobs.target_budget_gib(cfg, agent_running=False) * 2**30
    if ck.repo and _hf_has_model_json(ck.repo):
        mj, chosen = _fetch(rt, f"https://huggingface.co/{ck.repo}/resolve/main/model.json",
                            paths.home() / "packages" / ck.slug, preset)
        return Servable("package", mj, _memory_for_fetched(mj.parent, budget), preset=chosen)
    card = find_catalog(rt.library, ck)
    if card:
        mj, chosen = _fetch(rt, str(card), paths.home() / "packages" / ck.slug, preset)
        return Servable("catalog", mj, _memory_for_fetched(mj.parent, budget), preset=chosen)
    rec = registry.passing(ck.slug)
    if not rec:
        if not allow_port:
            raise SystemExit(f"{ck} is not ported yet: run `weaver port {ck}`")
        if confirm and not confirm(f"{ck} is not a Loom package yet. Port it now with the local agent "
                                   "(roughly 30 min to a few hours)?"):
            raise SystemExit("not ported")
        rec = jobs.port(rt, cfg, ck, no_spec=no_spec, verbose=verbose)
        if rec.get("status") != "ok" or not rec.get("package"):
            raise SystemExit(1)       # jobs.port() already said what happened and what to do
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
        servers.start(rt, name, port, s.memory_bytes, [s.model_json],
                      [f"--preset={s.preset}"] if s.preset else None)
    except RuntimeError as e:
        if s.kind in ("package", "catalog") and ("BACKEND/" in str(e) or "LINK/" in str(e)):
            import importlib.metadata as md
            raise RuntimeError(f"{e}\n\nThis {s.kind} does not compile with hrx-loom {md.version('hrx-loom')} "
                               "(the SDK in this weaver): it targets a different SDK. Port the original "
                               "GGUF checkpoint instead (weaver serve/port ORG/REPO-GGUF), or serve a package "
                               "built for this SDK (e.g. huggingface://loom-fun/bartowski-Qwen3.8-27B-loom).") from None
        raise
    st = servers.status(port) or {}
    data = st.get("data") or []
    if not data:
        raise RuntimeError(f"{name} reports no models:\n{servers.log_tail(name)}")
    return data[0]
