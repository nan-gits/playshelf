# Deployment

This guide covers running Playshelf with Docker Compose: where the data lives,
which settings exist, how to add the optional SMB share for the inbox, and how
to put Playshelf behind a reverse proxy safely.

## Quick start

Requirements: Docker Engine with the Compose plugin (`docker compose version`).

```bash
git clone <repository-url> playshelf
cd playshelf
docker compose up -d --build
```

Open `http://<host>:8080`. All data is stored in `./data` next to
`docker-compose.yml`:

| Folder         | Content |
|----------------|---------|
| `data/games/`  | The game library, one subfolder per game |
| `data/data/`   | Database, database backups, backups of replaced game versions, thumbnails |
| `data/inbox/`  | Inbox: ZIP files placed here are imported automatically |

Missing folders are created on the first start and handed to the user
Playshelf runs as (see [User and file permissions](#user-and-file-permissions)).

To update later:

```bash
git pull
docker compose up -d --build
```

## Configuration

Settings go in a file named `.env` next to `docker-compose.yml`, one
`NAME=value` per line. Every setting is optional. After changing `.env`, run
`docker compose up -d` again.

| Setting | Default | Meaning |
|---------|---------|---------|
| `HTTP_PORT` | `8080` | Port the dashboard is reachable on |
| `DATA_ROOT` | `./data` | Folder holding `games/`, `data/` and `inbox/`. Relative paths are relative to `docker-compose.yml` |
| `PUID` / `PGID` | `1000` | User and group id Playshelf runs as and that own the files in `DATA_ROOT` |
| `TZ` | `UTC` | Time zone for timestamps and logs, e.g. `Europe/Berlin` |
| `AUTH` | `accounts` | `accounts` = sign in with user accounts, `none` = no login, see [Accounts and sign-in](#accounts-and-sign-in) |
| `PASSWORD_HASH` | empty | Only when upgrading from a version with a single password, see [Accounts and sign-in](#accounts-and-sign-in) |
| `MAX_UPLOAD_MB` | `4096` | Largest ZIP file accepted for import |
| `MAX_EXTRACTED_MB` | `8192` | Largest unpacked size of a ZIP file (protection against zip bombs) |
| `KEEP_BACKUPS` | `3` | Old versions kept per game after an update |
| `INBOX_POLL_SECONDS` | `5` | How often the inbox is checked; `0` turns the inbox off |
| `INBOX_SETTLE_SECONDS` | `10` | How long a ZIP file must stay unchanged before it is imported |
| `UPDATE_CHECK_HOURS` | `24` | Interval of the online update check; `0` = only when started by hand |
| `ADULT_SOURCES` | `false` | Enables the F95Zone and TFGames update sources |
| `UI_LANGUAGE` | `auto` | Interface language: `en`, `de`, or `auto` for the browser's language |

Example `.env`:

```dotenv
TZ=Europe/Berlin
DATA_ROOT=/srv/playshelf
PUID=1000
PGID=1000
```

### Moving an existing library

Point `DATA_ROOT` at a folder whose `games/` subfolder holds one folder per
game, or copy your game folders into `data/games/`. Then click
**Rescan folder** in the dashboard and confirm the version of each game.

## User and file permissions

Playshelf never runs as root. The container starts, prepares the data
folders and then switches to `PUID:PGID`. Choose the ids of the host user who
should own the files, usually your own account:

```bash
id -u   # PUID
id -g   # PGID
```

On start, empty data folders are given to `PUID:PGID` automatically. Folders
that already contain files are never re-owned; if they belong to someone
else, the log shows a warning with the command to fix it, for example:

```bash
sudo chown -R 1000:1000 ./data
```

Files you copy into `games/` or `inbox/` by hand must be readable and
writable for that user too.

## Accounts and sign-in

By default (`AUTH=accounts`) everyone signs in with their own account. On the
first start, open the dashboard and create the admin account on the setup
page; until then every page leads there, so do this right after the first
`docker compose up -d`. The admin adds further accounts under **Accounts**.
How accounts, sharing and the per-account inbox folders work is described in
the [README](../README.md#accounts).

**Forgotten password.** Set a new one from the command line (run it as
`PUID:PGID`, 1000:1000 by default, so the database keeps its owner):

```bash
docker compose exec -u 1000:1000 playshelf python -m app.auth --reset <user name>
```

**Upgrading from a version with a single password.** Keep `PASSWORD_HASH` in
`.env` for the first start of the new version: it becomes the password of the
admin account `admin`, which keeps all games and play history. Sign in as
`admin` with your old password; you can rename the account under **Accounts**.
After that `PASSWORD_HASH` is ignored and can be removed.

**No login.** `AUTH=none` in `.env` turns sign-in off; everyone who can reach
the port then acts as the first admin. Only use this on a network you trust.

## Inbox over SMB (optional)

The inbox becomes most useful as a network folder: copy a ZIP file into it
from any computer and Playshelf imports it on its own. Playshelf ships an
optional Samba service for this in `compose.samba.yml`. It offers one share,
`\\<host>\inbox`. Everyone signs in with their own SMB account and sees only
their own inbox folder (`inbox/<user name>/`), so their imports belong to
them.

1. Create the Playshelf accounts first (**Accounts** in the dashboard). Each
   account gets its inbox folder automatically.

2. Copy `samba.env.example` to `samba.env` and add one line per person who
   should use the share. The name must be the Playshelf user name; the
   password is a separate SMB password:

   ```dotenv
   ACCOUNT_alice=a-long-smb-password
   ACCOUNT_bob=another-smb-password
   ```

   SMB accounts only work for user names made of letters, digits and `_`
   (no `.` or `-`), and the password must not contain spaces.

3. Add this line to `.env` (on a Windows host, separate the file names with
   `;` instead of `:`) and start it:

   ```dotenv
   COMPOSE_FILE=docker-compose.yml:compose.samba.yml
   ```

   ```bash
   docker compose up -d
   ```

4. Connect from a client and sign in with the SMB account:
   - Windows: open `\\<host>\inbox` in Explorer, or map it as a network
     drive.
   - macOS: Finder → Go → Connect to Server → `smb://<host>/inbox`.
   - Linux: `smb://<host>/inbox` in the file manager, or
     `smbclient //<host>/inbox -U alice`.

Copy a ZIP file into the share. After about 15 seconds it moves to
`processed/` and the import appears under **Pending imports** for that
account. Archives that were rejected move to `failed/`, next to a `.txt` file
with the reason.

To add or remove an SMB account later, edit `samba.env` and run
`docker compose up -d --force-recreate samba`. If an account is renamed in
Playshelf, rename its line in `samba.env` too.

Files written through the share belong to `PUID`, so Playshelf can move them.

**No login (`AUTH=none`).** There are no per-account folders then. Add
`SAMBA_INBOX_PATH=/shares/inbox` to `.env`, so every SMB account sees the
whole inbox.

**Port 445 already in use?** Only one SMB server can listen on port 445 of a
host. If the host already runs Samba (or another container does), do not
start this service; add a share for the inbox folder to the existing server
instead, and make sure files written there belong to `PUID`. `SAMBA_PORT`
changes the published port, but Windows only connects on port 445, so another
port only helps macOS and Linux clients.

## Behind a reverse proxy

Playshelf can sit behind any reverse proxy that terminates HTTPS. Keep in
mind:

- Serve it at the root of its own host name (`https://games.example.org/`).
  Running it under a sub-path (`/playshelf/`) is not supported.
- Allow large request bodies, or ZIP uploads fail. The limit should be at
  least `MAX_UPLOAD_MB`.
- Allow long requests: importing a large archive can take a few minutes.
- Pass `X-Forwarded-Proto`, so the login cookie is marked secure on HTTPS.
- Once the proxy is in place, publish the port on localhost only, so the
  dashboard cannot be reached around the proxy. In `docker-compose.yml`:
  `- "127.0.0.1:${HTTP_PORT:-8080}:8000"`. If the proxy runs on another
  machine, restrict the port with a firewall instead.

nginx:

```nginx
server {
    listen 443 ssl;
    server_name games.example.org;
    # ssl_certificate ... ;

    client_max_body_size 4g;
    proxy_request_buffering off;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Caddy (no upload limit by default; sets the forwarding headers itself):

```caddyfile
games.example.org {
    reverse_proxy 127.0.0.1:8080
}
```

Nginx Proxy Manager: create a proxy host for `http://<host>:8080`, request a
certificate, and add `client_max_body_size 4g;` under **Advanced**.

## Security notes

- **Keep sign-in on** (`AUTH=accounts`) before Playshelf is reachable from
  anywhere but your own network, and use HTTPS for that. Create the admin
  account right after the first start: until then, whoever opens the page
  first can create it.
- **Games run with the dashboard's rights.** Games are served from the same
  address as the dashboard, so a game's JavaScript could use the dashboard
  the same way you do (for example delete games). Only add games you trust.
- **Never expose SMB to the internet.** Do not forward port 445; use the
  share inside your home network or over a VPN only.
- **The online update check** connects to the game pages you link (itch.io,
  Patreon, and F95Zone/TFGames if `ADULT_SOURCES` is on). Set
  `UPDATE_CHECK_HOURS=0` to only check when you start it by hand.
- **Backups:** the database is backed up daily to `data/data/db-backups/`.
  Back up the whole `DATA_ROOT` folder to another disk; it holds everything
  Playshelf knows.
