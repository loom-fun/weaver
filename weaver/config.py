"""config.toml: agent model, ports, memory reservation, defaults. Written by `weaver init`."""
import tomllib
from dataclasses import asdict, dataclass, field

from weaver import paths

# The agent model: loom-fun's Qwen3.8-27B Q4_K_M package, MTP speculative decoding (3 proposals)
# to the full 262k context. Pinned to a revision so a release never changes its agent underneath.
AGENT_CARD_REPO = "loom-fun/bartowski-Qwen3.8-27B-loom"
AGENT_CARD_REVISION = "15e5509c42d165aab0fb0b68d55092733e78b220"


@dataclass
class AgentConfig:
    card: str = f"https://huggingface.co/{AGENT_CARD_REPO}/resolve/{AGENT_CARD_REVISION}/model.json"
    model_id: str = "qwen3.8-27b"
    port: int = 8079
    # The card's 45 GB sizes 4 concurrent rows; weaver's sessions use one or two. 32 GB served a
    # 100,130-token prompt correctly (M0) and leaves 43.8 GiB of the 60 % budget to the model being ported.
    memory_bytes: int = 32_000_000_000
    thinking: str = "medium"   # Pi thinking level for weaver's own sessions
    # What Pi is told about the agent model. Pi compacts at contextWindow - 16k, so this bounds every
    # prompt; it must fit the KV pool memory_bytes leaves (32 GB served a 100k-token prompt). With the
    # model's own 262k, long gemma4 E4B sessions on DevLab grew past the pool (HTTP 503 "insufficient
    # completion capacity" after 313 tool calls) or into 6-min timeouts on uncached prefills. At 64k Pi
    # compacts near 48k: an uncached prefill takes ~3 min.
    context_window: int = 65536
    max_tokens: int = 8192


@dataclass
class Defaults:
    serve_port: int = 8080
    ui_port: int = 8090
    port_port: int = 8160          # port.sh / gates serving port for the model being ported
    # Agent-driven runs are slower than the Claude Code trials e2e_budget.py was calibrated on.
    agent_slowdown: float = 3.0


@dataclass
class Config:
    agent: AgentConfig = field(default_factory=AgentConfig)
    defaults: Defaults = field(default_factory=Defaults)

    @property
    def reserved_gib(self) -> float:
        """Memory the agent server holds: other servers must fit next to it."""
        return round(self.agent.memory_bytes / 2**30, 1)


def load() -> Config:
    cfg = Config()
    f = paths.config_file()
    if f.is_file():
        data = tomllib.loads(f.read_text())
        for k, v in data.get("agent", {}).items():
            if hasattr(cfg.agent, k):
                setattr(cfg.agent, k, v)
        for k, v in data.get("defaults", {}).items():
            if hasattr(cfg.defaults, k):
                setattr(cfg.defaults, k, v)
    return cfg


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(cfg: Config) -> None:
    lines = []
    for section, obj in (("agent", cfg.agent), ("defaults", cfg.defaults)):
        lines.append(f"[{section}]")
        lines += [f"{k} = {_toml_value(v)}" for k, v in asdict(obj).items()]
        lines.append("")
    f = paths.config_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(lines))
