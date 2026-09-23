import importlib.util
import json
from pathlib import Path
import unittest
from copy import deepcopy


SPEC = importlib.util.spec_from_file_location("blueprint_check", Path(__file__).with_name("check.py"))
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)
BLUEPRINT = Path(__file__).with_name("IMPLEMENTATION-BLUEPRINT.md").read_text()
REPO_SPEC = importlib.util.spec_from_file_location("repository_check", Path(__file__).with_name("verify_repository.py"))
REPOSITORY = importlib.util.module_from_spec(REPO_SPEC)
REPO_SPEC.loader.exec_module(REPOSITORY)


class GateChecks(unittest.TestCase):
    def test_incremental_p1_cannot_claim_release_or_enable_effects(self):
        release = json.loads(Path(__file__).with_name("p0-release.json").read_text())
        for change in [{"release_status":"PASS"},{"production_effects_enabled":True},{"remaining":[]}]:
            damaged = deepcopy(release)
            damaged["p1_increment"].update(change)
            self.assertTrue(CHECK.release_errors(BLUEPRINT,damaged))

    def test_durable_report_requires_successful_executed_commands(self):
        report = json.loads(Path(__file__).with_name("development-entry-evidence.json").read_text())
        self.assertEqual(CHECK.evidence_report_errors(report), [])
        for damaged in [{}, {"commands":[]}, {"commands":[{}]}]:
            self.assertTrue(CHECK.evidence_report_errors(damaged))
        for field in ["exit_code","errors","failures","skipped"]:
            damaged = deepcopy(report)
            damaged["commands"][0][field] = 1
            self.assertTrue(CHECK.evidence_report_errors(damaged))

    def test_remote_gate_cannot_accept_unprotected_stale_or_missing_checks(self):
        self.assertTrue(REPOSITORY.enforcement_errors({}))
        self.assertTrue(REPOSITORY.ci_errors([], "current"))
        checks = [{"id": index, "name": name, "app": {"id": 15368}, "head_sha": "current", "conclusion": "success", "status": "completed"} for index, name in enumerate(REPOSITORY.REQUIRED)]
        self.assertEqual(REPOSITORY.ci_errors(checks, "current"), [])
        self.assertTrue(REPOSITORY.ci_errors(checks, "next"))
        self.assertTrue(REPOSITORY.ci_errors(checks + [checks[0] | {"id": 100, "status": "queued", "conclusion": None}], "current"))
        self.assertEqual(REPOSITORY.ci_status([], "current"), "NOT RUN")
        self.assertEqual(REPOSITORY.ci_status(checks, "current"), "PASS")
        self.assertEqual(REPOSITORY.ci_status(checks + [checks[0] | {"id":100,"status":"in_progress","conclusion":None}], "current"), "NOT RUN")
        self.assertEqual(REPOSITORY.ci_status(checks + [checks[0] | {"id":100,"conclusion":"failure"}], "current"), "FAIL")

    def test_release_requires_exact_external_check_set_and_valid_statuses(self):
        baseline = json.loads(Path(__file__).with_name("p0-release.json").read_text())
        for checks in [None, {}, 1, [None], [], baseline["external_checks"][:-1], baseline["external_checks"] + baseline["external_checks"][:1], [{"check": "invented", "status": "PASS"}]]:
            with self.subTest(checks=checks):
                self.assertTrue(CHECK.release_errors(BLUEPRINT, baseline | {"external_checks": checks}))
        for field in ["external_checks", "local_qualification", "verification", "evidence_artifacts"]:
            release = deepcopy(baseline)
            del release[field]
            self.assertTrue(CHECK.release_errors(BLUEPRINT, release))
        release = deepcopy(baseline)
        release["external_checks"][0]["status"] = "waived"
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release))
        self.assertTrue(CHECK.release_errors(BLUEPRINT, baseline | {"release_status": "green"}))

    def test_release_cannot_pass_without_successful_evidenced_local_qualification(self):
        baseline = json.loads(Path(__file__).with_name("p0-release.json").read_text())
        baseline["release_status"] = "PASS"
        for check in baseline["external_checks"]:
            check.update(status="PASS", evidence="https://example.invalid/qualification")
        self.assertEqual(CHECK.release_errors(BLUEPRINT, baseline), [])
        for update in [{"local_qualification": "FAIL"}, {"local_qualification": "NOT RUN"}, {"verification": {}}, {"evidence_artifacts": []}]:
            self.assertTrue(CHECK.release_errors(BLUEPRINT, baseline | update))
        for field in ["failed", "skipped", "errors", "exit_code"]:
            release = deepcopy(baseline)
            release["verification"]["focused_p0"][field] = 1
            self.assertTrue(CHECK.release_errors(BLUEPRINT, release))
            release = deepcopy(baseline)
            release["verification"]["focused_p0"].pop(field)
            self.assertTrue(CHECK.release_errors(BLUEPRINT, release))
        release = deepcopy(baseline)
        release["external_checks"][0].pop("evidence")
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release))
        for invalid_path in ["/tmp/evidence.json", "../evidence.json", "docs/../../evidence.json", "artifacts/ignored-evidence.json"]:
            release = deepcopy(baseline)
            release["evidence_artifacts"] = [{"path":invalid_path,"sha256":"a"*64,"local_only":False}]
            self.assertTrue(CHECK.release_errors(BLUEPRINT, release))
        release = deepcopy(baseline)
        for artifact in release["evidence_artifacts"]:
            artifact["local_only"] = True
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release))

    def test_not_run_cannot_be_reported_as_released(self):
        release = json.loads(Path(__file__).with_name("p0-release.json").read_text())
        self.assertEqual(CHECK.release_errors(BLUEPRINT, release), [])
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release | {"release_status": "PASS"}))
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release | {"production_effects_enabled": True}))
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release | {"requirements": []}))

    def test_corrected_blueprint_is_acyclic(self):
        self.assertEqual(CHECK.gate_errors(BLUEPRINT), [])
        self.assertEqual(CHECK.requirement_errors(BLUEPRINT), [])

    def test_development_entry_is_independent_but_fail_closed(self):
        release = json.loads(Path(__file__).with_name("p0-release.json").read_text())
        entry = {"status": "PASS", "checks": [{"check": name, "status": "PASS", "evidence": "local test evidence"} for name in CHECK.DEVELOPMENT_CHECKS]}
        self.assertEqual(CHECK.release_errors(BLUEPRINT, release | {"development_entry": entry}), [])
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release | {"development_entry": entry | {"checks": []}}))
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release | {"development_entry": entry, "local_qualification": "FAIL"}))

    def test_deferral_contract_distinguishes_all_three_identity_paths(self):
        decision = BLUEPRINT.split("**P1 re-admission and deferral:**", 1)[1].split("\n", 1)[0]
        for assertion in ["has no cycle until admitted", "Retry/reconciliation resumes the existing cycle and operation", "Strategic defer/abstain closes the admitted cycle", "separately authorized successor intent", "coalesced with the equivalent scheduled slot", "Closed cycles never resume"]:
            self.assertIn(assertion, decision)
        self.assertNotIn("Deferral after cycle creation resumes the existing cycle", BLUEPRINT)

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
