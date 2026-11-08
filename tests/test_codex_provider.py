from pathlib import Path
import json
import os
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_app import codex_provider


class CodexProviderTests(unittest.TestCase):



    def test_cli_gets_data_on_stdin_and_execution_tools_are_disabled(self):
        calls = []
        def run(command, **kwargs):
            calls.append((command, kwargs))
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            self.assertNotIn("CODEX_API_KEY", kwargs["env"])
            self.assertEqual(list(Path(kwargs["cwd"]).iterdir()), [])
            output = Path(command[command.index("--output-last-message") + 1])
            output.write_text(json.dumps({"answer": "연결됐어요", "used_source_ids": [], "proposals": []}), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout='{"type":"turn.completed","usage":{}}\n', stderr="")
        with patch.object(codex_provider, "find_codex", return_value="codex-test"), patch.object(codex_provider, "disabled_features", return_value=list(codex_provider.REQUIRED_DISABLED)), patch.object(codex_provider, "_run", run):
            result = codex_provider.generate({"question": "public-check"}, instructions="Review supplied data only", output_schema={"type":"object","properties":{"answer":{"type":"string"}}})
        command, kwargs = calls[0]
        self.assertNotIn("public-check", command)
        self.assertIn("public-check", kwargs["input"])
        self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
        for feature in codex_provider.REQUIRED_DISABLED:
            self.assertIn(feature, command)
        self.assertIn("--ignore-user-config", command)
        self.assertEqual(result["result"]["answer"], "연결됐어요")


if __name__ == "__main__":
    unittest.main()
