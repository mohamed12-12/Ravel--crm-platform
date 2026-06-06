# Security Policy

## Supported Versions

Only the current `main` branch is considered supported.

## Reporting a Vulnerability

If you discover a vulnerability, report it privately to the repository owner before publishing details publicly.

Include:

- A short summary of the issue
- The affected files or endpoints
- Reproduction steps
- Any suggested remediation

## Handling Secrets

- Do not commit `.env` files, service-account JSON files, certificates, or API keys.
- Rotate any credential that has been exposed in a local workspace or commit history.
- Keep production credentials in a secret manager or deployment environment variables.
