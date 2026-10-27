# Security

Task Continuity Agent is a local automation harness, not a security sandbox.

## Boundary

The agent runs with the permissions of the signed-in Windows user. The built-in policy blocks Administrator escalation and several destructive system commands, but it cannot make arbitrary normal-user automation risk-free.

Keep backups of valuable files. Review third-party MCP servers before enabling them. Do not expose the loopback UI or model API directly to untrusted networks.

Agent instructions require human authorization or participation for UAC, login, CAPTCHA, purchases, publishing, external messages, and other direct authorization steps. This policy depends on model behavior; it is not a technical permission boundary for arbitrary browser or shell actions.

## Reporting

For a public release, report vulnerabilities through the repository's GitHub Security Advisory feature rather than posting exploit details in a public issue.
