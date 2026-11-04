"""weaver doctor: can this machine run weaver? Each failed check prints its fix."""
import os
import shutil
from pathlib import Path

from weaver import jobs, paths, pi

MIN_GLIBC = (2, 39)          # hrx-loom wheels: Linux x86-64 glibc >= 2.39
MIN_FREE_GIB = 40            # the agent model (17 GB, unless already in the HF cache) + a port's work
HOST_TOOLS = ["bash", "curl", "ss", "pgrep", "unzip", "tar"]


def _glibc() -> tuple[int, ...] | None:
    try:
        v = os.confstr("CS_GNU_LIBC_VERSION").split()[1]
        return tuple(int(x) for x in v.split(".")[:2])
    except (ValueError, OSError, IndexError, AttributeError):
        return None


def _gfx() -> list[str]:
    out = []
    for f in Path("/sys/class/kfd/kfd/topology/nodes").glob("*/properties"):
        for line in f.read_text().splitlines():
            if line.startswith("gfx_target_version"):
                v = int(line.split()[1])
                if v:
                    out.append(f"gfx{v // 10000}{(v // 100) % 100:x}{v % 100:x}")
    return out


def _gtt_gib() -> float:
    best = 0
    for f in Path("/sys/class/drm").glob("card*/device/mem_info_gtt_total"):
        try:
            best = max(best, int(f.read_text()))
        except (OSError, ValueError):
            pass
    return best / 2**30


def checks() -> list[tuple[str, bool, str, str]]:
    """(name, ok, detail, fix)"""
    r = []
    g = _glibc()
    r.append(("glibc", bool(g and g >= MIN_GLIBC), ".".join(map(str, g)) if g else "unknown",
              "hrx-loom needs glibc >= 2.39 (Ubuntu 24.04 or newer)"))
    kfd = Path("/dev/kfd")
    r.append(("/dev/kfd", kfd.exists() and os.access(kfd, os.R_OK | os.W_OK), "present" if kfd.exists() else "missing",
              "install the amdgpu driver and add yourself to the render and video groups"))
    gfx = _gfx()
    r.append(("AMD GPU", bool(gfx), ", ".join(sorted(set(gfx))) or "none found",
              "weaver needs an AMD GPU (developed on gfx1151, Strix Halo)"))
    ram = jobs.ram_gib()
    r.append(("memory", ram >= 60, f"{ram:.0f} GiB RAM, {_gtt_gib():.0f} GiB GTT",
              "the 27B agent model needs about 42 GiB; 64 GB of RAM is the minimum, 128 GB recommended"))
    home = paths.home()
    probe = home if home.exists() else Path.home()
    free = shutil.disk_usage(probe).free / 2**30
    r.append(("disk", free >= MIN_FREE_GIB, f"{free:.0f} GiB free at {probe}",
              f"free at least {MIN_FREE_GIB} GiB, or set WEAVER_HOME to a bigger disk"))
    missing = [t for t in HOST_TOOLS if not shutil.which(t)]
    r.append(("host tools", not missing, "all present" if not missing else "missing: " + " ".join(missing),
              "install them with your package manager (e.g. apt install curl iproute2 procps unzip)"))
    pb = pi.binary()
    r.append(("pi", True, str(pb) if pb else f"not installed yet (`weaver init` fetches Pi {pi.PI_VERSION})", ""))
    return r


def run() -> int:
    bad = 0
    for name, ok, detail, fix in checks():
        print(f"  {'ok ' if ok else 'FAIL'}  {name:11s} {detail}")
        if not ok:
            bad += 1
            print(f"         -> {fix}")
    print("weaver doctor:", "all checks passed" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0
