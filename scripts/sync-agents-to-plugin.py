#!/usr/bin/env python3
"""Create or refresh a plugin marketplace, plugin manifest, and managed-settings distribution from agents/, using only Python's stdlib."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def discover_agents(root: Path) -> list[tuple[Path, str]]:
    directory = root / "agents"
    if not directory.is_dir():
        raise ValueError(f"No agents directory found at {directory}")
    agents = []
    for path in sorted(directory.glob("*.md"), key=lambda item: item.name.casefold()):
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8-sig").lstrip().splitlines()
        if not lines or lines[0].strip() != "---":
            continue
        end = next(
            (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
            None,
        )
        if end is None:
            raise ValueError(f"{path}: frontmatter has no closing ---")
        name = path.stem.removesuffix(".agent")
        for line in lines[1:end]:
            match = re.match(r"^name:\s*(.+)$", line)
            if match:
                name = match[1].strip().strip("\"'")
        agents.append((path, name))
    if not agents:
        raise ValueError(f"No agent definitions with frontmatter found in {directory}")
    return agents


def sync(
    root: Path, marketplace_name: str | None = None, plugin_name: str | None = None
) -> tuple[str, str, str]:
    agents = discover_agents(root)
    remote = run(["git", "remote", "get-url", "origin"], root).strip()
    match = re.fullmatch(
        r"(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"([^/\s]+)/([^/\s]+?)(?:\.git)?/?", remote,
    )
    if not match:
        raise ValueError("The origin remote must identify a GitHub owner/repository")
    owner, repository = match.groups()
    slug = f"{owner}/{repository}"
    url = f"https://github.com/{slug}"
    source_paths = [f"./agents/{path.name}" for path, _ in agents]
    namespace_paths = [f"./com.github.copilot/agents/{path.name}" for path, _ in agents]
    summary = ", ".join(name for _, name in agents)
    description = f"Custom agents for {owner}, auto-generated from agents/. Includes: {summary}."
    manifest_path = root / "plugin.json"
    marketplace_path = root / ".github" / "plugin" / "marketplace.json"
    manifest = read_object(manifest_path) if manifest_path.exists() else {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": plugin_name or f"{owner}-agents",
        "version": "1.0.0",
        "description": description,
        "repository": url,
        "homepage": f"{url}#readme",
        "keywords": ["copilot", "custom-agents", owner],
    }
    marketplace = read_object(marketplace_path) if marketplace_path.exists() else {
        "name": marketplace_name or f"{owner}-marketplace",
        "owner": {"name": owner},
        "metadata": {
            "description": f"Agent plugin marketplace for {owner}.",
            "version": "1.0.0",
        },
        "plugins": [],
    }
    plugin_name = manifest.get("name")
    marketplace_name = marketplace.get("name")
    if not isinstance(plugin_name, str) or not plugin_name.strip():
        raise ValueError("plugin.json must have a non-empty name")
    if not isinstance(marketplace_name, str) or not marketplace_name.strip():
        raise ValueError("marketplace.json must have a non-empty name")
    extensions = manifest.setdefault("extensions", {})
    if not isinstance(extensions, dict):
        raise ValueError("plugin.json extensions must be an object")
    for namespace, paths in (
        ("com.github.copilot", namespace_paths),
        ("com.github.awesome-copilot", source_paths),
    ):
        extension = extensions.setdefault(namespace, {})
        if not isinstance(extension, dict):
            raise ValueError(f"plugin.json extension {namespace} must be an object")
        extension["agents"] = paths
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or any(not isinstance(p, dict) for p in plugins):
        raise ValueError("marketplace.json plugins must be an array of objects")
    matches = [plugin for plugin in plugins if plugin.get("name") == plugin_name]
    if len(matches) > 1:
        raise ValueError(f"Duplicate marketplace entries for {plugin_name}")
    if matches:
        entry = matches[0]
        if entry.get("source") != "./":
            raise ValueError(f"{plugin_name}: expected local root source './'")
    else:
        entry = {
            "name": plugin_name, "description": description,
            "version": manifest.get("version", "1.0.0"), "repository": url, "source": "./",
        }
        plugins.append(entry)
    entry["agents"] = source_paths

    # Prune only mirrors previously referenced by this plugin, never unrelated files.
    old_paths = []
    if manifest_path.exists():
        old_paths = read_object(manifest_path).get("extensions", {}).get(
            "com.github.copilot", {}
        ).get("agents", [])
        if not isinstance(old_paths, list):
            raise ValueError("Existing Copilot agents must be an array")
    mirror = root / "com.github.copilot" / "agents"
    mirror.mkdir(parents=True, exist_ok=True)
    for path, _ in agents:
        destination = mirror / path.name
        if destination.is_symlink():
            raise ValueError(f"Refusing to overwrite symlink: {destination}")
        shutil.copyfile(path, destination)
    for old in old_paths:
        if not isinstance(old, str):
            raise ValueError("Existing agent paths must be strings")
        candidate = root.joinpath(*old.removeprefix("./").split("/"))
        if (
            old not in namespace_paths and candidate.parent == mirror
            and candidate.suffix == ".md" and not candidate.is_symlink()
            and candidate.is_file()
        ):
            candidate.unlink()
    write_object(manifest_path, manifest)
    write_object(marketplace_path, marketplace)

    # Populate enterprise distribution so managed clients auto-receive the plugin.
    settings_path = root / "copilot" / "managed-settings.json"
    settings = read_object(settings_path) if settings_path.exists() else {}
    marketplaces = settings.setdefault("extraKnownMarketplaces", {})
    enabled = settings.setdefault("enabledPlugins", {})
    if not isinstance(marketplaces, dict) or not isinstance(enabled, dict):
        raise ValueError("managed-settings.json marketplace/plugin sections must be objects")
    marketplaces[marketplace_name] = {
        "source": {"source": "github", "repo": slug},
        "autoUpdate": True,
    }
    enabled[f"{plugin_name}@{marketplace_name}"] = True
    write_object(settings_path, settings)
    print(f"Marketplace: {marketplace_name}\nPlugin: {plugin_name}\nAgents ({len(agents)}): {summary}")
    print(f"Managed settings: {settings_path}")
    return slug, marketplace_name, plugin_name


def install(root: Path, slug: str, marketplace: str, plugin: str) -> None:
    executable = shutil.which("copilot")
    if executable is None:
        raise ValueError("GitHub Copilot CLI is required for --install; install it and sign in")
    qualified = f"{plugin}@{marketplace}"
    if marketplace not in run([executable, "plugin", "marketplace", "list"], root):
        print(run([executable, "plugin", "marketplace", "add", slug], root), end="")
    if qualified not in run([executable, "plugin", "list"], root):
        print(run([executable, "plugin", "install", qualified], root), end="")
    print(run([executable, "plugin", "enable", qualified], root), end="")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--marketplace-name", help="Name when creating a marketplace")
    parser.add_argument("--plugin-name", help="Name when creating a plugin")
    parser.add_argument("--install", action="store_true", help="Also install/enable via Copilot CLI")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        result = sync(root, args.marketplace_name, args.plugin_name)
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
