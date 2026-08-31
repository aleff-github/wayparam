# Changelog

## 0.3.1
- Fixed BrokenPipeError traceback when the consumer closes the pipe (`--stdout | head`); exits 141 instead
- Added snap packaging (strict confinement)
- Added Debian packaging (PPA, standalone .deb, Debian/Kali)
- Added release workflow for PyPI, Snap Store and GitHub Releases

## 0.3.0
- Milestone

## 0.2.3
- Added httpx-level integration tests using MockTransport (no network)

## 0.2.2
- Fixed pyproject.toml table ordering (project.urls) for editable installs

## 0.2.1
- Added man page (man/wayparam.1)

## 0.2.0
- Added output formats (txt/jsonl) and safer diagnostics to stderr
- Added per-domain stats (optional)
- Improved HTTP error messages with status/no-status
- Added basic tests and CI config

## 0.1.0
- Initial release
