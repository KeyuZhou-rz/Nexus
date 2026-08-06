# Security Policy

## Supported version

Nexus `0.3.x` Private Beta receives security fixes. Earlier development
snapshots are unsupported.

## Reporting

Do not post credentials, tokens, private course material, database files, or
debug screenshots in a public issue. Report a vulnerability privately to the
repository owner or through the repository host's private security advisory
feature. Include reproduction steps with synthetic data whenever possible.

## Local deployment guidance

- Keep `.env`, OAuth credentials, tokens, feed URLs, `data/`, and backups out of
  source control.
- Run `nexus doctor --strict` after configuring credentials.
- Keep credential files owner-readable only (`chmod 600`).
- Bind Streamlit to localhost unless remote access is protected separately.
- Leave LLM integration disabled unless the configured provider is acceptable
  for the course content being processed.
- Use a dedicated Python environment and install releases from a trusted source.
