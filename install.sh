#!/usr/bin/env bash
# weaver installer.
#
#   curl -fsSL https://github.com/loom-fun/weaver/releases/latest/download/install.sh | bash
#
# Downloads the weaver release tarball, checks its sha256 against the release's SHA256SUMS, unpacks it
# to $WEAVER_HOME/<version>/ (default ~/.local/share/weaver), links `weaver` into a bin directory on
# PATH, and offers to run `weaver init` (the local agent model). Re-running it upgrades in place.
#
# Options (after `bash -s --` when piped):
#   --version X       install vX instead of the latest release
#   --init / --no-init  run `weaver init` without asking / don't offer it
#   --uninstall       remove weaver (asks first; --yes skips the question)
# Environment:
#   WEAVER_HOME            install root and all weaver state (default ~/.local/share/weaver)
#   WEAVER_BIN_DIR         where the `weaver` link goes (default: ~/.local/bin, or ~/bin if that is on PATH)
#   WEAVER_RELEASE_URL     where the release assets are (default: the GitHub release); file:// works
#   WEAVER_NO_MODIFY_PATH  set to 1 to leave shell rc files alone
#   WEAVER_NO_ANIM         set to 1 for plain output; NO_COLOR is honored
#
# Everything is inside main(), called on the last line, so a truncated download runs nothing.

weaver_install_main() {
set -euo pipefail

REPO="loom-fun/weaver"
ASSET="weaver-linux-x64.tar.gz"
version=""; init_mode=ask; uninstall=0; yes=0
while [ $# -gt 0 ]; do
  case "$1" in
    --version) version="${2#v}"; shift ;;
    --version=*) version="${1#--version=}"; version="${version#v}" ;;
    --init) init_mode=yes ;;
    --no-init) init_mode=no ;;
    --uninstall) uninstall=1 ;;
    --yes|-y) yes=1 ;;
    -h|--help) sed -n '2,21p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null | sed 's/^# \{0,1\}//'
               echo "see https://github.com/$REPO#install"; return 0 ;;
    *) echo "weaver installer: unknown option $1" >&2; return 2 ;;
  esac
  shift
done
case "${WEAVER_INIT:-}" in 1|yes|true) init_mode=yes ;; 0|no|false) init_mode=no ;; esac

home="${WEAVER_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/weaver}"

# ---- terminal ------------------------------------------------------------------------------------
tty_out=0; [ -t 1 ] && tty_out=1
tty_in=0; if ( : <>/dev/tty ) 2>/dev/null; then tty_in=1; fi      # stdin is the pipe under curl | bash
utf8=0
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in *UTF-8*|*utf-8*|*UTF8*|*utf8*) utf8=1 ;; esac
color=0; [ $tty_out = 1 ] && [ -z "${NO_COLOR:-}" ] && [ "${TERM:-dumb}" != dumb ] && color=1
E=$'\033'
if [ $color = 1 ]; then
  B="$E[1m" D="$E[2m" R="$E[0m" RED="$E[31m" GRN="$E[32m" YEL="$E[33m" CYN="$E[36m"
