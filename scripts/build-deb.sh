#!/usr/bin/env bash
# Build a wayparam .deb (and optionally a signed source package for a PPA).
#
#   ./scripts/build-deb.sh            # binary .deb in ../
#   ./scripts/build-deb.sh --source   # signed source package, ready for dput
#
# Build dependencies (Debian/Ubuntu):
#   sudo apt install devscripts debhelper dh-python python3-all \
#        python3-setuptools pybuild-plugin-pyproject python3-httpx python3-pytest

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
VERSION="$(sed -n 's/^version *= *"\(.*\)"/\1/p' pyproject.toml | head -1)"
DEB_VERSION="$(dpkg-parsechangelog -S Version)"

if [[ "$DEB_VERSION" != "$VERSION"-* ]]; then
  echo "ERROR: pyproject version ($VERSION) and debian/changelog ($DEB_VERSION) disagree." >&2
  echo "       Update debian/changelog (dch -v ${VERSION}-1) before building." >&2
  exit 1
fi

# dpkg-source (3.0 quilt) needs an upstream tarball next to the source tree.
ORIG="../wayparam_${VERSION}.orig.tar.gz"
if [[ ! -f "$ORIG" ]]; then
  echo ">> creating $ORIG"
  git archive --worktree-attributes --format=tar.gz \
    --prefix="wayparam-${VERSION}/" HEAD -o "$ORIG"
fi

if [[ "${1:-}" == "--source" ]]; then
  # -sa includes the orig tarball (required for the first upload of a version).
  dpkg-buildpackage -S -sa
  echo
  echo ">> now upload it:  dput ppa:<your-launchpad-user>/wayparam ../wayparam_${DEB_VERSION}_source.changes"
else
  dpkg-buildpackage -us -uc -b
  echo
  echo ">> built:"
  ls -1 ../wayparam*_"${DEB_VERSION}"_all.deb | sed 's/^/     /'
  echo ">> the CLI is in wayparam_*.deb; the web UI in wayparam-gui_*.deb"
fi
