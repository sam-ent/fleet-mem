# Security Policy

## Reporting vulnerabilities

Please report security vulnerabilities via [GitHub Security Advisories](https://github.com/sam-ent/fleet-mem/security/advisories/new) rather than public issues.

## Response timeline

- Acknowledge within 48 hours
- Patch within 7 days for critical vulnerabilities
- Coordinated disclosure after fix is released

## Supported versions

Only the latest release receives security updates.

## Scope

fleet-mem runs locally and does not make network requests except to a user-configured Ollama instance. Security concerns include:
- Path traversal via MCP tool inputs
- SQL injection in memory queries
- Symlink following during file indexing
- Shell injection in scripts
