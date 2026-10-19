# Playshelf

A self-hosted library for the HTML games you keep on your own disk. Playshelf
scans a folder of downloaded browser games and shows them as tiles: start a
game with one click, keep track of versions and update links, see when you last
played, and import new games and updates from ZIP, RAR, 7z or tar archives.

- **Library:** every subfolder is a game; the start file, title, version hint
  and cover image are detected automatically.
- **Versions:** the installed version is recorded per game, with a version
  history. Updates replace the game folder safely and keep the old version as a
  backup you can restore.
- **Archive import:** upload ZIP, RAR, 7z or tar archives in the browser,
  paste a download link and let Playshelf fetch the archive itself, or drop
  archives into an inbox folder (for example a network share). Every archive
  is checked before it touches the library.
- **Update check:** for games with an update link (itch.io, Patreon and,
  optionally, F95Zone), Playshelf looks up the newest version online and marks
  games with an update.
- **Statistics:** most played, not played for a long time, disk usage, and a
  list of all backups with their size.
- **User accounts:** everyone in the household signs in with their own
  account and sees their own games; last played, favourites, status and
  statistics are kept per person. Admins see all games and can share them.
  Login can be turned off for a single user.
- **English and German interface,** chosen from the browser's language or set
  with `UI_LANGUAGE`.

Only HTML games are supported. Folders with an `.exe` (Ren'Py, RPG Maker,
NW.js and similar) are skipped.

Playshelf is a Python application (FastAPI, SQLite) and runs as a Docker
container or directly with Python 3.11 or newer.

## Installation

Running Playshelf on a server with Docker Compose is described step by step in
[docs/INSTALL.md](docs/INSTALL.md), including updates, backups, the inbox
folder and moving an existing game collection.

To try it right away with Docker, without downloading anything else:

```bash
docker run -d --name playshelf -p 8080:8000 \
  -v "$PWD/playshelf/games:/srv/games" \
  -v "$PWD/playshelf/data:/srv/data" \
  -v "$PWD/playshelf/inbox:/srv/inbox" \
  ghcr.io/nan-gits/playshelf:1
```

Then open <http://127.0.0.1:8080> and create the admin account. Games go into
`playshelf/games/`, one subfolder per game. Stop and remove the test with
`docker rm -f playshelf`; the folders stay.

