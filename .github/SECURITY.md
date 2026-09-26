# Security policy

## Reporting a vulnerability

Report privately through this repository's **Security → Report a vulnerability** form (GitHub private vulnerability reporting). Please do not open public issues for security problems.

Include the Northkey commit (`northkey --version` or `git rev-parse HEAD` in the install), the affected component, and the impact.

## Scope

| Where the issue lives | Where it goes |
|---|---|
| Northkey's own layer (`northkey/`, `hermes_cli/northkey_brand.py`, the seams listed in `northkey/seams.yaml`, the update channel records) | Here |
| Upstream Hermes Agent code | Here as well; the maintainers coordinate with Nous Research through their policy ([SECURITY.md](../SECURITY.md)) so the fix lands upstream and reaches Northkey on the next sync |

## Supported versions

Only the current `main` branch receives fixes. Installs track `main` and update with `northkey update`.

## Hardening Northkey ships by default

- Dangerous commands always need your approval (`approvals.mode: manual`).
- Messaging gateways only send files from allow-listed locations (`gateway.strict: true`).
- No keyless third-party web search and no borrowing of other tools' logins.
- Skills written by the agent are security-scanned.
- Installs update only from this repository's `main`: the built-in update channels resolve to it directly, and no upstream server is consulted.

The operating system remains the real security boundary for an agent that can run commands. For shared or server machines, run Northkey in a container or a dedicated account.