else B="" D="" R="" RED="" GRN="" YEL="" CYN=""; fi
if [ $utf8 = 1 ]; then OK="✓" BAD="✗" WARN="!" SPIN=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
else OK="ok" BAD="x" WARN="!" SPIN=('-' '\' '|' '/'); fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '  %s%s%s %s\n' "$GRN" "$OK" "$R" "$*"; }
warn() { printf '  %s%s%s %s\n' "$YEL" "$WARN" "$R" "$*"; }
die()  { loom_stop; printf '  %s%s %s%s\n' "$RED" "$BAD" "$*" "$R" >&2; exit 1; }
tilde() { case "$1" in "$HOME"/*) printf '~/%s' "${1#"$HOME"/}" ;; *) printf '%s' "$1" ;; esac; }

ask() {  # ask "question" default(y|n) -> 0 for yes
  local q="$1" def="$2" a=""
  if [ $tty_in = 0 ]; then [ "$def" = y ]; return; fi
  printf '%s %s ' "$q" "$([ "$def" = y ] && echo "[Y/n]" || echo "[y/N]")" > /dev/tty
  IFS= read -r a < /dev/tty || a=""
  case "$a" in "") [ "$def" = y ] ;; [Yy]*) return 0 ;; *) return 1 ;; esac
}

# ---- the loom (top-right corner while downloading and unpacking) ---------------------------------
# A little loom: the shuttle carries a coloured weft thread across the warp, the reed beats it in, the
# sheds swap, and the cloth grows a stripe per pass. Drawn in the foreground between progress polls
# (no background drawer, so nothing interleaves), with cursor save/restore around each frame.
anim=0; LW=20; lrow=1; lcol=1; lpos=0; ldir=1; lpass=0; lbeat=0; lcloth=(); llabel=""
loom_start() {
  [ $tty_out = 1 ] && [ $color = 1 ] && [ -z "${WEAVER_NO_ANIM:-}" ] || return 0
  local rows=0 cols=0
  read -r rows cols < <(stty size < /dev/tty 2>/dev/null || echo "0 0") || true
  [ "${cols:-0}" -gt 0 ] || cols=${COLUMNS:-0}
  [ "${rows:-0}" -gt 0 ] || rows=${LINES:-0}
  [ "$cols" -ge 72 ] && [ "$rows" -ge 18 ] || return 0
  anim=1; lcol=$((cols - LW - 3)); lrow=1
  WEFT=(209 221 150 116 74 141 211)                 # coral gold lime turquoise blue violet pink
  lcloth=("${WEFT[4]}" "${WEFT[3]}" "${WEFT[2]}")
  if [ $utf8 = 1 ]; then
    G_TL="╭" G_TR="╮" G_BL="╰" G_BR="╯" G_H="━" G_V="┃" G_UP="│" G_DN="╵" G_UP2="╷" G_WEFT="━"
    G_SHR="▶" G_SHL="◀" G_REED="┊" G_BEAT="▀" G_C1="▀" G_C2="▄"
  else
    G_TL="+" G_TR="+" G_BL="+" G_BR="+" G_H="=" G_V="|" G_UP="|" G_DN="'" G_UP2="." G_WEFT="-"
    G_SHR=">" G_SHL="<" G_REED=":" G_BEAT="=" G_C1="#" G_C2="="
  fi
  printf '%s' "$E[?25l"
}
loom_frame() {
  [ $anim = 1 ] || return 0
  local f="" i r=$lrow c=$lcol wood="$E[38;5;137m" warp="$E[38;5;252m" dim="$E[38;5;242m"
  local weft="$E[38;5;${WEFT[$((lpass % ${#WEFT[@]}))]}m" lab pad l rr
  f+="$E""7"
  f+="$E[$r;${c}H$wood$G_TL$(rep "$G_H" $LW)$G_TR$R"; r=$((r + 1))
  # two shed rows: one set of warp threads up, the other down; they swap every pass
  for rr in 0 1; do
    f+="$E[$r;${c}H$wood$G_V$R"
    for ((i = 0; i < LW; i++)); do
      if [ $((i % 2)) = 1 ]; then f+=" "; continue; fi
      if [ $(((i / 2 + lpass + rr) % 2)) = 0 ]; then f+="$warp$G_UP"; else f+="$dim$([ $rr = 0 ] && echo "$G_UP2" || echo "$G_DN")"; fi
    done
    f+="$R$wood$G_V$R"; r=$((r + 1))
  done
  # the shuttle row: weft laid behind the shuttle, bare warp ahead of it
  f+="$E[$r;${c}H$wood$G_V$R"
  for ((i = 0; i < LW; i++)); do
    if [ $i = $lpos ]; then f+="$B$weft$([ $ldir = 1 ] && echo "$G_SHR" || echo "$G_SHL")$R"
    elif { [ $ldir = 1 ] && [ $i -lt $lpos ]; } || { [ $ldir = -1 ] && [ $i -gt $lpos ]; }; then f+="$weft$G_WEFT"
    elif [ $((i % 2)) = 0 ]; then f+="$warp$G_UP"; else f+=" "; fi
  done
  f+="$R$wood$G_V$R"; r=$((r + 1))
  # the reed, which beats the new pick into the cloth
  f+="$E[$r;${c}H$wood$G_V"
  if [ $lbeat -gt 0 ]; then f+="$wood$(rep "$G_BEAT" $LW)"; else f+="$dim$(rep "$G_REED" $LW)"; fi
  f+="$R$wood$G_V$R"; r=$((r + 1))
  # the cloth: newest pick on top, a woven over/under texture in each pick's colour
  for ((l = 0; l < 3; l++)); do
    f+="$E[$r;${c}H$wood$G_V$E[38;5;${lcloth[$l]}m"
    for ((i = 0; i < LW; i++)); do
      if [ $(((i + lpass + l) % 2)) = 0 ]; then f+="$G_C1"; else f+="$G_C2"; fi
    done
    f+="$R$wood$G_V$R"; r=$((r + 1))
  done
  lab="${llabel:0:$((LW - 2))}"; [ -n "$lab" ] && lab=" $lab "
  pad=$(((LW - ${#lab}) / 2))
  f+="$E[$r;${c}H$wood$G_BL$(rep "$G_H" $pad)$R$B$lab$R$wood$(rep "$G_H" $((LW - pad - ${#lab})))$G_BR$R"
  f+="$E""8"
  printf '%s' "$f"
  # advance
  if [ $lbeat -gt 0 ]; then lbeat=$((lbeat - 1)); return 0; fi
  lpos=$((lpos + ldir))
  if [ $lpos -ge $LW ] || [ $lpos -lt 0 ]; then
    lcloth=("${WEFT[$((lpass % ${#WEFT[@]}))]}" "${lcloth[0]}" "${lcloth[1]}")
    lpass=$((lpass + 1)); ldir=$((-ldir)); lbeat=2
    if [ $ldir = 1 ]; then lpos=0; else lpos=$((LW - 1)); fi
  fi
}
loom_stop() {
  [ $anim = 1 ] || return 0
  loom_frame; anim=0; printf '%s' "$R$E[?25h"
}
rep() { local s="" n=$2; while [ $n -gt 0 ]; do s+="$1"; n=$((n - 1)); done; printf '%s' "$s"; }

# Run a command in the background, animating and showing a status line until it exits.
# usage: with_progress "label" STATUS_FN -- cmd args...   (STATUS_FN prints the right-hand detail)
with_progress() {
  local label="$1" detail_fn="$2" k=0 pid rc
  shift 3
  "$@" & pid=$!
  BGPID=$pid
  while kill -0 $pid 2>/dev/null; do
    if [ $tty_out = 1 ]; then
      clr
      printf '  %s%s%s %s %s%s%s' "$CYN" "${SPIN[$((k % ${#SPIN[@]}))]}" "$R" "$label" "$D" "$($detail_fn)" "$R"
      loom_frame
    fi
    k=$((k + 1)); sleep 0.06
  done
  if wait $pid; then rc=0; else rc=$?; fi
  BGPID=""
  [ $tty_out = 1 ] && clr
  return $rc
}
no_detail() { :; }
# Clear the status line. Beside the loom, blank only up to it: erasing to the end of the line would
# take a row of the loom with it until the next frame.
clr() { if [ $anim = 1 ]; then printf '\r%*s\r' $((lcol - 2)) ''; else printf '\r%s' "$E[K"; fi; }

BGPID=""; work=""
cleanup() {
  [ -n "$BGPID" ] && kill "$BGPID" 2>/dev/null
  [ -n "$work" ] && rm -rf "$work"
  [ $tty_out = 1 ] && printf '%s' "$R$E[?25h"
  return 0
}
trap cleanup EXIT
trap 'cleanup; printf "\n  interrupted\n"; trap - EXIT; exit 130' INT TERM

# ---- uninstall -----------------------------------------------------------------------------------
if [ $uninstall = 1 ]; then
  link=$(command -v weaver 2>/dev/null || true)
  say "This removes $(tilde "$home") (the weaver runtime, the agent model package, ports and logs)"
  [ -n "$link" ] && [ -L "$link" ] && say "and the link $(tilde "$link"). Your Hugging Face cache is kept."
  if [ $yes = 1 ] || { [ $tty_in = 1 ] && ask "Remove weaver?" n; }; then
    "$home"/current/bin/weaver agent-server stop >/dev/null 2>&1 || true
    [ -n "$link" ] && [ -L "$link" ] && case "$(readlink -f "$link")" in "$home"/*) rm -f "$link" ;; esac
    rm -rf "$home"; ok "weaver removed"
  else say "Nothing removed."; fi
  return 0
fi

# ---- preflight -----------------------------------------------------------------------------------
if [ $tty_out = 1 ] && [ $color = 1 ] && [ -z "${WEAVER_NO_ANIM:-}" ]; then printf '%s' "$E[2J$E[H"; fi
printf '\n  %sweaver%s %sinstaller%s\n' "$B" "$R" "$D" "$R"
printf '  %sHugging Face LLMs to fast local AMD Loom models.%s\n\n' "$D" "$R"
loom_start

[ "$(uname -s)" = Linux ] || die "weaver needs Linux (Windows will follow)."
[ "$(uname -m)" = x86_64 ] || die "weaver needs x86-64 (this is $(uname -m))."
for t in curl tar gzip sha256sum readlink; do command -v $t >/dev/null || die "missing $t (install it and run this again)"; done
glibc=$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')
if [ -n "$glibc" ]; then
  [ "$(printf '%s\n2.39\n' "$glibc" | sort -V | head -1)" = 2.39 ] \
    || die "weaver needs glibc 2.39 or newer (Ubuntu 24.04+); this system has $glibc."
  ok "Linux x86-64, glibc $glibc"
else warn "could not read the glibc version; weaver needs 2.39 or newer"; fi
if [ -e /dev/kfd ]; then ok "AMD GPU (/dev/kfd)"
else warn "no /dev/kfd: weaver installs, but needs an AMD GPU to run models"; fi

# ---- download ------------------------------------------------------------------------------------
if [ -n "${WEAVER_RELEASE_URL:-}" ]; then base="${WEAVER_RELEASE_URL%/}"
elif [ -n "$version" ]; then base="https://github.com/$REPO/releases/download/v$version"
else
  base="https://github.com/$REPO/releases/latest/download"
  tag=$(curl -fsSIL -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest" 2>/dev/null || true)
  case "$tag" in */tag/v*) version="${tag##*/tag/v}" ;; esac
fi
mkdir -p "$home"
work=$(mktemp -d "$home/.install.XXXXXX")
tarball="$work/$ASSET"
total=$( { curl -fsSIL "$base/$ASSET" 2>/dev/null || true; } | tr -d '\r' | awk 'tolower($1)=="content-length:"{n=$2} END{print n+0}')
mb() { echo $(($1 / 1048576)); }
dl_detail() {
  local n; n=$(stat -c %s "$tarball" 2>/dev/null || echo 0)
  if [ "${total:-0}" -gt 0 ]; then printf '%3d%%  %d/%d MB' $((n * 100 / total)) "$(mb "$n")" "$(mb "$total")"
  else printf '%d MB' "$(mb "$n")"; fi
}
what="weaver${version:+ $version}"
llabel="spinning"
with_progress "downloading $what" dl_detail -- curl -fsSL --retry 3 -o "$tarball" "$base/$ASSET" \
  || die "download failed: $base/$ASSET"
curl -fsSL --retry 3 -o "$work/SHA256SUMS" "$base/SHA256SUMS" || die "download failed: $base/SHA256SUMS"
want=$(awk -v f="$ASSET" '$2==f || $2=="*"f {print $1}' "$work/SHA256SUMS")
[ -n "$want" ] || die "SHA256SUMS has no entry for $ASSET"
got=$(sha256sum "$tarball" | cut -d' ' -f1)
[ "$want" = "$got" ] || die "sha256 mismatch for $ASSET (expected $want, got $got)"
ok "downloaded $what ($(mb "$(stat -c %s "$tarball")") MB, sha256 ok)"

# ---- unpack --------------------------------------------------------------------------------------
llabel="weaving"
mkdir -p "$work/root"
with_progress "unpacking" no_detail -- tar -xzf "$tarball" -C "$work/root" || die "unpacking failed"
[ -x "$work/root/bin/weaver" ] && [ -f "$work/root/BUNDLE" ] || die "$ASSET is not a weaver bundle"
v=$(awk '$1=="weaver"{print $2; exit}' "$work/root/BUNDLE")
[ -n "$v" ] || die "$ASSET names no version"
dest="$home/$v"
if [ -d "$dest" ]; then rm -rf "$dest.old"; mv "$dest" "$dest.old"; fi
mv "$work/root" "$dest"
rm -rf "$dest.old"
ln -sfn "$v" "$home/current"
"$dest/bin/weaver" --version >/dev/null 2>&1 || die "the installed weaver does not run ($dest/bin/weaver --version)"
# older versions: removed unless something is still running from them
for old in "$home"/[0-9]*; do
  [ -d "$old" ] && [ "$old" != "$dest" ] && [ -f "$old/BUNDLE" ] || continue
  if pgrep -f "$old/python" >/dev/null 2>&1; then warn "kept $(tilde "$old") (still running)"
  else rm -rf "$old"; fi
done
llabel="$OK woven"
loom_stop
ok "installed weaver $v in $(tilde "$dest")"

# ---- link onto PATH ------------------------------------------------------------------------------
on_path() { case ":$PATH:" in *":$1:"*) return 0 ;; esac; return 1; }
bindir="${WEAVER_BIN_DIR:-}"
if [ -z "$bindir" ]; then
  bindir="$HOME/.local/bin"
  for d in "$HOME/.local/bin" "$HOME/bin" "$HOME/.bin"; do if on_path "$d"; then bindir="$d"; break; fi; done
