"""Regression tests using temporary repositories; no local CLI state is changed."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "sync_agents", Path(__file__).with_name("sync-agents-to-plugin.py")
)
assert SPEC is not None and SPEC.loader is not None
sync_agents = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync_agents)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "agents").mkdir()
        self.agent("first.agent.md", "First")
        self.git("init", "-q")
        self.git("remote", "add", "origin", "git@github.com:example/team.git")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def agent(self, filename, name, body="Original"):
        path = self.root / "agents" / filename
        path.write_text(f"---\nname: '{name}'\ndescription: Test agent\n---\n{body}\n")
        return path

    def read(self, *parts):
        return json.loads(self.root.joinpath(*parts).read_text(encoding="utf-8"))

    def test_create_and_idempotence(self):
        (self.root / "agents" / "notes.md").write_text("Plain documentation, ignored.")
        result = sync_agents.sync(self.root)
        self.assertEqual(result, ("example/team", "example-marketplace", "example-agents"))
        manifest = self.read("plugin.json")
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertEqual(
            manifest["extensions"]["com.github.copilot"]["agents"],
            ["./com.github.copilot/agents/first.agent.md"],
        )
        marketplace = self.read(".github", "plugin", "marketplace.json")
        self.assertEqual(marketplace["plugins"][0]["version"], "1.0.0")
        settings = self.read("copilot", "managed-settings.json")
        self.assertEqual(
            settings["extraKnownMarketplaces"]["example-marketplace"],
            {"source": {"source": "github", "repo": "example/team"}, "autoUpdate": True},
        )
        self.assertTrue(settings["enabledPlugins"]["example-agents@example-marketplace"])
        paths = [
            self.root / "plugin.json",
            self.root / ".github" / "plugin" / "marketplace.json",
            self.root / "copilot" / "managed-settings.json",
        ]
        before = [path.read_bytes() for path in paths]
        sync_agents.sync(self.root)
        self.assertEqual(before, [path.read_bytes() for path in paths])

    def test_version_bumps_on_change_and_propagates_to_marketplace(self):
        sync_agents.sync(self.root)
        self.agent("first.agent.md", "First", "Amended body")
        sync_agents.sync(self.root)
        self.assertEqual(self.read("plugin.json")["version"], "1.0.1")
        self.assertEqual(
            self.read(".github", "plugin", "marketplace.json")["plugins"][0]["version"], "1.0.1"
        )
        sync_agents.sync(self.root)  # no change: no bump
        self.assertEqual(self.read("plugin.json")["version"], "1.0.1")
        sync_agents.sync(self.root, version="2.0.0")  # explicit override
        self.assertEqual(self.read("plugin.json")["version"], "2.0.0")
        self.assertEqual(
            self.read(".github", "plugin", "marketplace.json")["plugins"][0]["version"], "2.0.0"
        )

    def test_add_amend_remove_and_preserve_unmanaged_files(self):
        sync_agents.sync(self.root)
        mirror = self.root / "com.github.copilot" / "agents"
        (mirror / "unmanaged.md").write_text("Not generated")
        self.agent("first.agent.md", "First", "Amended")
        second = self.agent("second.agent.md", "Second")
        sync_agents.sync(self.root)
        self.assertIn("Amended", (mirror / "first.agent.md").read_text())
        self.assertEqual(
            len(self.read(".github", "plugin", "marketplace.json")["plugins"][0]["agents"]), 2
        )
        second.unlink()
        sync_agents.sync(self.root)
        self.assertFalse((mirror / "second.agent.md").exists())
        self.assertTrue((mirror / "unmanaged.md").exists())

    def test_preserves_metadata_and_other_plugins(self):
        sync_agents.sync(self.root, "custom-market", "custom-plugin")
        path = self.root / ".github" / "plugin" / "marketplace.json"
        marketplace = self.read(".github", "plugin", "marketplace.json")
        other = {"name": "other", "source": "./other"}
        marketplace["plugins"].append(other)
        marketplace["plugins"][0]["author"] = {"name": "Keep me"}
        path.write_text(json.dumps(marketplace))
        self.agent("second.agent.md", "Second")
        sync_agents.sync(self.root)
        updated = self.read(".github", "plugin", "marketplace.json")
        self.assertEqual(updated["name"], "custom-market")
        self.assertEqual(updated["plugins"][1], other)
        self.assertEqual(updated["plugins"][0]["author"], {"name": "Keep me"})
        self.assertEqual(len(updated["plugins"][0]["agents"]), 2)

    def test_rename_cleans_stale_managed_settings(self):
        sync_agents.sync(self.root, "old-market", "old-plugin")
        settings_path = self.root / "copilot" / "managed-settings.json"
        settings = self.read("copilot", "managed-settings.json")
        settings["extraKnownMarketplaces"]["unrelated"] = {
            "source": {"source": "github", "repo": "someone/else"}, "autoUpdate": True,
        }
        settings["enabledPlugins"]["their-plugin@unrelated"] = True
        settings_path.write_text(json.dumps(settings))
        # Rename both by recreating manifests.
        (self.root / "plugin.json").unlink()
        (self.root / ".github" / "plugin" / "marketplace.json").unlink()
        sync_agents.sync(self.root, "new-market", "new-plugin")
        updated = self.read("copilot", "managed-settings.json")
        self.assertNotIn("old-market", updated["extraKnownMarketplaces"])
        self.assertNotIn("old-plugin@old-market", updated["enabledPlugins"])
        self.assertTrue(updated["enabledPlugins"]["new-plugin@new-market"])
        # Unrelated settings pointing at other repositories are untouched.
        self.assertIn("unrelated", updated["extraKnownMarketplaces"])
        self.assertTrue(updated["enabledPlugins"]["their-plugin@unrelated"])

    def test_agent_md_with_bad_frontmatter_is_an_error(self):
        path = self.root / "agents" / "first.agent.md"
        path.write_text("---\nname: Unclosed")
        with self.assertRaisesRegex(ValueError, "closing"):
            sync_agents.sync(self.root)
        self.assertFalse((self.root / "plugin.json").exists())
        path.write_text("# No frontmatter at all\nJust text.")
        with self.assertRaisesRegex(ValueError, "frontmatter"):
            sync_agents.sync(self.root)
        path.write_text("---\nname: NoDescription\n---\nBody")
        with self.assertRaisesRegex(ValueError, "description"):
            sync_agents.sync(self.root)
        path.unlink()
        (self.root / "agents" / "plain.md").write_text("docs only")
        with self.assertRaisesRegex(ValueError, "No \\*\\.agent\\.md files"):
            sync_agents.sync(self.root)

    def test_invalid_names_and_versions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Plugin name"):
            sync_agents.sync(self.root, plugin_name="Has Spaces")
        with self.assertRaisesRegex(ValueError, "Marketplace name"):
            sync_agents.sync(self.root, marketplace_name="UPPER")
        with self.assertRaisesRegex(ValueError, "Version"):
            sync_agents.sync(self.root, version="not-a-version")
        self.assertFalse((self.root / "plugin.json").exists())

    def test_invalid_existing_json_leaves_everything_untouched(self):
        sync_agents.sync(self.root)
        mirror = self.root / "com.github.copilot" / "agents" / "first.agent.md"
        before_mirror = mirror.read_bytes()
        marketplace_path = self.root / ".github" / "plugin" / "marketplace.json"
        before_marketplace = marketplace_path.read_bytes()
        settings_path = self.root / "copilot" / "managed-settings.json"
        settings_path.write_text('{"enabledPlugins": "not-an-object"}')
        self.agent("first.agent.md", "First", "Changed")
        with self.assertRaisesRegex(ValueError, "must be objects"):
            sync_agents.sync(self.root)
        # Managed-settings validation happens before any mutation.
        self.assertEqual(mirror.read_bytes(), before_mirror)
        self.assertEqual(marketplace_path.read_bytes(), before_marketplace)

    def test_non_root_marketplace_source_is_rejected_but_equivalents_accepted(self):
        sync_agents.sync(self.root)
        path = self.root / ".github" / "plugin" / "marketplace.json"
        marketplace = self.read(".github", "plugin", "marketplace.json")
        marketplace["plugins"][0]["source"] = "."
        path.write_text(json.dumps(marketplace))
        sync_agents.sync(self.root)  # "." is equivalent to "./": accepted
        marketplace = self.read(".github", "plugin", "marketplace.json")
        marketplace["plugins"][0]["source"] = "./subdir"
        path.write_text(json.dumps(marketplace))
        with self.assertRaisesRegex(ValueError, "repository root"):
            sync_agents.sync(self.root)

    def test_cli_root_argument_and_git_toplevel_resolution(self):
        script = Path(__file__).with_name("sync-agents-to-plugin.py")
        # --root works from an unrelated working directory.
        result = subprocess.run(
            [sys.executable, "-B", str(script), "--root", str(self.root)],
            cwd=self.root.parent, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Target repository:", result.stdout)
        # Without --root, the current git toplevel wins over the script location.
        (self.root / "plugin.json").unlink()
        result = subprocess.run(
            [sys.executable, "-B", str(script)],
            cwd=self.root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "plugin.json").is_file())

    def test_cli_has_entry_requires_exact_token(self):
        output = "Installed plugins:\n  • my-agents-extra@market (v1.0.0)\n"
        self.assertFalse(sync_agents.cli_has_entry(output, "my-agents@market"))
        self.assertTrue(sync_agents.cli_has_entry(output, "my-agents-extra@market"))

    def test_install_uses_generated_names_and_checks_command_failures(self):
        with patch.object(sync_agents.shutil, "which", return_value="copilot"), patch.object(
            sync_agents, "run", return_value=""
        ) as run:
            sync_agents.install(self.root, "example/team", "custom-market", "custom-plugin")
            commands = [call.args[0] for call in run.call_args_list]
            self.assertIn(["copilot", "plugin", "marketplace", "add", "example/team"], commands)
            self.assertIn(["copilot", "plugin", "install", "custom-plugin@custom-market"], commands)
            self.assertEqual(commands[-1], ["copilot", "plugin", "enable", "custom-plugin@custom-market"])
        with patch.object(sync_agents.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "CLI is required"):
                sync_agents.install(self.root, "example/team", "market", "plugin")

    def test_powershell_wrapper_delegates_to_python(self):
        wrapper = Path(__file__).with_name("sync-agents-to-plugin.ps1")
        content = wrapper.read_text()
        self.assertIn("sync-agents-to-plugin.py", content)
        # The wrapper must not contain its own conversion logic.
        for marker in ("marketplace.json", "plugin.json\"", "managed-settings"):
            self.assertNotIn(marker, content)


if __name__ == "__main__":
    unittest.main()
