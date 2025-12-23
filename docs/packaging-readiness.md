# Packaging readiness (Debian/Kali-oriented)

This project is structured to make packaging straightforward later, even if you are not packaging it today.

## Already in place
- PEP 621 `pyproject.toml`
- CLI entry point (`[project.scripts]`)
- deterministic output behaviors (stdout vs stderr)
- manpage in `man/wayparam.1`
- offline test suite

## Common next steps for Debian/Kali packaging
- install manpage into `/usr/share/man/man1/wayparam.1.gz`
- add shell completions (bash/zsh/fish)
- ensure license files and third-party notices are included
- pin or validate dependency versions if needed
- provide a `debian/` directory (when you decide to package)

## Release checklist (recommended)
- update version and changelog
- run `ruff` + `pytest`
- verify `wayparam --help` and manpage content
