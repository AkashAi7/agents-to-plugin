# agents-to-plugin

Universal converter that wraps GitHub Copilot **custom agents** into a distributable **plugin + marketplace** — and keeps everything in sync as your agent collection grows.

Drop your `*.agent.md` files into an `agents/` folder, run one script, and get a complete, installable Copilot plugin setup:

```text
your-repo/
├── agents/                                  ← you write these
│   └── my-agent.agent.md
├── .github/plugin/marketplace.json          ← generated: plugin marketplace manifest
├── plugin.json                              ← generated: Agent Plugins 1.0 manifest
├── com.github.copilot/agents/*.agent.md     ← generated: Copilot-namespace mirrors
└── copilot/managed-settings.json            ← generated: enterprise auto-distribution
```

## Why

GitHub Copilot supports distributing custom agents via [plugin marketplaces](https://docs.github.com/copilot/concepts/agents/copilot-cli/about-cli-plugins), but wiring up the manifests by hand is fiddly and error-prone — and every time you add, rename, or delete an agent you have to touch four files. These scripts automate the whole chain.

## Quick start

From the root of any git repository that has an `agents/` directory and a GitHub `origin` remote:

```bash
# Python (any OS, Python 3.9+, stdlib only)
python scripts/sync-agents-to-plugin.py

# or PowerShell
./scripts/sync-agents-to-plugin.ps1
```

Then commit and push the generated files. Done — your agents are now an installable plugin:

```bash
copilot plugin marketplace add <owner>/<repo>
copilot plugin install <owner>-agents@<owner>-marketplace
```

## What the sync script does

On every run it:

1. **Discovers agents** — top-level Markdown files in `agents/` with closed YAML frontmatter (`---` … `---`). Commented-out templates and plain docs are skipped automatically.
2. **Creates or updates `.github/plugin/marketplace.json`** — the marketplace is created if missing (named `<owner>-marketplace` from your git remote); existing names, authors, and unrelated plugins are preserved.
3. **Creates or updates `plugin.json`** — the Agent Plugins 1.0 manifest (named `<owner>-agents` when created).
4. **Mirrors agents into `com.github.copilot/agents/`** — and prunes stale mirrors for agents you deleted, without touching unmanaged files.
5. **Populates `copilot/managed-settings.json`** — registers the marketplace and enables the plugin so GitHub Enterprise managed clients receive it automatically (existing settings are merged, never overwritten).
6. **Validates** all generated JSON and every referenced agent path.

It is **idempotent**: re-running without changes produces byte-identical output. It is **scale-ready**: add, amend, or delete agents and re-run to repopulate everything.

### Options

| Python | PowerShell | Effect |
|---|---|---|
| `--marketplace-name NAME` | `-MarketplaceName NAME` | Name used when *creating* a marketplace (existing names are kept) |
| `--plugin-name NAME` | `-PluginName NAME` | Name used when *creating* a plugin manifest |
| `--install` | `-Install` | Also register + install + enable the plugin in your local Copilot CLI |

> `--install` requires the GitHub Copilot CLI to be installed and signed in, and the repo must already be pushed to GitHub (the CLI installs from the remote).

## Local install/uninstall helper

`scripts/setup-plugin.ps1` is a standalone idempotent installer for consumers of your plugin (defaults are wired for its original repo — edit the three variables at the top for yours):

```powershell
./scripts/setup-plugin.ps1                  # register marketplace + install + enable
./scripts/setup-plugin.ps1 -Action Remove   # uninstall + unregister
```

## Requirements

- git, with a GitHub `origin` remote (`https`, `ssh`, or `git@` forms all work)
- **Python script**: Python 3.9+ (standard library only — no pip installs)
- **PowerShell script**: PowerShell 5.1+ / PowerShell Core
- GitHub Copilot CLI — only for `--install` / `setup-plugin.ps1`

## Agent file format

Only files like this count as agents (anything else in `agents/` is ignored):

```markdown
---
name: My Agent
description: What this agent does and when to use it.
---
You are a specialist at ...
```

## Tests

```bash
python -B -m unittest discover -s scripts -p "test_sync_agents_to_plugin.py"
```

The suite covers from-scratch creation, idempotence, add/amend/remove cycles, preservation of existing metadata and unrelated plugins, malformed-input safety, and the install flow (mocked — no local CLI state is touched).

## License

MIT
