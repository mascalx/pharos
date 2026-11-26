# Security

PHAROS is intended for trusted LAN/VPN deployments. It does not provide built-in user authentication. Do not expose the application, Qdrant or Kiwix directly to the public Internet. Protect configuration, secrets and backups with filesystem and network access controls.

For suspected vulnerabilities, use GitHub private vulnerability reporting if enabled; otherwise contact the maintainer privately before disclosing technical details. Never publish API keys, personal documents or exploit details in a public issue. Include the affected version, a minimal reproduction using synthetic data, and impact.

See [security architecture](docs/SECURITY.md) and the user manuals for operational guidance.
