#!/bin/sh
# Install a release from a tarball on a board with no network.
#
#   sudo sh offline-deploy.sh duck-offline-<version>.tar.gz
#   sudo sh offline-deploy.sh /media/usb/release.tar.gz --bootstrap
#
# The tarball is what `scripts/offline-package.sh` makes: a release directory. This unpacks it
# somewhere `updaterd` can read and applies it, which means every check a download gets —
# signature, hash, compatibility, disk space, the atomic swap, the health gate, auto-rollback.
# `--from` changes where the bytes come from and nothing else.
#
# **Not `/tmp`, and not `/var/tmp`.** `updaterd.service` sets `PrivateTmp=yes`, so the daemon has
# its own copies of both; a release unpacked there is not the one it reads, and the failure is a
# preflight error against a directory whose `ls` lists the file it says is missing. The default is
# `~/duck-sideload` in the *invoking* user's home — outside that namespace, and needing no root
# to write.
#
# `--bootstrap` is for a board whose installed `updaterd` predates `apply --from` and cannot be
# asked to use it. It runs `updaterd install --from`, which forces `on_apply` and the health gate
# off, so it stops `robotd` first and restarts it after. One install, no auto-rollback; every
# later deploy is the ordinary path.
set -eu
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
TAR=""
DIR=""
BOOTSTRAP=no
DRY_RUN=no
while [ $# -gt 0 ]; do
    case "$1" in
        --dir) [ $# -ge 2 ] || die "--dir needs a directory" ; DIR="$2" ; shift ;;
        --bootstrap) BOOTSTRAP=yes ;;
        --dry-run) DRY_RUN=yes ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) die "unknown argument: $1  (try --help)" ;;
        *) [ -z "$TAR" ] || die "one tarball at a time: $TAR is already named"
           TAR="$1" ;;
    esac
    shift
done
[ -n "$TAR" ] || die "no tarball named — offline-deploy.sh <release.tar.gz>"
[ -f "$TAR" ] || die "no such file: $TAR"
[ "$(id -u)" -eq 0 ] || die "run this as root: sudo sh offline-deploy.sh $TAR"
# The invoking user's home, so a deploy as `sudo sh` does not unpack into /root and then puzzle
# whoever looks for it. SUDO_USER is unset when this is run from a root shell, which is fine.
if [ -z "$DIR" ]; then
    home=""
    if [ -n "${SUDO_USER:-}" ] && command -v getent >/dev/null 2>&1; then
        home="$(getent passwd "$SUDO_USER" | cut -d: -f6)" || home=""
    fi
    DIR="${home:-$HOME}/duck-sideload"
fi
case "$DIR" in
    /) die "refusing to use / as the sideload directory" ;;
    /tmp|/tmp/*|/var/tmp|/var/tmp/*)
        die "$DIR is invisible to updaterd: the unit sets PrivateTmp=yes, so /tmp and /var/tmp \
are its own. Anywhere else works — the default, ~/duck-sideload, does." ;;
    /*) ;;
    *) die "--dir must be an absolute path, got $DIR" ;;
esac
RELEASE="$DIR/release"
# A tarball is data off a USB stick, so its member names are not trusted: an absolute path or a
# `..` would write outside $RELEASE.
if tar -tzf "$TAR" | grep -Eq '(^/|(^|/)\.\.(/|$))'; then
    die "$TAR holds a member with an absolute path or .., which is not a release"
fi
echo "==> unpacking $(basename "$TAR") into $RELEASE"
# The whole directory is replaced rather than added to, so a second deploy does not leave two
# versions where something has to pick one. `$RELEASE` is a directory of this script's own making,
# which is why this is not `rm -rf "$DIR"`.
rm -rf "$RELEASE"
mkdir -p "$RELEASE"
# `--no-same-owner` because the uids in the tarball are the *packaging* host's, which mean nothing
# here; everything under a release is read by updaterd, which runs as root.
tar --no-same-owner -xzf "$TAR" -C "$RELEASE"
VERSION=""
others=""
for f in "$RELEASE"/*.manifest.json; do
    [ -f "$f" ] || continue
    # The unversioned `manifest.json` a published release serves is not a version directory entry.
    case "${f##*/}" in manifest.json) continue ;; esac
    v="${f##*/}"
    v="${v%.manifest.json}"
    if [ -z "$VERSION" ]; then
        VERSION="$v"
    else
        others="$others $v"
    fi
