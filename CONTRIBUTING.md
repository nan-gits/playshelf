# Contributing to Playshelf

Thanks for taking the time to help. Bug reports, ideas, documentation fixes
and code are all welcome.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Reporting bugs and asking for features

- Search the [existing issues](https://github.com/nan-gits/playshelf/issues)
  first; maybe someone has already reported it.
- Use the issue templates. For bugs, include the Playshelf version (shown at
  `/api/health`), how you run it (Docker or Python) and the steps to reproduce
  it.
- Please do not attach copyrighted games. If a game triggers a bug, describe
  its folder layout (file names, start file) instead, or build a tiny test game
  that shows the same problem.
- Security problems do not belong in public issues; see [SECURITY.md](SECURITY.md).

## Development setup

Playshelf needs Python 3.11 or newer.

```bash
git clone https://github.com/nan-gits/playshelf.git
cd playshelf
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
uvicorn app.main:app --reload    # development server with auto-reload
```

Before you open a pull request, run the same checks as CI:

```bash
ruff check
ruff format --check   # without --check: format the code
pytest
docker build -t playshelf:dev .   # if you changed the Dockerfile or dependencies
```

## Pull requests

- Open an issue first for larger changes, so we can agree on the approach
  before you spend time on it.
- Keep a pull request focused on one change. Add or update tests for
  behaviour changes.
- Code, comments, commit messages and documentation are in English. User
  interface strings go into both the English and the German table.
- Add a line to the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md) for
  anything a user would notice.
- Test data must be made up or freely licensed. Never commit real games,
  personal paths or account names.

## Licence

Playshelf is licensed under the [GNU Affero General Public License v3.0 or
later](LICENSE). By submitting a contribution you agree that it is released
under the same licence.
