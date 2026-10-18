# Installing Playshelf on a server

This guide sets up Playshelf from scratch on a Linux server with Docker
Compose, then covers updates, backups and moving an existing game collection.
The commands are written for Debian and Ubuntu; other distributions work the
same once Docker is installed.

All settings, file permissions, password protection, the optional SMB share
for the inbox and reverse-proxy setups are described in
[DEPLOYMENT.md](DEPLOYMENT.md).

Commands that change the system run with `sudo`. Replace `<server>` with the
server's address in your network.

## 1. Install Docker

Skip this step if `docker version` and `docker compose version` both print a
version. Otherwise install Docker Engine with the Compose plugin from Docker's
own package repository (shown for Debian; for Ubuntu replace `debian` with
`ubuntu` in both URLs):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

`systemctl enable` makes Docker start at boot. To run `docker` without `sudo`,
add your user to the `docker` group (`sudo usermod -aG docker $USER`) and log in
again.

## 2. Get Playshelf

Pick a folder for the installation, for example `/opt/playshelf`:

```bash
sudo git clone https://github.com/nan-gits/playshelf.git /opt/playshelf
sudo chown -R "$(id -u):$(id -g)" /opt/playshelf
cd /opt/playshelf
```

## 3. Configure

Playshelf works without any configuration: it stores everything in `data/`
next to `docker-compose.yml` and is reachable on port 8080. To change that,
create a file `.env` in `/opt/playshelf`, for example:

```dotenv
TZ=Europe/Berlin          # time zone for timestamps and logs
HTTP_PORT=8080            # port in your network
DATA_ROOT=./data          # folder holding games/, data/ and inbox/
PUID=1000                 # user that owns the files (`id -u`)
PGID=1000                 # group of that user (`id -g`)
```

The full list of settings is in [DEPLOYMENT.md](DEPLOYMENT.md#configuration).
Set `PUID` and `PGID` to the account that should own the game files, usually
your own, so you can copy games into the library without permission problems.

Port 8080 must not be used by another service (`ss -tlnp | grep 8080` prints
nothing). A fixed address for the server, set on the server itself or as a
DHCP reservation in your router, keeps the dashboard's address stable.

## 4. Start

```bash
docker compose up -d --build
docker compose ps        # after a few seconds: status "healthy"
```

Open `http://<server>:8080`. The health endpoint `http://<server>:8080/api/health`
answers `{"status": "ok", ...}` when Playshelf is running.

Playshelf restarts by itself after a reboot: Docker starts at boot, and the
container uses the restart policy `unless-stopped`. A container stopped with
`docker compose stop` stays stopped until you start it again.

Open the dashboard right away: on the first start it asks you to create the
admin account (see [Accounts and sign-in](DEPLOYMENT.md#accounts-and-sign-in)).
Before making Playshelf reachable from outside your home network, read the
[security notes](DEPLOYMENT.md#security-notes).

## 5. Add games

Copy each game into its own folder under `data/games/` (or `DATA_ROOT/games/`),
then click **Rescan folder** in the dashboard. Alternatively upload ZIP files
in the dashboard or drop them into `data/inbox/`. See
[Moving an existing library](#8-moving-an-existing-library) for a larger
collection.

## 6. Updating Playshelf

```bash
cd /opt/playshelf
docker compose exec -u 1000:1000 playshelf python -m app.backup   # optional: extra database backup
git pull
docker compose up -d --build
docker image prune -f                                 # remove old images
```

The database is migrated to the new schema automatically at startup. Games and
data are not touched, because they live outside the container.

## 7. Backups and restore

Playshelf backs up its database by itself once a day (checked at startup and
then every hour) using SQLite's backup function, which is consistent even while
the app is running. The backups are stored in `data/data/db-backups/` as
`library-YYYYMMDD-HHMMSS.db`; the newest 14 are kept.

These backups cover the database only: versions, sources, update links, notes,
play history. Replaced game versions are kept in `data/data/backups/`; the
games themselves are in `data/games/`. Because all of this sits on the same
disk, include the whole `DATA_ROOT` folder in an external backup as well.

Create an extra backup right away, for example before an update:

```bash
docker compose exec -u 1000:1000 playshelf python -m app.backup
```

`-u` runs the command as the user Playshelf runs as, so the backup file gets
the same owner as the database. Use your `PUID:PGID` if you changed them.

Restore a backup:

```bash
cd /opt/playshelf
ls data/data/db-backups/                                   # pick a backup
docker compose stop
cp data/data/library.db data/data/library.db.before-restore
cp data/data/db-backups/library-20261016-080000.db data/data/library.db   # use your file name
docker compose start
```

Check in the dashboard that the expected state is back, then delete
`library.db.before-restore`.

## 8. Moving an existing library

If your games are already in a folder on another computer, copy every game
folder into `data/games/` on the server. Playshelf only reads game folders;
nothing in them is changed. Folders that contain an `.exe` (Ren'Py, RPG Maker
and similar) are skipped by Playshelf anyway, so there is no need to sort them
out first.

From Linux or macOS, `rsync` copies everything and can be repeated safely:

```bash
rsync -av --progress ~/Games/ <user>@<server>:/opt/playshelf/data/games/
```

From Windows, the built-in OpenSSH client can copy a whole folder:

```powershell
scp -r "$env:USERPROFILE\Documents\Games\*" <user>@<server>:/opt/playshelf/data/games/
```

If you set up the [SMB share](DEPLOYMENT.md#inbox-over-smb-optional), you can
also zip games and drop the archives into the inbox share instead.

Copy as the user set in `PUID`/`PGID`, so the files belong to the account
Playshelf runs as. Then click **Rescan folder** in the dashboard and confirm
the version of each game: the filter for games without a version shows the
ones still to do, and the version hint from the folder name is pre-filled.

**Game saves:** browsers keep game saves per address. Saves made while playing
from `file:///…` do not show up under `http://<server>:8080`. Most games can
export and import saves; in SugarCube games use *Saves* → *Save to Disk* on the
old setup and *Load from Disk* in Playshelf.

## 9. Online update check

Playshelf checks games with an update link for new versions about once a day
(how it works and which sites are supported: [README](../README.md#online-update-check)).
The interval is set with `UPDATE_CHECK_HOURS` in `.env` (`0` = only by hand);
F95Zone and TFGames are only offered with `ADULT_SOURCES=true`. Run
`docker compose up -d` after changing `.env`. The container needs outgoing
HTTPS access for the check.

## Troubleshooting

| Problem | What to check |
|---|---|
| Page does not load | `docker compose ps`: is the container running and `healthy`? Is the port free (`ss -tlnp \| grep 8080`)? |
| Container keeps restarting | `docker compose logs --tail 50`; usually the data folders are not writable for `PUID:PGID` (see [file permissions](DEPLOYMENT.md#user-and-file-permissions)) |
| A ZIP in the inbox is not imported | Is it in `inbox/failed/` (reason in the `.txt` next to it)? Otherwise check `docker compose logs playshelf` and that `inbox/` is writable for `PUID:PGID` |
| Every update check fails | Outgoing internet access from the container: `docker compose exec playshelf python -c "import urllib.request; print(urllib.request.urlopen('https://itch.io', timeout=10).status)"` |
| Games are missing | Are they direct subfolders of `games/`? Click **Rescan folder** |
| Upload fails with error 413 behind a proxy | The proxy limits the request size; see [reverse proxy](DEPLOYMENT.md#behind-a-reverse-proxy) |
