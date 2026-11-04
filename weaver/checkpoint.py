"""Parse the CHECKPOINT argument.

Accepted forms:
  huggingface://org/repo[:file.gguf]   hf://org/repo[:file.gguf]
  https://huggingface.co/org/repo[/...]
  org/repo[:file.gguf]
  /path/to/local/dir-or-file.gguf
"""
import re
from dataclasses import dataclass
from pathlib import Path

_REPO = re.compile(r"^[A-Za-z0-9][\w.-]*/[\w.-]+$")


@dataclass(frozen=True)
class Checkpoint:
    repo: str | None          # org/repo on Hugging Face
    file: str | None = None   # a specific GGUF inside the repo
    local: Path | None = None # a local GGUF or directory

    @property
    def slug(self) -> str:
        """Filesystem-safe name: org__repo (local: the file or dir stem)."""
        if self.repo:
            return self.repo.replace("/", "__")
        return re.sub(r"[^\w.-]", "_", self.local.stem if self.local.suffix else self.local.name)

    @property
    def spec(self) -> str:
        """The ORG/REPO[:FILE] form port.sh takes."""
        if self.repo:
            return f"{self.repo}:{self.file}" if self.file else self.repo
        return str(self.local)

    def __str__(self) -> str:
        return self.spec


def parse(text: str) -> Checkpoint:
    t = text.strip()
    for prefix in ("huggingface://", "hf://"):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    else:
        m = re.match(r"^https?://(?:www\.)?huggingface\.co/(.+)$", t)
        if m:
            parts = m.group(1).split("/")
            if len(parts) < 2:
                raise ValueError(f"not a model URL: {text}")
            repo = "/".join(parts[:2])
            # .../resolve/<rev>/<file>.gguf or .../blob/<rev>/<file>.gguf
            file = parts[-1] if len(parts) > 4 and parts[-1].endswith(".gguf") else None
            return Checkpoint(repo=repo, file=file)
        p = Path(t).expanduser()
        if t.startswith((".", "/", "~")) or p.exists():
            if not p.exists():
                raise ValueError(f"no such file or directory: {text}")
            return Checkpoint(repo=None, local=p.resolve())
    repo, _, file = t.partition(":")
    repo = repo.strip("/")
    if not _REPO.match(repo):
        raise ValueError(f"expected huggingface://org/repo[:file.gguf], got {text!r}")
    if file and not file.endswith(".gguf"):
        raise ValueError(f"the file after ':' must be a .gguf, got {file!r}")
    return Checkpoint(repo=repo, file=file or None)
