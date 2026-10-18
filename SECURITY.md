# Security policy

## Supported versions

Security fixes go into the latest release. Please update to it before
reporting a problem.

## Reporting a vulnerability

Please do **not** open a public issue for security problems.

Report it privately through GitHub instead:
[Report a vulnerability](https://github.com/nan-gits/playshelf/security/advisories/new).
Include what you found, how to reproduce it and the version you tested.

You will get an answer as soon as possible, usually within a week. Once a fix
is released, the advisory is published and you are credited unless you prefer
not to be.

## Scope

Playshelf is meant for a home network. Things worth reporting include, for
example:

- ways around the password protection,
- reading or writing files outside the games, data or inbox folders (for
  example through crafted ZIP archives or game paths),
- script injection through game titles, file names or update pages.

Running Playshelf openly on the internet without password protection or a
reverse proxy with TLS is not supported; see
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the recommended setup.
