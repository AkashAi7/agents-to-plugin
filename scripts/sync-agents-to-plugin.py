#!/usr/bin/env python3
"""Convert Copilot custom agents into a plugin marketplace setup.

Generates/updates .github/plugin/marketplace.json, plugin.json,
com.github.copilot/agents/ mirrors, and copilot/managed-settings.json
from the selected agent source folder (agents/ by default). Stdlib only;
idempotent; validates all inputs before mutating anything.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def run(command: list[str], root: Path) -> str:
    result = subprocess.run(
        command, cwd=root, check=True, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return result.stdout


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def write_object(path: Path, value: dict[str, Any]) -> None:
    """Write JSON atomically via a same-directory temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temp_name, path)
    except BaseException:
        os.unlink(temp_name)
        raise


def check_name(kind: str, name: Any) -> str:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"{kind} name {name!r} must be 2-64 lowercase letters, digits, '.', '_' or '-'"
        )
    return name


def check_version(version: Any) -> str:
    if not isinstance(version, str) or not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"Version {version!r} must be MAJOR.MINOR.PATCH digits")
    return version


def bump_patch(version: str) -> str:
    major, minor, patch = VERSION_PATTERN.fullmatch(version).groups()
    return f"{major}.{minor}.{int(patch) + 1}"


def resolve_agents_directory(root: Path, agents_dir: str | Path) -> Path:
    root = root.resolve()
    directory = Path(agents_dir)
    if not directory.is_absolute():
        directory = root / directory
    directory = directory.resolve()
    try:
        directory.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Agent directory must be inside the repository root: {directory}") from error
    return directory