fi
mkdir -p "$bindir"
if [ -e "$bindir/weaver" ] && ! [ -L "$bindir/weaver" ]; then
  # the old single-file weaver (<= 0.1.3) lived here; it is replaced by the link
  if head -c 2 "$bindir/weaver" 2>/dev/null | grep -q '#!' && grep -qa '__WEAVER_PAYLOAD__' "$bindir/weaver"; then
    rm -f "$bindir/weaver"
  else die "$(tilde "$bindir/weaver") exists and is not weaver's; move it or set WEAVER_BIN_DIR"; fi
fi
ln -sfn "$home/current/bin/weaver" "$bindir/weaver"
ok "linked $(tilde "$bindir/weaver")"
path_note=""
if ! on_path "$bindir"; then
  if [ -z "${WEAVER_NO_MODIFY_PATH:-}" ]; then
    case "$(basename "${SHELL:-bash}")" in
      zsh)  rc="$HOME/.zshrc";  line="export PATH=\"$bindir:\$PATH\"" ;;
      fish) rc="$HOME/.config/fish/config.fish"; line="fish_add_path \"$bindir\"" ;;
      bash) rc="$HOME/.bashrc"; line="export PATH=\"$bindir:\$PATH\"" ;;
      *)    rc="$HOME/.profile"; line="export PATH=\"$bindir:\$PATH\"" ;;
    esac
    mkdir -p "$(dirname "$rc")"
    if ! grep -qsF "$line" "$rc"; then printf '\n# weaver\n%s\n' "$line" >> "$rc"; fi
    ok "added $(tilde "$bindir") to PATH in $(tilde "$rc")"
  fi
  path_note="export PATH=\"$bindir:\$PATH\""
