"""Regression tests using temporary repositories; no local CLI state is changed."""

import importlib.util
import json
from pathlib import Path
import shutil
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
        (self.root / "agents" / "example.md").write_text("<!--\n---\nname: Template\n-->")
        result = sync_agents.sync(self.root)
        self.assertEqual(result, ("example/team", "example-marketplace", "example-agents"))
        manifest = self.read("plugin.json")
        self.assertEqual(
            manifest["extensions"]["com.github.copilot"]["agents"],
            ["./com.github.copilot/agents/first.agent.md"],
        )
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

    def test_add_amend_remove_and_preserve_unmanaged_files(self):
        sync_agents.sync(self.root)
        mirror = self.root / "com.github.copilot" / "agents"
        (mirror / "unmanaged.md").write_text("Not generated")
        self.agent("first.agent.md", "First", "Amended")
        second = self.agent("second.md", "Second")
        sync_agents.sync(self.root)
        self.assertIn("Amended", (mirror / "first.agent.md").read_text())
        self.assertEqual(
            len(self.read(".github", "plugin", "marketplace.json")["plugins"][0]["agents"]), 2
        )
        second.unlink()
        sync_agents.sync(self.root)
        self.assertFalse((mirror / "second.md").exists())
        self.assertTrue((mirror / "unmanaged.md").exists())

    def test_preserves_metadata_and_other_plugins(self):
        sync_agents.sync(self.root, "custom-market", "custom-plugin")
        path = self.root / ".github" / "plugin" / "marketplace.json"
        marketplace = self.read(".github", "plugin", "marketplace.json")
        other = {"name": "other", "source": "./other"}
        marketplace["plugins"].append(other)
        marketplace["plugins"][0]["author"] = {"name": "Keep me"}
        path.write_text(json.dumps(marketplace))
        self.agent("second.md", "Second")
        sync_agents.sync(self.root)
        updated = self.read(".github", "plugin", "marketplace.json")
        self.assertEqual(updated["name"], "custom-market")
        self.assertEqual(updated["plugins"][1], other)
        self.assertEqual(updated["plugins"][0]["author"], {"name": "Keep me"})
        self.assertEqual(len(updated["plugins"][0]["agents"]), 2)

    def test_invalid_inputs_do_not_create_manifest(self):
        path = self.root / "agents" / "first.agent.md"
        path.write_text("---\nname: Unclosed")
        with self.assertRaisesRegex(ValueError, "closing"):
            sync_agents.sync(self.root)
        self.assertFalse((self.root / "plugin.json").exists())
        path.write_text("<!-- template -->")
        with self.assertRaisesRegex(ValueError, "No agent definitions"):
            sync_agents.sync(self.root)

    def test_invalid_existing_json_leaves_mirrors_untouched(self):
        sync_agents.sync(self.root)
        mirror = self.root / "com.github.copilot" / "agents" / "first.agent.md"
        before = mirror.read_bytes()
        (self.root / "plugin.json").write_text("{invalid")
        self.agent("first.agent.md", "First", "Changed")
        with self.assertRaises(ValueError):
            sync_agents.sync(self.root)
        self.assertEqual(mirror.read_bytes(), before)

    def test_cli_resolves_root_from_script_not_working_directory(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        script = scripts / "sync-agents-to-plugin.py"
        shutil.copyfile(Path(__file__).with_name(script.name), script)
        result = subprocess.run(
            [sys.executable, "-B", str(script)], cwd=self.root.parent,
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "plugin.json").is_file())
        (self.root / "agents" / "first.agent.md").unlink()
        result = subprocess.run(
            [sys.executable, "-B", str(script)], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("No agent definitions", result.stderr)

    def test_existing_repository_manifests_are_preserved(self):
        repository = Path(__file__).resolve().parent.parent
        if not (repository / "plugin.json").is_file():
            self.skipTest("Host repository has no generated plugin manifests")
        for parts in (("plugin.json",), (".github", "plugin", "marketplace.json")):
            destination = self.root.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repository.joinpath(*parts), destination)
        for agent in (self.root / "agents").glob("*.md"):
            agent.unlink()
        for agent in (repository / "agents").glob("*.md"):
            shutil.copyfile(agent, self.root / "agents" / agent.name)
        manifest = self.read("plugin.json")
        marketplace = self.read(".github", "plugin", "marketplace.json")
        sync_agents.sync(self.root)
        self.assertEqual(self.read("plugin.json"), manifest)
        self.assertEqual(self.read(".github", "plugin", "marketplace.json"), marketplace)

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


if __name__ == "__main__":
    unittest.main()