def discover_agents(root: Path, agents_dir: str | Path = "agents") -> list[tuple[Path, str]]:
    """Return (path, display name) for every *.agent.md file in the source folder.

    An *.agent.md file with missing, unopened, or unclosed frontmatter, or
    without a description, is an error. Other Markdown files are ignored.
    """
    directory = resolve_agents_directory(root, agents_dir)
    if not directory.is_dir():
        raise ValueError(f"No agents directory found at {directory}")
    agents = []
    skipped = []
    for path in sorted(directory.glob("*.md"), key=lambda item: item.name.casefold()):
        if not path.is_file():
            continue
        if not path.name.endswith(".agent.md"):
            skipped.append(path.name)
            continue
        lines = path.read_text(encoding="utf-8-sig").lstrip().splitlines()
        if not lines or lines[0].strip() != "---":
            raise ValueError(f"{path}: agent file must start with '---' YAML frontmatter")
        end = next(
            (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
            None,
        )
        if end is None:
            raise ValueError(f"{path}: frontmatter has no closing ---")
        fields = {}
        for line in lines[1:end]:
            match = re.match(r"^(name|description):\s*(.+)$", line)
            if match:
                fields[match[1]] = match[2].strip().strip("\"'")
        if not fields.get("description"):
            raise ValueError(f"{path}: frontmatter must define a non-empty description")
        agents.append((path, fields.get("name") or path.name.removesuffix(".agent.md")))
    if skipped:
        print(f"Skipped {len(skipped)} non-agent Markdown file(s): {', '.join(skipped)}")
    if not agents:
        raise ValueError(
            f"No *.agent.md files found in {directory}. "
            "Existing generated manifests were left untouched; remove them manually to tear down."
        )
    return agents


def resolve_slug(root: Path) -> tuple[str, str]:
    remote = run(["git", "remote", "get-url", "origin"], root).strip()
    match = re.fullmatch(
        r"(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"([^/\s]+)/([^/\s]+?)(?:\.git)?/?", remote,
    )
    if not match:
        raise ValueError("The origin remote must identify a GitHub owner/repository")
    owner, repository = match.groups()
    return owner, f"{owner}/{repository}"


def is_root_source(source: Any) -> bool:
    return isinstance(source, str) and source.strip().rstrip("/") in ("", ".")


def sync(
    root: Path,
    marketplace_name: str | None = None,
    plugin_name: str | None = None,
    version: str | None = None,
    agents_dir: str | Path = "agents",
) -> tuple[str, str, str]:
    root = root.resolve()
    agent_directory = resolve_agents_directory(root, agents_dir)
    agents = discover_agents(root, agent_directory)
    owner, slug = resolve_slug(root)
    url = f"https://github.com/{slug}"
    source_paths = [f"./{path.relative_to(root).as_posix()}" for path, _ in agents]
    namespace_paths = [f"./com.github.copilot/agents/{path.name}" for path, _ in agents]
    names = ", ".join(name for _, name in agents)
    summary = names if len(names) <= 120 else f"{len(agents)} agents"
    source_directory = agent_directory.relative_to(root).as_posix()
    description = (
        f"Custom agents for {owner}, auto-generated from {source_directory}/. "
        f"Includes: {summary}."
    )

    # --- Phase 1: read and validate every input before mutating anything ---
    manifest_path = root / "plugin.json"
    marketplace_path = root / ".github" / "plugin" / "marketplace.json"
    settings_path = root / "copilot" / "managed-settings.json"
    if version is not None:
        check_version(version)

    manifest = read_object(manifest_path) if manifest_path.exists() else {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": check_name("Plugin", plugin_name or f"{owner}-agents"),
        "version": "1.0.0",
        "description": description,
        "repository": url,
        "homepage": f"{url}#readme",
        "keywords": ["copilot", "custom-agents", owner],
    }
    marketplace = read_object(marketplace_path) if marketplace_path.exists() else {
        "name": check_name("Marketplace", marketplace_name or f"{owner}-marketplace"),
        "owner": {"name": owner},
        "metadata": {
            "description": f"Agent plugin marketplace for {owner}.",
            "version": "1.0.0",
        },
        "plugins": [],
    }
    settings = read_object(settings_path) if settings_path.exists() else {}

    plugin_name = check_name("Plugin", manifest.get("name"))
    marketplace_name = check_name("Marketplace", marketplace.get("name"))
    old_version = check_version(manifest.get("version", "1.0.0"))
    extensions = manifest.setdefault("extensions", {})
    if not isinstance(extensions, dict):
        raise ValueError("plugin.json extensions must be an object")
    old_copilot = extensions.get("com.github.copilot", {})
    if not isinstance(old_copilot, dict):
        raise ValueError("plugin.json extension com.github.copilot must be an object")
    old_paths = old_copilot.get("agents", [])
    if not isinstance(old_paths, list) or any(not isinstance(p, str) for p in old_paths):
        raise ValueError("Existing com.github.copilot agents must be an array of strings")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or any(not isinstance(p, dict) for p in plugins):
        raise ValueError("marketplace.json plugins must be an array of objects")
    matches = [plugin for plugin in plugins if plugin.get("name") == plugin_name]
    if len(matches) > 1:
        raise ValueError(f"Duplicate marketplace entries for {plugin_name}")
    if matches and not is_root_source(matches[0].get("source")):
        raise ValueError(
            f"{plugin_name}: marketplace source {matches[0].get('source')!r} does not point "
            "at the repository root; this converter only manages root-sourced plugins"
        )
    marketplaces = settings.setdefault("extraKnownMarketplaces", {})
    enabled = settings.setdefault("enabledPlugins", {})
    if not isinstance(marketplaces, dict) or not isinstance(enabled, dict):
        raise ValueError("managed-settings.json marketplace/plugin sections must be objects")

    # --- Phase 2: detect content changes to drive the version policy ---
    mirror = root / "com.github.copilot" / "agents"
    changed = namespace_paths != old_paths
    for path, _ in agents:
        destination = mirror / path.name
        if destination.is_symlink():
            raise ValueError(f"Refusing to overwrite symlink: {destination}")
        if not destination.is_file() or destination.read_bytes() != path.read_bytes():
            changed = True
    if version is not None:
        new_version = version
    elif manifest_path.exists() and changed:
        new_version = bump_patch(old_version)
    else:
        new_version = old_version
    manifest["version"] = new_version

    # --- Phase 3: build remaining output state in memory ---
    for namespace, paths in (
        ("com.github.copilot", namespace_paths),
        ("com.github.awesome-copilot", source_paths),
    ):
        extension = extensions.setdefault(namespace, {})
        if not isinstance(extension, dict):
            raise ValueError(f"plugin.json extension {namespace} must be an object")
        extension["agents"] = paths
    if matches:
        entry = matches[0]
    else:
        entry = {
            "name": plugin_name, "description": description,
            "repository": url, "source": "./",
        }
        plugins.append(entry)
    entry["version"] = new_version
    entry["agents"] = source_paths

    marketplace_plugins = {p.get("name") for p in plugins}
    for stale in [
        key for key, value in marketplaces.items()
        if key != marketplace_name and isinstance(value, dict)
        and value.get("source", {}).get("repo") == slug
    ]:
        del marketplaces[stale]
        for qualified in [k for k in enabled if k.endswith(f"@{stale}")]:
            del enabled[qualified]
        print(f"Removed stale managed-settings entries for renamed marketplace: {stale}")
    for qualified in [
        k for k in enabled
        if k.endswith(f"@{marketplace_name}") and k.rsplit("@", 1)[0] not in marketplace_plugins
    ]:
        del enabled[qualified]
        print(f"Removed stale enabled plugin: {qualified}")
    marketplaces[marketplace_name] = {
        "source": {"source": "github", "repo": slug},
        "autoUpdate": True,
    }
    enabled[f"{plugin_name}@{marketplace_name}"] = True

    # --- Phase 4: mutate (mirrors first, then atomic JSON writes) ---
    mirror.mkdir(parents=True, exist_ok=True)
    for path, _ in agents:
        shutil.copyfile(path, mirror / path.name)
    for old in old_paths:
        candidate = root.joinpath(*old.removeprefix("./").split("/"))
        if (
            old not in namespace_paths and candidate.parent == mirror
            and candidate.suffix == ".md" and not candidate.is_symlink()
            and candidate.is_file()
        ):
            candidate.unlink()
            print(f"Removed stale mirrored agent: {candidate.name}")
    write_object(manifest_path, manifest)
    write_object(marketplace_path, marketplace)
    write_object(settings_path, settings)
    print(
        f"Marketplace: {marketplace_name}\nPlugin: {plugin_name} (v{new_version})\n"
        f"Agents ({len(agents)}): {names}\nManaged settings: {settings_path}"
    )
    return slug, marketplace_name, plugin_name


def cli_has_entry(output: str, name: str) -> bool:
    """Exact-token match against copilot CLI list output (not substring)."""
    return re.search(rf"(?m)(^|[\s•◆]){re.escape(name)}([\s(]|$)", output) is not None


def install(root: Path, slug: str, marketplace: str, plugin: str) -> None:
    executable = shutil.which("copilot")
    if executable is None:
        raise ValueError("GitHub Copilot CLI is required for --install; install it and sign in")
    qualified = f"{plugin}@{marketplace}"
    if not cli_has_entry(run([executable, "plugin", "marketplace", "list"], root), marketplace):
        print(run([executable, "plugin", "marketplace", "add", slug], root), end="")
    if not cli_has_entry(run([executable, "plugin", "list"], root), qualified):
        print(run([executable, "plugin", "install", qualified], root), end="")
    print(run([executable, "plugin", "enable", qualified], root), end="")


def resolve_root(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit).resolve()
        if not root.is_dir():
            raise ValueError(f"--root {explicit} is not a directory")
        return root
    try:
        toplevel = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], check=True,
            capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()
        if toplevel:
            return Path(toplevel).resolve()
    except (OSError, subprocess.CalledProcessError):
        pass
    return Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", help="Repository root (default: current git toplevel)")
    parser.add_argument(
        "--agents-dir", default="agents",
        help="Agent source folder inside the repository (default: agents)",
    )
    parser.add_argument("--marketplace-name", help="Name when creating a marketplace")
    parser.add_argument("--plugin-name", help="Name when creating a plugin")
    parser.add_argument("--version", help="Explicit plugin version (default: bump patch on change)")
    parser.add_argument("--install", action="store_true", help="Also install/enable via Copilot CLI")
    args = parser.parse_args()
    try:
        root = resolve_root(args.root)
        print(f"Target repository: {root}")
        result = sync(
            root, args.marketplace_name, args.plugin_name, args.version, args.agents_dir
        )
        if args.install:
            install(root, *result)
    except subprocess.CalledProcessError as error:
        print(f"Command failed: {error.cmd}\n{error.stderr}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