fi

# ---- init ----------------------------------------------------------------------------------------
W="$bindir/weaver"
ran_init=0; init_rc=0
have_agent=0; [ -f "$home/agent/qwen3.8-27b/model.json" ] && have_agent=1
say ""
if [ $init_mode = no ]; then :
elif [ $init_mode = yes ] || { [ $tty_in = 1 ] && [ $have_agent = 0 ] \
    && ask "  Set up the local agent now? ${D}(weaver init: fetches Qwen3.8-27B, ~17 GB, and starts it)${R}" y; }; then
  say ""
  ran_init=1
  if [ $tty_in = 1 ]; then "$W" init < /dev/tty || init_rc=$?; else "$W" init < /dev/null || init_rc=$?; fi
  say ""
fi

if [ $init_rc != 0 ]; then
  printf '  %s%s weaver init failed (exit %s).%s Fix what it reported, then run: %sweaver init%s\n' "$RED" "$BAD" "$init_rc" "$R" "$B" "$R"
  return $init_rc
fi
printf '  %sweaver %s is ready.%s\n\n' "$B" "$v" "$R"
if [ -n "$path_note" ]; then
  printf '  This terminal does not have %s on PATH yet. Open a new one, or run:\n    %s%s%s\n\n' "$(tilde "$bindir")" "$CYN" "$path_note" "$R"
fi
if [ $ran_init = 0 ] && [ $have_agent = 0 ]; then
  printf '  First, set up the local agent (once):   %sweaver init%s\n' "$CYN" "$R"
fi
printf '  Serve a model:   %sweaver serve huggingface://unsloth/Qwen3.5-0.8B-GGUF%s\n' "$CYN" "$R"
printf '  Everything else: %sweaver --help%s\n\n' "$CYN" "$R"
}

weaver_install_main "$@"
