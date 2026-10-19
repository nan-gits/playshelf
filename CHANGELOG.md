# Changelog

All notable changes to Playshelf are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-10-19

### Added

- **Import from link:** paste a download link and Playshelf downloads the
  archive on the server, with progress in the page, then extracts and checks
  it like an upload. Downloads continue when the page is closed and can be
  cancelled. Pixeldrain, Dropbox and Google Drive share links are converted to
  their download address; MEGA, Gofile and Patreon links are refused with an
  explanation, as are links that lead to a web page instead of a file.
  `MAX_UPLOAD_MB` applies.
- `URL_IMPORT_PRIVATE_NETWORKS` (default `false`): by default the link import
  only connects to internet addresses, so it cannot be used to reach devices in
  the local network.
- API: `POST /api/downloads`, `GET /api/downloads`,
  `DELETE /api/downloads/{id}`.

## [1.1.1] - 2026-10-18

### Fixed

- Deleting a backup whose folder cannot be removed (for example because of
  file permissions in `DATA_DIR/backups`) is now reported as an error and
  logged. Before, the dashboard reported success while the folder stayed on
  disk.

## [1.1.0] - 2026-10-18

### Added

- RAR (4 and 5), 7z and tar archives (`.tar`, `.tar.gz`/`.tgz`,
  `.tar.bz2`/`.tbz2`, `.tar.xz`/`.txz`) can be imported like ZIP files, by
  upload and through the inbox folder. They are read with libarchive, which
  the Docker image now contains; without it only ZIP works. The same checks
  apply to every format, and password protected and split archives are
  refused with a clear reason.
- Delete a game in its edit dialog. Admins delete it for everyone, with its
  folder and backups. Other accounts remove it from their own library; its
  files are deleted only if no other account uses the game.
- Backups: the edit dialog shows the size of each saved version and can delete
  it. **Backups** in the toolbar lists the backups of all games, largest first.
  Admins also see backup folders that no game refers to any more and can
  delete them.
- **Overview** for admins: which account has which games, with that account's
  last start and number of starts, and the games nobody owns yet.
- A GitHub link in the top bar.
- API: `POST /api/games/{id}/remove`, `GET /api/backups`,
  `DELETE /api/backups/{id}`, `DELETE /api/backups?path=…`,
  `GET /api/overview`; `GET /api/imports/limits` also returns the supported
  archive endings.

### Changed

- `DELETE /api/games/{id}` deletes the game with its folder and backups
  instead of removing only entries whose folder is missing. Removing the entry
  of a missing folder now deletes its backups as well.
- **Import ZIP** is now called **Import game**.

### Fixed

- In the edit dialog of a game without a cover image, the placeholder no longer
  takes the whole row and squeezes the image controls into a narrow column.

## [1.0.2] - 2026-10-18

### Fixed

- F95Zone update check: every F95Zone thread page now asks for a browser
  check (bot protection), so the check failed with error 403. Playshelf now
  reads the version and the time of the last update from F95Zone's
  "Latest Updates" data, found by the game's title and matched by the thread
  number in the update link. Titles with special characters such as `!`,
  `~` or `&` are searched in several spellings.
- A page that asks for a browser check is shown as "check by hand", with a
  link to the page, instead of a red error. Bot protection is still never
  circumvented.
- Uploading a ZIP file that is larger than `MAX_UPLOAD_MB` is refused in the
  browser at once, with the limit in the message, instead of after the
  upload. A reverse proxy that rejects the file (error 413) or stops reading
  it no longer leaves the upload stuck: the message names the proxy as the
  likely cause.

### Added

- `GET /api/imports/limits` returns the upload and extraction limits.
- Documentation: the reverse proxy section covers Nginx Proxy Manager step by
  step and how `MAX_UPLOAD_MB`, `MAX_EXTRACTED_MB` and the proxy limit fit
  together for large games.

## [1.0.1] - 2026-10-18

### Changed

- `docker compose up -d` pulls the published image from the GitHub Container
  Registry instead of building it; `docker compose up -d --build` still builds
  from source. `PLAYSHELF_VERSION` picks the image tag.
- The README starts with a one-command `docker run` quick start.

### Fixed

- The buttons at the top right of the page: the color scheme button showed a
  symbol that some systems draw as an empty box or a thin sliver; it now uses
  an icon that looks the same everywhere. The account button has a person
  icon, and on narrow screens the buttons wrap below the title instead of
  running off the page.

## [1.0.0] - 2026-10-18

First public release.

### Added

- Game library: every subfolder of the games folder is a game; start file,
  title, version hint and cover image are detected automatically.
- One-click start, last played, play count, favourites and play status.
- Installed version per game with a version history; updates keep the old
  version as a backup that can be restored.
- ZIP import through the browser or an inbox folder, with safety checks
  before anything touches the library.
- Online update check for itch.io and Patreon; F95Zone and TFGames are
  available as opt-in sources (`ADULT_SOURCES=true`).
- Statistics: most played, not played for a long time, disk usage.
- User accounts with the roles admin and user: everyone sees their own and
  shared games, with their own last played, favourites, status and
  statistics; each account has its own inbox subfolder. Admins see all games,
  share them and manage accounts. A first-start setup page creates the admin;
  `AUTH=none` turns login off. An existing `PASSWORD_HASH` becomes the admin's
  password and keeps all existing data with that account.
- Daily database backup.
- Docker image for `linux/amd64` and `linux/arm64`, published to the GitHub
  Container Registry, and a Docker Compose setup with an optional Samba share
  for the inbox.

[Unreleased]: https://github.com/nan-gits/playshelf/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/nan-gits/playshelf/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/nan-gits/playshelf/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/nan-gits/playshelf/compare/v1.0.2...v1.1.0
[1.0.2]: https://github.com/nan-gits/playshelf/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/nan-gits/playshelf/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/nan-gits/playshelf/releases/tag/v1.0.0
