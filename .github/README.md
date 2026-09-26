<div align="center">

# ✦ Northkey

**Quiet intelligence, pointed north.**

A self-improving AI agent for your terminal, messaging apps and desktop, with secure defaults, a calm design and continuous updates.

</div>

---

## Install

**macOS / Linux / WSL**

```bash
curl -fsSL https://raw.githubusercontent.com/zerosec-ai/northkey-agent/main/scripts/install.sh | bash
```

**Windows (PowerShell)**

```powershell
iex (irm https://raw.githubusercontent.com/zerosec-ai/northkey-agent/main/scripts/install.ps1)
```

Then start a session:

```bash
northkey
```

The `hermes` command is installed alongside `northkey` and does exactly the same thing, so scripts written for Hermes keep working.

## Updates

```bash
northkey update
```

Northkey tracks upstream Hermes Agent continuously. The maintainers merge the newest upstream several times a day, re-apply the Northkey layer automatically, and publish it to `main`. Your install fast-forwards to it; nothing is ever force-pushed.

**Coming from Hermes Agent?** Point your existing install at Northkey once, then update as usual:

```bash
cd ~/.hermes/hermes-agent   # Windows: %LOCALAPPDATA%\hermes\hermes-agent
git remote set-url origin https://github.com/zerosec-ai/northkey-agent.git
hermes update --branch main    # one time only; plain `northkey update` from then on
```

## What is different from Hermes Agent

| | Hermes Agent default | Northkey default |
|---|---|---|
| Dangerous-command approval | `smart` (a second LLM may approve) | `manual`: you approve every flagged command |
| Files sent as chat media | anything outside a denylist | only allow-listed locations (`gateway.strict`) |
| Keyless third-party search tiers | on | off: no queries leave without a configured backend |
| Borrowing Claude Code / Codex logins | on | off: Northkey uses only its own credentials |
| Skills the agent writes | not scanned | security-scanned |
| Look | Hermes gold | Northkey graphite, glacier and champagne |

Every default is a normal setting you can change in `config.yaml`.

## Documentation

Northkey follows upstream features one-to-one, so the [Hermes Agent documentation](https://hermes-agent.nousresearch.com/docs) applies. Maintainers: see [northkey/README.md](../northkey/README.md).

## License and credits

Northkey is built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research and is distributed under the same [MIT License](../LICENSE). See [NOTICE](../NOTICE). Northkey is independent and not endorsed by Nous Research.
