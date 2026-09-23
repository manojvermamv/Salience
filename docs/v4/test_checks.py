import importlib.util
import json
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location("blueprint_check", Path(__file__).with_name("check.py"))
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
BLUEPRINT = Path(__file__).with_name("IMPLEMENTATION-BLUEPRINT.md").read_text()


class GateChecks(unittest.TestCase):
    def test_not_run_cannot_be_reported_as_released(self):
        release = json.loads(Path(__file__).with_name("p0-release.json").read_text())
        self.assertEqual(CHECK.release_errors(BLUEPRINT, release), [])
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release | {"release_status": "PASS"}))
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release | {"production_effects_enabled": True}))
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release | {"requirements": []}))

    def test_corrected_blueprint_is_acyclic(self):
        self.assertEqual(CHECK.gate_errors(BLUEPRINT), [])
        self.assertEqual(CHECK.requirement_errors(BLUEPRINT), [])

    def test_gate_cannot_require_a_later_phase(self):
        damaged = BLUEPRINT.replace("**Gate RG2:**", "**Gate RG2:** R23/R25,", 1)
        self.assertTrue(CHECK.gate_errors(damaged))

    def test_outbox_tracing_belongs_to_p1(self):
        phase = BLUEPRINT.split("### P0 —", 1)[1].split("### P1 —", 1)[0]
        self.assertNotIn("traceparent crosses API/outbox/workflow/activity/adapter", phase)

    def test_gate_range_checks_every_requirement(self):
        damaged = BLUEPRINT.replace("**Gate RG2:**", "**Gate RG2:** R19–R31,", 1)
        self.assertTrue(CHECK.gate_errors(damaged))

    def test_phase_gate_prerequisite_cannot_point_forward(self):
        damaged = BLUEPRINT.replace("**Dependencies:** RG3 and P1", "**Dependencies:** RG5 and P1", 1)
        self.assertTrue(CHECK.gate_errors(damaged))

    def test_markdown_damage_is_detected_without_rewriting(self):
        for damaged in ["#Broken", "| one | two |\n| --- |\n", "```sh\ncommand", "damaged \ufffd"]:
            self.assertTrue(CHECK.markdown_structure_errors(Path("sample.md"), damaged))
        self.assertEqual(CHECK.markdown_structure_errors(Path("sample.md"), "# Heading\n```sh\ncommand --flag path/to_file\n```\n"), [])


if __name__ == "__main__":
    unittest.main()