The image is published for `linux/amd64` and `linux/arm64` (for example a
Raspberry Pi 4 or 5) on the
[GitHub Container Registry](https://github.com/nan-gits/playshelf/pkgs/container/playshelf).

Without Docker, with Python 3.11 or newer:

```bash
git clone https://github.com/nan-gits/playshelf.git
cd playshelf
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install .
playshelf                        # or: python -m app
```

RAR, 7z and tar imports need the libarchive library (in the Docker image
already): `sudo apt install libarchive13t64` on Debian 13 and Ubuntu 24.04
(`libarchive13` on older releases), `brew install libarchive` on macOS. Without
it, only ZIP archives are imported.

Then open <http://127.0.0.1:8000>. On the first start Playshelf asks you to
create the admin account. Games go into the `games/` folder next to where you
started Playshelf, one subfolder per game.

## Configuration

All settings come from environment variables or a `.env` file in the folder
Playshelf is started from. [.env.example](.env.example) lists every setting
with its default:

```bash
cp .env.example .env
```

| Variable | Default | Meaning |
|---|---|---|
| `GAMES_DIR` | `games` | Game library, one subfolder per game |
| `DATA_DIR` | `data` | Database, backups and thumbnails |
| `INBOX_DIR` | `inbox` | Inbox folder for new archives |
| `HOST` | `127.0.0.1` | Address the web server binds to |
| `PORT` | `8000` | Web server port |
| `MAX_UPLOAD_MB` | `4096` | Maximum size of an uploaded archive in MB |
| `MAX_EXTRACTED_MB` | `8192` | Maximum size of an archive's extracted content in MB |
| `URL_IMPORT_PRIVATE_NETWORKS` | `false` | Allow **Import from link** to download from addresses in the local network |
| `KEEP_BACKUPS` | `3` | Old versions kept per game after updates |
| `INBOX_POLL_SECONDS` | `5` | How often the inbox folder is checked, `0` = off |
| `INBOX_SETTLE_SECONDS` | `10` | How long an archive must stay unchanged before it is imported |
| `UPDATE_CHECK_HOURS` | `24` | Interval of the online update check in hours, `0` = by hand only |
| `ADULT_SOURCES` | `false` | Offer the adult game sites F95Zone and TFGames as sources |
| `UI_LANGUAGE` | `auto` | Interface language: `en`, `de`, or `auto` for the browser's language (English if it is neither) |
| `AUTH` | `accounts` | `accounts` = sign in with user accounts, `none` = no login (everyone acts as the first admin) |
| `PASSWORD_HASH` | empty | Only for upgrades from versions with a single password: becomes the first admin's password once, see [Accounts](#accounts) |

Missing folders are created at startup. If a path cannot be used (for example
a file instead of a folder), Playshelf stops with a clear error message.

## Usage

### Library

Every subfolder of `GAMES_DIR` is a game. The folder is scanned at startup and
with **Rescan folder**:

- **Start file:** the shallowest `index.html`, otherwise an HTML file named
  like the folder, otherwise the largest HTML file (up to three folder levels
  deep). `changelog`, `credits`, `head`, `readme`, `license`, `__MACOSX` and
  `._` files never count. The start file can be changed in the edit dialog.
- **Title** from the start file's `<title>`, **version hint** from the folder
  name (for example `Quiet Valley 0_11` → `0.11`). The version itself is
  required and confirmed in the edit dialog; games without one are marked.
- **Cover image:** an image with `cover`, `poster`, `banner`, `title`,
  `splash`, `thumb` or `preview` in its name (in this order, the largest if
  there are several), otherwise the largest image in the game folder. Another
  image from the folder, no image, or an uploaded image can be chosen in the
  edit dialog. Tiles show scaled-down copies from `DATA_DIR/thumbs`.
- **Missing folders** are greyed out and admins can remove their entries.
- **Disk usage** of every game folder is computed when scanning.

**Play** opens the game in a new tab and records the start. Browsers keep game
saves per address: saves made while playing from `file:///…` do not show up
under Playshelf's address. Most games can export and import saves (in SugarCube
games: *Saves* → *Save to Disk* / *Load from Disk*).

**Deleting a game** is in the edit dialog. Admins delete a game for everyone:
its folder, its backups and every account's play data. Other accounts remove
a game from their own library; its files are only deleted when no other account
owns the game or has played it, marked it or given it a status. Playshelf only
deletes folders inside `GAMES_DIR` and `DATA_DIR/backups`, never links.

Each game can have a **status** (playing, completed, dropped, waiting for an
update) and be marked as a **favourite**. **Statistics** shows the most played
games, games not played for more than 30 days or never, and disk usage per game
and in total, including backups.

### Accounts

With `AUTH=accounts` (the default) everyone signs in with a user name and
password. On the first start every page leads to a setup page that creates the
first **admin** account. Admins manage accounts under **Accounts** (add,
rename, set a new password, change the role, remove) and see every game.
**Overview** shows which account has which games, with that account's last
start and number of starts, and the games nobody owns yet.
**Users** see the games they own and the games shared with them.

- A game imported by someone belongs to them. Games found by **Rescan
  folders** (copied into `GAMES_DIR` by hand) belong to nobody yet, so only
  admins see them until an admin ticks the accounts under **Visible to** in the
  edit dialog.
- Last played, play count, favourite, status and the statistics are kept per
  account. Title, version, update link, notes and cover image belong to the
  game and are the same for everyone who sees it.
- Pending imports are visible only to the account that uploaded them.
- Each account picks its interface language and changes its password under its
  name in the top bar.
- Removing an account deletes its play data; its games and files stay, and its
  pending imports move to the admin who removed it.

