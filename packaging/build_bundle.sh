#!/bin/bash
# Build the single-file weaver bundle: packaging/out/weaver-<version>-linux-x64
#   python/   relocatable CPython (python-build-standalone) + hrx-loom[hf] + the tools deps + weaver
#   pi/       the standalone Pi release
#   skills/   the pinned loom-skills (vendor/loom-skills)
#   library/  hrx-loom-library at the catalog revision the SDK names (no git needed at run time)
# Prebuilt pieces only, nothing compiled (like lemonade-sdk/vllm-rocm). Every download is sha-checked.
# usage: packaging/build_bundle.sh [OUT_DIR=packaging/out]
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd); repo=$(cd "$here/.." && pwd)
out=$(mkdir -p "${1:-$here/out}" && cd "${1:-$here/out}" && pwd)
. "$here/versions.env"
version=$(cd "$repo" && python3 -c "import re;print(re.search(r'__version__ = \"(.+)\"', open('weaver/__init__.py').read())[1])")
[ -f "$repo/vendor/loom-skills/skills/loom/SKILL.md" ] || { echo "run: git submodule update --init"; exit 1; }

dl="$out/downloads"; mkdir -p "$dl"
fetch() {  # URL FILE SHA256
  if [ ! -f "$dl/$2" ]; then curl -fsSL --retry 3 -o "$dl/$2.tmp" "$1"; mv "$dl/$2.tmp" "$dl/$2"; fi
  echo "$3  $dl/$2" | sha256sum -c --quiet - || { echo "checksum mismatch: $2"; rm -f "$dl/$2"; exit 1; }
}
fetch "https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_TAG/$PBS_ASSET" "$PBS_ASSET" "$PBS_SHA256"
fetch "https://github.com/earendil-works/pi/releases/download/v$PI_VERSION/$PI_ASSET" "$PI_ASSET" "$PI_SHA256"

root="$out/root"; rm -rf "$root"; mkdir -p "$root"
echo "== python"
tar -xzf "$dl/$PBS_ASSET" -C "$root"            # -> root/python
py="$root/python/bin/python3"
"$py" -m pip install -q --no-warn-script-location --upgrade pip
echo "== hrx-loom $HRX_LOOM_VERSION + weaver"
"$py" -m pip install -q --no-warn-script-location "$repo"     # setup.py vendors the skills into the package
echo "== pi $PI_VERSION"
tar -xzf "$dl/$PI_ASSET" -C "$root"             # -> root/pi/pi
echo "== skills"
cp -a "$repo/vendor/loom-skills/skills" "$root/skills"
find "$root/skills" -name __pycache__ -prune -exec rm -rf {} +
echo "== library"
rev=$("$py" - <<'EOF'
import importlib.metadata as md, json
d = md.distribution("hrx-loom")
f = next(f for f in d.files if f.name == "loom.json" and f.parent.name.endswith(".dist-info"))
print((json.loads(open(d.locate_file(f)).read()).get("catalog") or "").rpartition("@")[2])
EOF
)
[ -n "$rev" ] || { echo "the SDK names no catalog revision"; exit 1; }
curl -fsSL "https://codeload.github.com/ROCm/hrx-loom-library/tar.gz/$rev" | tar -xzf - -C "$root"
mv "$root"/hrx-loom-library-* "$root/library"
echo "$rev" > "$root/library/.revision"

# Trim: caches and tests the runtime never uses.
find "$root/python" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$root/python/lib/python3.12/test" "$root/python/lib/python3.12/idlelib" "$root/python/lib/python3.12/tkinter" \
       "$root/python/lib/python3.12/ensurepip"
cat > "$root/BUNDLE" <<EOF
weaver $version
python $PBS_ASSET
hrx-loom $HRX_LOOM_VERSION
library $rev
pi $PI_VERSION
skills $(git -C "$repo/vendor/loom-skills" rev-parse HEAD)
built $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

echo "== payload"
payload="$out/payload.tar.gz"
tar -C "$root" -czf "$payload" .
sha=$(sha256sum "$payload" | cut -c1-16)
bin="$out/weaver-$version-linux-x64"
sed -e "s/@VERSION@/$version/" -e "s/@PAYLOAD_SHA@/$sha/" "$here/stub.sh" > "$bin"
cat "$payload" >> "$bin"
chmod +x "$bin"
rm -f "$payload"
echo "built $bin ($(du -h "$bin" | cut -f1))"
