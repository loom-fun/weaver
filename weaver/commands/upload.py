"""weaver upload CHECKPOINT HF-ORG: publish a finished port the way loom-fun publishes packages.

The repo holds the programs, model.json and model.json.lock, never the weights: each parameter file
becomes `origin: "@<origin>/<file>"` with an `origins` entry naming the Hugging Face repo and the
exact revision the port was validated with (read from the HF cache path the package links to).
`loom-fetch --update-lock --lock-scope all` writes the publication lock (sizes + sha256).
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from weaver import jobs, paths, registry
from weaver.checkpoint import Checkpoint
from weaver.config import Config
from weaver.runtime import Runtime

HF_CACHE_RE = re.compile(r"models--(?P<org>[^/]+?)--(?P<name>[^/]+)/snapshots/(?P<rev>[0-9a-f]{40})/(?P<file>.+)$")
KEEP = ["model.json", "tokenizer.json", "weights.loom", "llm_config.json", "program"]


def hf_origin(path: Path) -> tuple[str, str, str] | None:
    """(repo, revision, file) for a file reached through the HF cache (symlink chain kept)."""
    p = path
    for _ in range(4):
        m = HF_CACHE_RE.search(str(p))
        if m:
            return f"{m['org']}/{m['name']}", m["rev"], m["file"]
        if not p.is_symlink():
            break
        p = Path(p.parent, p.readlink())
    return None


def stage_package(pkg: Path, stage: Path, gguf_hint: str | None, extra_ggufs: list[str] = ()) -> dict:
    """Copy the publishable files and rewrite local parameter paths into HF origins."""
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    for name in KEEP:
        src = pkg / name
        if src.is_dir():
            shutil.copytree(src, stage / name, symlinks=False)
        elif src.exists():
            shutil.copyfile(src.resolve(), stage / name)
    card = json.loads((pkg / "model.json").read_text())
    origins: dict[str, dict] = {}
    names = {}
    hints = [h for h in [gguf_hint, *extra_ggufs] if h]
    for variant in card.get("variants", {}).values():
        for service in variant.get("services", {}).values():
            for scope, spec in (service.get("parameters") or {}).items():
                for f in spec.get("files", []):
                    local = pkg / f["path"]
                    src = next((Path(h) for h in hints if Path(h).resolve() == local.resolve()), local)
                    o = hf_origin(src)
                    if not o:
                        raise SystemExit(f"cannot tell which Hugging Face repo {f['path']} came from "
                                         f"(not reached through the HF cache): {local.resolve()}")
                    repo, rev, file = o
                    if repo not in names:   # first repo is the checkpoint, others by scope (e.g. draft)
                        key = "checkpoint" if not names else scope
                        names[repo] = key if key not in origins else f"{key}{len(names)}"
                    key = names[repo]
                    origins[key] = {"kind": "hf", "repository": repo, "revision": rev}
                    f["path"] = f"parameter/{Path(file).name}"
                    f["origin"] = f"@{key}/{file}"
    card["origins"] = {**card.get("origins", {}), **origins}
    (stage / "model.json").write_text(json.dumps(card, indent=1))
    import importlib.metadata as md
    (stage / "weaver.json").write_text(json.dumps({"hrx_loom": md.version("hrx-loom"), "built_with": "weaver"}, indent=1))
    return {"origins": origins, "model_id": card["model"]["id"], "name": card["model"].get("name")}


def lock(rt: Runtime, stage: Path) -> None:
    subprocess.run([str(rt.loom_root / ".venv" / "bin" / "loom-fetch"), str(stage / "model.json"),
                    "--update-lock", "--lock-scope", "all", "--all-variants", "--no-input"],
                   env=rt.env(), check=True)


def run_commands(repo_id: str, model_id: str, memory_bytes) -> str:
    short = re.sub(r"[^a-z0-9.]+", "-", model_id.split("/")[-1].lower()).strip("-")
    mem = f" --memory_bytes={memory_bytes}" if memory_bytes else ""
    return ("```sh\n"
            'pip install "hrx-loom[hf]"\n'
            f"loom-fetch https://huggingface.co/{repo_id}/resolve/main/model.json --materialize=copy --output={short}\n"
            f"loom-serve --model={short}/model.json --device=amdgpu{mem}\n"
            "```")


def ensure_run_commands(readme: Path, repo_id: str, model_id: str, memory_bytes) -> None:
    """The how-to-run block is what makes the card usable: it is written by weaver, not left to the
    agent (a trial's card dropped the loom-fetch line)."""
    text = readme.read_text()
    block = run_commands(repo_id, model_id, memory_bytes)
    m = re.search(r"## How to run\s*\n+```(?:sh|bash)?\n.*?```", text, re.S)
    if m:
        text = text[:m.start()] + "## How to run\n\n" + block + text[m.end():]
    else:
        text = text.rstrip() + "\n\n## How to run\n\n" + block + "\n"
    readme.write_text(text)


def check_write_access(org: str) -> str:
    from huggingface_hub import HfApi
    try:
        who = HfApi().whoami()
    except Exception as e:
        raise SystemExit(f"not logged in to Hugging Face ({e}): run `hf auth login` with a write token")
    if who.get("name") == org:
        return who["name"]
    for o in who.get("orgs") or []:
        if o.get("name") == org:
            role = o.get("roleInOrg") or ""
            if role in ("admin", "write", "contributor"):
                return who["name"]
            raise SystemExit(f"{who['name']} is a '{role}' of {org}: write access is needed")
    raise SystemExit(f"{who.get('name')} is not a member of {org}")


def repo_name(ck: Checkpoint, org: str) -> str:
    base = ck.repo.replace("/", "-") if ck.repo else ck.slug
    base = re.sub(r"-GGUF$", "", base, flags=re.I)
    return f"{org}/{base}-loom"


def run(rt: Runtime, cfg: Config, ck: Checkpoint, org: str, *, yes: bool = False, private: bool = False,
        verbose: bool = False) -> int:
    rec = registry.passing(ck.slug)
    if not rec:
        raise SystemExit(f"{ck} has no passing port: run `weaver port {ck}` first")
    if rec.get("validated") != "PASS":
        print(f"note: the full-context check hasn't passed on this port, so the card will show it as "
              f"'no evidence'. `weaver validate {ck}` runs it (it is slow: one prompt as long as the whole "
              "context).", file=sys.stderr)
    user = check_write_access(org)
    repo_id = repo_name(ck, org)
    work = paths.work_dir(ck.slug)
    stage = work / "upload"
    facts = stage_package(Path(rec["package"]), stage, rec.get("gguf"))
    lock(rt, stage)
    base = facts["origins"]["checkpoint"]["repository"]
    gguf_name = Path(rec.get("gguf") or "").name
    quant = (re.search(r"(I?Q\d[\w]*|BF16|F16|Q8_0)", gguf_name, re.I) or [None])[0] or "?"
    prompt = jobs.prompt_file("card.md").read_text().format(
        stage=stage, repo_id=repo_id, base_repo=base, gguf_name=gguf_name, quant=quant,
        facts=json.dumps({"model_id": facts["model_id"], "memory_bytes": rec.get("memory_bytes"),
                          "draft": rec.get("draft"), "gates": rec.get("gates"), "audit": rec.get("audit"),
                          "origins": facts["origins"]}),
        report=rec.get("report") or "none", template=jobs.template_file("model_card.md"))
    res = jobs.run_agent(rt, cfg, work, prompt, verbose=verbose)
    readme = stage / "README.md"
    if not readme.is_file():
        raise SystemExit(f"the agent did not write {readme} (log: {res.log})")
    ensure_run_commands(readme, repo_id, facts["model_id"], rec.get("memory_bytes"))
    for junk in (".loom-fetch.lock",):
        (stage / junk).unlink(missing_ok=True)
    files = sorted(str(p.relative_to(stage)) for p in stage.rglob("*") if p.is_file())
    print(readme.read_text())
    print(f"\n--- {len(files)} files to upload to https://huggingface.co/{repo_id} as {user}:")
    print("    " + "\n    ".join(files[:40]) + ("\n    ..." if len(files) > 40 else ""))
    if not yes:
        if not sys.stdin.isatty() or input("Publish? [y/N] ").strip().lower() not in ("y", "yes"):
            print("not published; the staged repo is in", stage)
            return 1
    from huggingface_hub import HfApi
    api = HfApi()
    api.create_repo(repo_id, exist_ok=True, private=private)
    # Replace what an earlier upload left (stale programs, lock files); other files in the repo stay.
    api.upload_folder(repo_id=repo_id, folder_path=str(stage), commit_message="Loom package (weaver)",
                      delete_patterns=["*.json", "*.lock", "*.loom", "*.md", "program/**"])
    url = f"https://huggingface.co/{repo_id}"
    registry.put(ck.slug, {"uploaded": url})
    print(f"published {url}; checking that loom-fetch resolves it...")
    subprocess.run([str(rt.loom_root / ".venv" / "bin" / "loom-fetch"), f"{url}/resolve/main/model.json",
                    "--dry-run", "--no-input", f"--output={work / 'upload-check'}"], env=rt.env(), check=True)
    print(f"OK: weaver serve huggingface://{repo_id}")
    return 0
