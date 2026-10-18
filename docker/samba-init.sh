#!/bin/sh
# Start script for the optional Samba service (compose.samba.yml).
#
# Creates a local "playshelf" account with PUID/PGID before the image's own
# entrypoint runs. The inbox share forces all file access to this account, so
# everything written over SMB belongs to the user Playshelf runs as. The
# account gets no SMB password, so nobody can sign in with it.
set -eu

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if ! id playshelf >/dev/null 2>&1; then
    group=$(awk -F: -v gid="$PGID" '$3 == gid { print $1; exit }' /etc/group)
    if [ -z "$group" ]; then
        addgroup -g "$PGID" playshelf
        group=playshelf
    fi
    adduser -D -H -u "$PUID" -G "$group" -s /bin/false playshelf
fi

exec /container/scripts/entrypoint.sh "$@"
