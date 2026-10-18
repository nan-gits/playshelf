#!/bin/sh
# Container entrypoint: prepare the data folders, then run Playshelf as PUID:PGID.
#
# Docker creates missing bind-mount folders as root, so on a first start the
# app could not write to them. Empty folders are handed to PUID:PGID here;
# folders that already hold files are never re-owned, only reported.
# If the container is started as a non-root user (`user:` in Compose), this
# step is skipped and the command runs as that user.
set -eu

if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
case "$PUID$PGID" in
    *[!0-9]*)
        echo "PUID and PGID must be numeric (got PUID=$PUID, PGID=$PGID)." >&2
        exit 1
        ;;
esac
if [ "$PUID" = "0" ]; then
    echo "Refusing to run as root: set PUID to a regular user id." >&2
    exit 1
fi

for dir in "$GAMES_DIR" "$DATA_DIR" "$INBOX_DIR"; do
    mkdir -p "$dir"
    if [ "$(stat -c %u "$dir")" = "$PUID" ]; then
        continue
    fi
    if [ -z "$(ls -A "$dir")" ]; then
        chown "$PUID:$PGID" "$dir"
    else
        echo "Warning: $dir is owned by uid $(stat -c %u "$dir"), not PUID=$PUID." \
            "Fix it on the host: sudo chown -R $PUID:$PGID <folder mounted at $dir>" >&2
    fi
done

exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups -- "$@"
