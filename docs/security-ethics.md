# Security & ethics

wayparam is designed as an OSINT collection tool.

- It queries the Internet Archive (Wayback CDX).
- It does not actively scan or crawl target domains.

Nevertheless, how you use collected URLs matters:
- respect laws and policies in your jurisdiction
- avoid unauthorized testing of systems you do not own or have permission to assess
- be mindful of privacy (parameter values can contain identifiers)

Defaults are chosen to be safer:
- values are replaced with a placeholder unless you explicitly request `--keep-values`
- tracking parameters are dropped by default
