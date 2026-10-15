# agents-to-plugin

Universal converter that wraps GitHub Copilot **custom agents** into a distributable **plugin + marketplace** — and keeps everything in sync as your agent collection grows.

Drop your `*.agent.md` files into an `agents/` folder, run one script, and get a complete, installable Copilot plugin setup:

```text
your-repo/
├── agents/                                  ← you write these (*.agent.md)
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

# or the PowerShell wrapper (delegates to the same Python engine)
./scripts/sync-agents-to-plugin.ps1
```

Then commit and push the generated files. Done — your agents are now an installable plugin:

```bash
copilot plugin marketplace add <owner>/<repo>
copilot plugin install <owner>-agents@<owner>-marketplace
```

## What the sync script does

On every run it:

1. **Resolves the target repository** — `--root PATH` if given, else the current `git rev-parse --show-toplevel`, else the script's parent repository. The resolved root is printed before anything is touched.
2. **Discovers agents** — `agents/*.agent.md` files with closed YAML frontmatter containing a `description`. A malformed `*.agent.md` is a **hard error** (so a typo can't silently drop an agent from the plugin); other Markdown files are ignored with a notice.
3. **Validates everything up front** — all existing manifests and settings are parsed and type/name/version-checked *before* any file is modified; failures leave the repository untouched.
4. **Creates or updates `plugin.json`** — Agent Plugins 1.0 manifest (named `<owner>-agents` when created; existing names win). Names must be lowercase kebab/dot style.
5. **Creates or updates `.github/plugin/marketplace.json`** — the plugin entry's `agents` **and `version`** are synchronized; existing metadata, authors, and unrelated plugins are preserved. Duplicate entries or a non-root plugin `source` are rejected (`./` and `.` are both accepted).
6. **Mirrors agents into `com.github.copilot/agents/`** — pruning only mirrors the converter itself previously registered; unmanaged files are never touched; symlinks are refused.
7. **Merges `copilot/managed-settings.json`** — registers the marketplace (`autoUpdate: true`) and enables `plugin@marketplace`. On a rename, stale entries pointing at this same repository are cleaned up; settings for other repositories are never modified.
8. **Writes atomically** — JSON files are written via same-directory temp files and `os.replace`.

### Version policy

Enterprise clients rely on version metadata to pick up updates, so the converter manages it:

- New manifests start at `1.0.0`.
- If any agent content or the agent list changed since the last run, the patch version is bumped automatically.
- `--version X.Y.Z` sets it explicitly (e.g. for minor/major bumps).
- The marketplace entry's `version` always mirrors `plugin.json`.

Re-running with no changes is byte-identical (no spurious bumps).

### Options

| Python | PowerShell | Effect |
|---|---|---|
| `--root PATH` | `-Root PATH` | Target repository (default: current git toplevel) |
| `--marketplace-name NAME` | `-MarketplaceName NAME` | Name used when *creating* a marketplace |
| `--plugin-name NAME` | `-PluginName NAME` | Name used when *creating* a plugin manifest |
| `--version X.Y.Z` | `-Version X.Y.Z` | Explicit version (default: auto patch-bump on change) |
| `--install` | `-Install` | Also register + install + enable in the local Copilot CLI |

> `--install` requires the GitHub Copilot CLI to be installed and signed in, and the repo must already be pushed to GitHub (the CLI installs from the remote).

**Single engine:** `sync-agents-to-plugin.ps1` is a thin launcher around the Python script — there is exactly one implementation of the conversion logic, and the test suite covers it on every platform. The wrapper requires Python 3.9+ on PATH.

## Local install/uninstall helper

`scripts/setup-plugin.ps1` installs/uninstalls the plugin a repository publishes. Run it from that repository's root — it derives the repo slug, marketplace, and plugin names from the generated manifests and git remote (all overridable via `-Repo`, `-MarketplaceName`, `-PluginName`):

```powershell
./scripts/setup-plugin.ps1                  # register marketplace + install + enable
./scripts/setup-plugin.ps1 -Action Remove   # uninstall + unregister
```

## Requirements

- git, with a GitHub `origin` remote (`https`, `ssh`, or `git@` forms all work)
- Python 3.9+ (standard library only — no pip installs); PowerShell 5.1+/Core for the wrappers
- GitHub Copilot CLI — only for `--install` / `setup-plugin.ps1`

## Agent file format

Only `agents/*.agent.md` files count as agents, and they must look like this:

```markdown
---
name: My Agent
description: What this agent does and when to use it.
---
You are a specialist at ...
```

A `*.agent.md` without valid frontmatter or a description fails the run. Other `.md` files (docs, commented-out templates) are listed as skipped and otherwise ignored.

## Tests

```bash
python -B -m unittest discover -s scripts -p "test_sync_agents_to_plugin.py"
```

13 cases cover from-scratch creation, byte-identical idempotence, version bump/propagation, add/amend/remove cycles, unmanaged-file preservation, metadata/other-plugin preservation, rename cleanup of managed settings, malformed-input safety with zero partial writes, source-path equivalence, `--root`/git-toplevel resolution, exact-token CLI matching, the mocked install flow, and wrapper delegation.

## Known limitations

- Frontmatter parsing is a deliberate subset of YAML (simple `key: value` lines); block scalars and multi-line values are not interpreted.
- Removing the *last* agent aborts rather than tearing down generated files — delete them manually (or `git revert`) for teardown.
- End-to-end client auto-update behavior should be verified against a real Copilot client; the test suite validates manifests, not client delivery.

## License

MIT