Forgotten password: `python -m app.auth --reset <user name>` sets a new one
directly in the database (with Docker: see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#accounts-and-sign-in)).

With `AUTH=none` there is no login and everyone acts as the first admin, as in
a single-user setup.

**Upgrading from a version with a single password:** the existing
`PASSWORD_HASH` becomes the password of the admin account `admin`, and all
games and play history stay with that account. Afterwards `PASSWORD_HASH` is no
longer read and can be removed.

### Importing new games and updates

Use **Import game**, drag archives onto the page, or drop them into the inbox
folder `INBOX_DIR`. Supported are `.zip`, `.rar` (RAR 4 and 5), `.7z`, `.tar`,
`.tar.gz`/`.tgz`, `.tar.bz2`/`.tbz2` and `.tar.xz`/`.txz`; everything but ZIP
needs libarchive (see [Installation](#installation)). Every archive is checked
and extracted into a staging folder under `DATA_DIR/imports`:

- Archives are rejected if they are damaged or password protected, contain an
  `.exe`, have no HTML file, contain paths outside the game folder, links or
  special files, or are larger than `MAX_UPLOAD_MB` (extracted:
  `MAX_EXTRACTED_MB`). The message gives the reason.
- Archives split into several parts (`.part1.rar`, `.part2.rar`, `.001` …) are
  not supported; extract them and pack the game as one archive.
- Wrapper folders, `__MACOSX` and `._` files are removed and the folder name is
  cleaned up.

The archive then appears under **Pending imports** with a suggestion: a new
game, or an update of an existing one (matched by folder name without version,
title and start file, with the reason shown). Match, version and start file can
be changed before confirming; **Discard** deletes the staging folder.

On an **update**, the previous version is moved to
`DATA_DIR/backups/<game>/<version>` before the new one is put in place. If
anything fails on the way, the old version stays playable. Title, source,
update link, notes and play statistics are kept. The edit dialog shows the
version history and the saved versions with their size; **Restore** brings one
back and saves the current version in turn, **Delete** removes it. At most
`KEEP_BACKUPS` backups are kept per game. **Backups** in the toolbar lists the
backups of all your games, largest first; admins also see backup folders that
no game refers to any more (for example from entries removed with older
versions) and can delete them there.

**Import from link** takes a download link instead of a file. Playshelf
downloads the archive on the server, with progress shown in the page, and then
imports it exactly like an upload; the same checks and `MAX_UPLOAD_MB` apply.
The download keeps running when the page is closed, and the list comes back
when the page is opened again (a restart of Playshelf drops running downloads).
At most two downloads run at the same time, the others wait.

- Direct links to an archive work best (in the browser: "Copy link address" on
  the download button). Redirects are followed, and the file name comes from
  the server or, if it has none, from the content.
- Pixeldrain (`/u/…`), Dropbox and Google Drive share links are turned into
  their download address automatically.
- MEGA, Gofile and Patreon cannot be downloaded this way (MEGA decrypts in the
  browser, the others need a browser session or your login); Playshelf says so
  right away. Download those files yourself and use **Import game**.
- If a link leads to a web page instead of a file, the import stops with a
  message and nothing is kept.
- Only addresses on the internet are contacted. Links to the local network
  (for example a NAS) are refused unless `URL_IMPORT_PRIVATE_NETWORKS=true`.

Archives dropped into the inbox are imported once they have not changed for
`INBOX_SETTLE_SECONDS`, so files still being copied are left alone. Afterwards
the archive is moved to `processed/`, or to `failed/` next to a `.txt` file
with the reason. Files of a kind that is not imported (a lone `.gz`, parts of a
split archive, RAR or 7z without libarchive) go to `failed/` with a note too.

Each account has its own inbox subfolder, created automatically:

```text
INBOX_DIR/
├── anna/            archives here become Anna's pending imports
│   ├── processed/
│   └── failed/
├── ben/
├── processed/       for archives placed directly in INBOX_DIR,
└── failed/          which belong to the first admin
```

Subfolders that match no account are ignored. Renaming an account renames its
folder; removing an account leaves the folder on disk. User names are limited
to `a-z`, digits and `. _ -` (at most 32 characters) so that they work as folder
and SMB user names; `processed` and `failed` are reserved.

### Online update check

For games with an **update link**, Playshelf looks up the newest version
online: automatically about every `UPDATE_CHECK_HOURS` hours (first two minutes
after startup), for all games with **Check for updates**, or for one game with
**Check now** in the edit dialog. Pages are requested one after another with
Playshelf's own user agent, a 20-second timeout and at least 5 seconds between
two requests to the same server.

If the online version is newer than the recorded one, the tile shows
**Update available** with a link to the update page. The notice disappears by
itself once the new version is imported or entered. Version comparison
understands common spellings (`0.11` = `0_11` = `v0.11`, `1.0` = `1.0.0`,
`1.0.26a` > `1.0.26`, `0.10` > `0.9`, `1.0 beta` < `1.0`).

| Source | Update link | What is checked |
|---|---|---|
| itch.io | Game page `https://<name>.itch.io/<game>` | Version from itch.io's Wharf API, only if the developer uploads with *butler*; otherwise "check by hand" |
| Patreon | Creator page `https://www.patreon.com/<name>` | First version (`v0.6`, `0.6.1`) in the titles of the ten newest posts; otherwise "check by hand" |
| F95Zone | Thread `https://f95zone.to/threads/name.12345/` | Version and date of the last update from F95Zone's "Latest Updates" data, searched by the game's title and matched by the thread number in the link, without login. If the search does not find the thread, the thread page; when that asks for a browser check, "check by hand". Only with `ADULT_SOURCES=true` |
| TFGames | Game page | No automatic check: the site only lets real browsers through (bot protection). Only offered with `ADULT_SOURCES=true` |
| others | any | "check by hand" |

Pages that ask for a browser check (bot protection) are shown as "check by hand" with a
link to the page. Bot protection is never circumvented, and Playshelf never downloads games
itself; updates always come in as archives.

### API

The web UI uses a JSON API under `/api`; interactive documentation is served at
`/docs`. Invalid input is answered with `{"detail": [{"field": ..., "message": ...}]}`;
other errors (not found, rejected archive) with `{"detail": "message"}`.

| Method and path | Purpose |
|---|---|
| `GET /api/games` | All games, each with `update`: the latest online check (`status` ok/error/manual, `version`, `date`, `error`, `checked_at`, `available`) or `null` |
| `GET /api/games/{id}` | One game |
| `PATCH /api/games/{id}` | Change title, version, source, update link, notes, start file, status, favourite (status and favourite are per account and also work without a version) |
| `PUT /api/games/{id}/owners` | Admins: set the accounts that see the game, `{"user_ids": […]}` |
| `DELETE /api/games/{id}` | Admins: delete a game for everyone, with its folder and backups |
| `POST /api/games/{id}/remove` | Remove a game from the own library; returns `{"deleted": …}`, true if no other account used it and its files were deleted |
| `GET /api/games/{id}/html-files` | Possible start files |
| `POST /api/scan` | Admins: rescan the games folder; returns new, found and missing counts |
| `GET /api/sources` | Sources offered for the update link |
| `GET /api/statuses` | Status codes |
| `GET /api/stats` | Statistics of the account's games |
| `GET /api/games/{id}/image` | Scaled-down cover image (WebP) |
| `GET /api/games/{id}/images` | Images in the game folder; `auto` is the automatic choice |
| `PUT /api/games/{id}/image` | Choose the cover image: `{"mode": "auto"}`, `{"mode": "none"}` or `{"mode": "file", "path": …}` |
| `POST /api/games/{id}/image` | Upload a custom image as raw request body (max. 25 MB) |
| `GET /api/games/{id}/history` | Version history and saved versions |
| `GET /api/backups` | Backups of the account's games, largest first, and `keep` (`KEEP_BACKUPS`); for admins also `orphans`, backup folders without a game |
| `DELETE /api/backups/{id}` | Delete a backup |
| `DELETE /api/backups?path=…` | Admins: delete a backup folder without a game (`path` as listed under `orphans`) |
| `GET /api/overview` | Admins: the games of each account with its play data, and the games without owner |
| `POST /api/games/{id}/restore` | Restore a backup (`{"backup_id": …}`; the newest one if omitted) |
| `POST /api/imports?filename=…` | Upload an archive as raw request body, check and extract it |
| `GET /api/imports` | The account's pending imports with suggestion |
| `POST /api/downloads` | Import from a link: `{"url": …}`; downloads in the background and returns the download with its `id` |
| `GET /api/downloads` | The account's downloads with `state` (`queued`, `downloading`, `extracting`, `done`, `failed`), `received`, `total`, `error` and `import_id` |
| `DELETE /api/downloads/{id}` | Cancel a download, or remove a finished one from the list |
| `GET /api/imports/limits` | Size limits for imports in bytes (`MAX_UPLOAD_MB`, `MAX_EXTRACTED_MB`) and the supported archive endings (`formats`) |
| `POST /api/imports/{id}/confirm` | Install: `version` (required), `game_id` for an update, otherwise a new game; optional `entry`, for new games also `title`, `source`, `update_url`, `notes` |
| `DELETE /api/imports/{id}` | Discard an import |
| `POST /api/games/{id}/check-update` | Check one game online right away |
| `POST /api/updates/check` | Check all games with an update link in the background |
| `GET /api/updates/status` | Whether a check is running |
| `GET /api/health` | Health check |
| `GET /api/me` | The signed-in account; `accounts` is false with `AUTH=none` |
| `PATCH /api/me` | Set the account's language: `{"language": "en" \| "de" \| null}` |
| `POST /api/me/password` | Change the own password: `{"current": …, "new": …}` |
| `GET /api/users` | Admins: all accounts |
| `POST /api/users` | Admins: add an account, `{"username", "password", "role": "user" \| "admin"}` |
| `PATCH /api/users/{id}` | Admins: change `username`, `password` or `role` |
| `DELETE /api/users/{id}` | Admins: remove an account |
| `GET /play/{id}` | Record a start and redirect to the game |
| `GET /games/…` | Game files |
| `GET/POST /login`, `POST /logout` | Log in and out |

## Development

```bash
pip install -e ".[dev]"
uvicorn app.main:app --reload   # development server with auto-reload
pytest                          # tests
ruff check                      # linter
ruff format --check             # formatting (without --check: format)
```

The same checks run on every push and pull request in GitHub Actions
(Python 3.11 and 3.13).

```
app/              Application code (FastAPI)
  config.py       Settings
  db.py           SQLite database with migrations
  scanner.py      Scanning the games folder, detecting the start file
  importer.py     Checking and extracting archives safely
  imports.py      Pending imports, updates with backup, restore, deleting
  inbox.py        Watching the inbox folder
  images.py       Cover images: choice, upload, scaling
  stats.py        Statistics
  updates.py      Online update check: version comparison, results, schedule
  sources/        One adapter per source (itch.io, Patreon, F95Zone, TFGames)
  api.py          REST API
  play.py         Starting games and serving their files
  backup.py       Daily database backup
  auth.py         Sign-in, sessions, setup page
  users.py        Account rules and per-account inbox folders
  accounts.py     Account API (/api/me, /api/users)
  static/         Web UI (HTML/CSS/JS, no build step)
tests/            Tests (pytest)
docs/             Documentation
```

## Contributing

Bug reports, ideas and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the development setup and
[SECURITY.md](SECURITY.md) for reporting security problems privately. Changes
between versions are listed in [CHANGELOG.md](CHANGELOG.md).

## Licence

Playshelf is free software, licensed under the
[GNU Affero General Public License v3.0 or later](LICENSE). If you run a
modified version for other people over a network, you must offer them its
source code.
