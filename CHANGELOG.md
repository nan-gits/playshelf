# Changelog

All notable changes to Playshelf are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/nan-gits/playshelf/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/nan-gits/playshelf/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/nan-gits/playshelf/releases/tag/v1.0.0