done
[ -n "$VERSION" ] || die "no <version>.manifest.json in $TAR — that is not a release directory"
# One release per directory, so `--from` cannot pick a version other than the one this deploy was
# asked for. A tarball of two dists is a packaging mistake, not something to choose from.
[ -z "$others" ] || die "$TAR holds more than one release: $VERSION$others — pack one dist"
artifact=no
for f in "$RELEASE"/*.tar.zst; do
    [ -f "$f" ] || continue
    artifact=yes
    break
done
[ "$artifact" = yes ] || die "no .tar.zst artifact in $TAR — that is not a release directory"
ROBOTCTL=/usr/local/bin/robotctl
[ -x "$ROBOTCTL" ] || ROBOTCTL=/opt/robot/daemon/current/bin/robotctl
UPDATERD=/opt/robot/daemon/current/bin/updaterd
if [ "$BOOTSTRAP" = yes ]; then
    [ -x "$UPDATERD" ] || die "no $UPDATERD, so there is no release to bootstrap from. A board \
with nothing installed needs scripts/install.sh once, over a network, or a factory image."
    if [ "$DRY_RUN" = yes ]; then
        echo "==> would stop robotd, then:"
        echo "    $UPDATERD install --from $RELEASE --force"
        echo "    systemctl restart updaterd; systemctl start robotd"
        exit 0
    fi
    echo "==> installing daemon $VERSION, ungated (--bootstrap)"
    # `updaterd install --force` refuses while `robotd` answers, because the objection is about a
    # *working* robot losing its safety net.
    systemctl stop robotd
    "$UPDATERD" install --from "$RELEASE" --force
    # Never restarted during an update — it is the process performing it — so the resident daemon
    # would otherwise keep running the old binary, which is the one that cannot do `--from`.
    echo "==> restarting updaterd and robotd"
    systemctl restart updaterd
    systemctl start robotd
else
    [ -x "$ROBOTCTL" ] || die "no robotctl on this board, so there is no release to update. A \
board with nothing installed needs scripts/install.sh once, over a network — or --bootstrap if \
the only problem is an installed updaterd too old to take --from."
    if [ "$DRY_RUN" = yes ]; then
        echo "==> would apply (fetch, verify, compatibility, space checks; no swap):"
        echo "    $ROBOTCTL update apply daemon --from $RELEASE --version $VERSION --dry-run"
        "$ROBOTCTL" update apply daemon --from "$RELEASE" --version "$VERSION" --dry-run
        exit 0
    fi
    echo "==> applying daemon $VERSION"
    status=0
    "$ROBOTCTL" update apply daemon --from "$RELEASE" --version "$VERSION" || status=$?
    if [ "$status" -ne 0 ]; then
        # Exit 2 is bad usage, and on a board that has never had a build with `--from` that is
        # how the API mismatch surfaces: the daemon refuses the call rather than ignoring the
        # option and installing from its configured source.
        if [ "$status" -eq 2 ]; then
            echo "If robotctl and updaterd disagree about the API version, this board's installed" >&2
            echo "release predates 'apply --from'. Deliver it once, ungated:" >&2
            echo "    sudo sh $0 $TAR --bootstrap" >&2
        else
            echo "The board keeps running what it was on — a failed apply rolls back." >&2
            echo "    sudo robotctl update status   # and: sudo robotctl update log" >&2
        fi
        exit "$status"
    fi
fi
echo "==> current -> $(readlink -f /opt/robot/daemon/current 2>/dev/null || echo '?')"
echo "    updaterd and btd restart five seconds after the apply replies; 'robotctl health' prints"
echo "    the release each daemon is actually running from."
echo "    The unpacked release stays in $RELEASE, so re-applying needs no tarball:"
echo "    sudo robotctl update apply daemon --from $RELEASE"
