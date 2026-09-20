# Security Policy

Please do **not** publish details of an undisclosed wayparam vulnerability in a
public issue.

Use GitHub's private vulnerability reporting / security-advisory flow when the
repository exposes **Security → Report a vulnerability**. If that option is not
available, open a minimal public issue asking for a private contact channel and
do not include exploit details, sensitive logs, tokens, target data or a PoC in
that issue.

The latest released version and the current main branch are the supported
targets for security fixes.

wayparam itself only queries the Internet Archive Wayback CDX API; it does not
crawl or actively scan target systems. Reports should concern wayparam's own
code, packaging or local web interface.
