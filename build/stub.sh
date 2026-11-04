#!/bin/sh
# weaver self-extracting launcher. A gzip'd tar of the bundle follows the __WEAVER_PAYLOAD__ line.
# First run (or a new version): unpack to $WEAVER_HOME/<version>/ (default ~/.local/share/weaver),
# then exec the bundled interpreter. Needs only sh, tail, tar and gzip.
set -eu
VERSION="@VERSION@"
PAYLOAD_SHA="@PAYLOAD_SHA@"
home="${WEAVER_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/weaver}"
dest="$home/$VERSION"
self="$0"
case "$self" in */*) ;; *) self=$(command -v -- "$self");; esac
if [ ! -f "$dest/.complete" ] || [ "$(cat "$dest/.complete")" != "$PAYLOAD_SHA" ]; then
  echo "weaver: unpacking $VERSION to $dest (first run)..." >&2
  rm -rf "$dest.partial"
  mkdir -p "$dest.partial"
  line=$(awk '/^__WEAVER_PAYLOAD__$/ { print NR + 1; exit 0 }' "$self")
  tail -n +"$line" "$self" | tar -xzf - -C "$dest.partial"
  rm -rf "$dest"
  mv "$dest.partial" "$dest"
  printf '%s' "$PAYLOAD_SHA" > "$dest/.complete"     # written last: an interrupted unpack is redone
fi
export WEAVER_HOME="$home" WEAVER_BUNDLE="$dest"
exec "$dest/python/bin/python3" -I -m weaver "$@"
exit 1
__WEAVER_PAYLOAD__
