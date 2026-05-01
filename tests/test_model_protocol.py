import json
import unittest

from local_agent.model_protocol import ModelProtocolError, parse_model_proposal
from local_agent.models import CommandProposal, EditProposal, PlanProposal


class ModelProtocolTests(unittest.TestCase):
    def test_valid_command_json(self):
        proposal = parse_model_proposal('{"type":"command","command":"python -m pytest"}')

        self.assertEqual(CommandProposal("python -m pytest"), proposal)

    def test_valid_edit_json(self):
        diff = "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-a\n+b"
        proposal = parse_model_proposal(json.dumps({"type": "edit", "diff": diff}))

        self.assertEqual(EditProposal(diff), proposal)

    def test_valid_plan_json(self):
        proposal = parse_model_proposal(
            json.dumps({"type": "plan", "steps": ["Inspect failures", "Run tests"]})
        )

        self.assertEqual(PlanProposal(["Inspect failures", "Run tests"]), proposal)

    def test_markdown_fenced_json_rejected(self):
        with self.assertRaises(ModelProtocolError):
            parse_model_proposal('```json\n{"type":"command","command":"pytest"}\n```')

    def test_prose_before_json_rejected(self):
        with self.assertRaises(ModelProtocolError):
            parse_model_proposal('Here you go: {"type":"command","command":"pytest"}')

    def test_json_array_rejected(self):
        with self.assertRaises(ModelProtocolError):
            parse_model_proposal('[{"type":"command","command":"pytest"}]')

    def test_missing_type_rejected(self):
        with self.assertRaises(ModelProtocolError):
            parse_model_proposal('{"command":"pytest"}')

    def test_unknown_type_rejected(self):
        with self.assertRaises(ModelProtocolError):
            parse_model_proposal('{"type":"other","command":"pytest"}')

    def test_extra_fields_rejected(self):
        with self.assertRaises(ModelProtocolError):
            parse_model_proposal('{"type":"command","command":"pytest","note":"x"}')

    def test_empty_command_rejected(self):
        with self.assertRaises(ModelProtocolError):
            parse_model_proposal('{"type":"command","command":"  "}')

    def test_non_string_command_rejected(self):
        with self.assertRaises(ModelProtocolError):
            parse_model_proposal('{"type":"command","command": ["pytest"]}')

    def test_empty_diff_rejected(self):
        with self.assertRaises(ModelProtocolError):
            parse_model_proposal('{"type":"edit","diff":"  "}')

    def test_non_list_plan_steps_rejected(self):
        with self.assertRaises(ModelProtocolError):
            parse_model_proposal('{"type":"plan","steps":"Inspect"}')

    def test_non_string_plan_step_rejected(self):
        with self.assertRaises(ModelProtocolError):
            parse_model_proposal('{"type":"plan","steps":["Inspect", 3]}')


if __name__ == "__main__":
    unittest.main()
