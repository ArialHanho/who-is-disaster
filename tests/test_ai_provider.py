import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_app import ai_provider as ai


def completed(payload, *, returncode=0):
    return SimpleNamespace(returncode=returncode, stdout=json.dumps(payload), stderr="")


class AIProviderTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DISASTER_AI_PROVIDER": "codex", "DISASTER_AI_MODEL": ""})
        env.start()
        self.addCleanup(env.stop)

    def test_claude_rejects_api_login_and_never_calls_codex(self):
        ai.select("claude", "")
        with (patch.object(ai, "find_cli", return_value="claude-fixture"),
              patch.object(ai, "_status_cache", {}),
              patch.object(ai, "_run", return_value=completed({
                  "loggedIn": True, "authMethod": "apiKey", "apiProvider": "firstParty"
              })) as run,
              patch.object(ai.codex_provider, "generate") as codex):
            with self.assertRaisesRegex(ValueError, "공식 로그인"):
                ai.generate({}, instructions="", output_schema={})
        self.assertEqual(run.call_args.args[0][1:], ["auth", "status", "--json"])
        codex.assert_not_called()

    def test_claude_sends_data_to_restricted_cli_without_environment_api_keys(self):
        ai.select("claude", "haiku")
        def run(command, **kwargs):
            self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
            if "status" in command:
                return completed({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty"})
            self.assertIn("--restricted", command)
            self.assertIn("--safe-mode", command)
            self.assertEqual(command[command.index("--tools") + 1], "")
            self.assertIn("<project_data>", kwargs["input"])
            self.assertEqual(list(kwargs["cwd"].iterdir()), [])
            return completed({"structured_output": {"summary": "ok"}, "usage": {"input_tokens": 3}})
        with (patch.dict(os.environ, {"ANTHROPIC_API_KEY": "fixture-key"}),
              patch.object(ai, "_status_cache", {}),
              patch.object(ai, "find_cli", return_value="claude-fixture"),
              patch.object(ai, "_run", side_effect=run)):
            result = ai.generate({"value": 1}, instructions="Check", output_schema={})
        self.assertEqual(result["result"], {"summary": "ok"})
        self.assertEqual(ai.model_identity(), ["claude", "haiku"])

    def test_gemini_uses_one_stdin_turn_and_disables_overage(self):
        ai.select("gemini", "")
        def run(command, **kwargs):
            from pathlib import Path
            settings = json.loads(Path(command[command.index("--settings") + 1]).read_text())
            self.assertFalse(settings["useG1Credits"])
            self.assertEqual(settings["modelProvider"], "")
            self.assertIn("command(*)", settings["permissions"]["deny"])
            self.assertNotIn("GEMINI_API_KEY", kwargs["env"])
            self.assertIn(ai.GEMINI_MODEL, command)
            self.assertEqual(json.loads(kwargs["input"])["event"], "user")
            return completed({"event": "result", "result": {"status": "SUCCESS", "structured_output": {"checks": []}}})
        with (patch.dict(os.environ, {"GEMINI_API_KEY": "fixture-key"}),
              patch.object(ai, "find_cli", return_value="agy-fixture"),
              patch.object(ai, "status", return_value={"ready": True}),
              patch.object(ai, "_run", side_effect=run)):
            result = ai.generate({}, instructions="Check", output_schema={})
        self.assertEqual(result["result"], {"checks": []})
        with self.assertRaises(ValueError):
            ai.select("gemini", "Claude Sonnet")
        with self.assertRaisesRegex(ValueError, "도구"):
            ai.gemini_result('{"event":"step_update","step_update":{"step_type":"tool","tool_name":"run_command"}}')
