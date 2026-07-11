# Security Policy

## Supported versions

Only the latest Wandao release and the current `main` branch receive security fixes.

## Reporting a vulnerability

Please do not open a public Issue for credential exposure, arbitrary code execution, plugin signature bypass, path traversal, or private-content leakage.

Use GitHub's private vulnerability reporting for this repository, or email `tl200599@163.com` with the subject `Wandao Security`. Include the affected version, operating system, reproduction steps, expected impact, and a minimal redacted proof of concept. Do not include real Cookie, Token, password, App Secret, API Key, or private knowledge-base content.

The maintainer will acknowledge a complete report within 3 working days, provide a triage decision within 7 working days, and coordinate disclosure after a fix is available.

## Security boundaries

- Official bundled plugins are distributed with the application.
- Installed plugin updates must pass SHA-256 and Ed25519 verification.
- Plugin permissions are enforced progressively; users should install only trusted plugins.
- Wandao does not bypass authentication or access controls and should only process content the user is authorized to access.
