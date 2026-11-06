#!/bin/sh
# weaver launcher (bin/weaver in the release tarball). The bundle is the directory above bin/; it lives
# at $WEAVER_HOME/<version>/, so WEAVER_HOME defaults to the bundle's parent. install.sh links this
# into a bin directory on PATH; it can also be run from anywhere directly.
self=$(readlink -f "$0") || self=$0
dest=$(dirname "$(dirname "$self")")
WEAVER_BUNDLE=$dest
WEAVER_HOME=${WEAVER_HOME:-$(dirname "$dest")}
export WEAVER_BUNDLE WEAVER_HOME
exec "$dest/python/bin/python3" -I -m weaver "$@"
