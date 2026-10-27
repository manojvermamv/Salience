import importlib.util
import hashlib
import json
from pathlib import Path
import re
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
    def test_admission_uniqueness_is_per_eligibility_revision(self):
        self.assertIn("One immutable admission disposition per (intent_id, eligibility_revision), with at most one admitted cycle per intent.", BLUEPRINT)

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

    def test_initial_p1_rg0_rg1_gap_audit_is_fail_closed_and_specs_are_bounded(self):
        audit = json.loads(Path(__file__).with_name("p1-rg0-rg1-initial-gap-audit.json").read_text())
        expected = {"R01", "R22", "R69", "R16F", "R23F"} | {
            "R02", "R03", "R05", "R06", "R07", "R08", "R09", "R10", "R13",
            "R15", "R17", "R18", "R32", "R33", "R34", "R35", "R53",
        }
        self.assertEqual({row["id"] for row in audit["requirements"]}, expected)
        self.assertEqual(len(audit["requirements"]), len(expected))
        self.assertTrue(all(row["audit_status"] in {"FAIL", "NOT RUN", "HELD"} for row in audit["requirements"]))
        self.assertTrue(all(row["owner_status"] == "MISSING" for row in audit["requirements"]))
        self.assertEqual(audit["gate_disposition"]["RG0"], "HELD")
        self.assertEqual(audit["gate_disposition"]["RG1"], "HELD")
        self.assertEqual(audit["gate_disposition"]["P2"], "BLOCKED")
        self.assertFalse(audit["environment"]["production_effects_enabled"])
        self.assertFalse(audit["environment"]["production_target_configured"])
        self.assertRegex(audit["environment"]["environment_manifest_sha256"], r"^[a-f0-9]{64}$")
        for row in audit["requirements"]:
            with self.subTest(row=row["id"], contract="structured evidence observations"):
                observations = row.get("evidence_observations")
                self.assertIsInstance(observations, list)
                self.assertGreater(len(observations), 0)
                for observation in observations:
                    self.assertRegex(observation.get("qualified_commit", ""), r"^[a-f0-9]{40}$")
                    artifact = observation.get("artifact_binding", {})
                    artifact_digest = any(
                        re.fullmatch(r"[a-f0-9]{64}", artifact.get(field, ""))
                        for field in ["sha256", "image_sha256", "wheel_sha256", "digest"]
                    )
                    self.assertTrue(
                        artifact_digest or artifact.get("unavailable_reason"),
                        artifact,
                    )
                    environment = observation.get("environment_scope", {})
                    self.assertTrue(environment.get("environment_ref"), environment)
                    self.assertTrue(environment.get("scope"), environment)
                    reference = observation.get("evidence_reference", {})
                    self.assertRegex(reference.get("path", ""), r"^docs/[^\s]+$")
                    self.assertRegex(reference.get("sha256", ""), r"^[a-f0-9]{64}$")
                    self.assertTrue((CHECK.ROOT / reference["path"]).is_file(), reference)
                    self.assertIn("source_references", observation)
                    self.assertIn("verifier_references", observation)
                    if observation.get("evidence_kind") == "local_extension":
                        self.assertTrue(observation["verifier_references"])
                    current_observation = observation.get("current_audit_observation", {})
                    self.assertEqual(current_observation.get("audit_source_commit"), audit["source"]["audit_commit"])
                    self.assertEqual(current_observation.get("environment_manifest_sha256"), audit["environment"]["environment_manifest_sha256"])
                    self.assertEqual(current_observation.get("status"), row["audit_status"])
                    self.assertTrue(current_observation.get("artifact_binding", {}).get("unavailable_reason"))
                    self.assertTrue(observation.get("final_entry_freshness"))
        expected_rg0_checks = {
            "independent safety review",
            "production identity and ingress",
            "production storage and restore",
            "production collector and alert delivery",
            "remote repository gate enforcement",
        }
        expected_rg0_rows = {
            "independent safety review": ["R01"],
            "production identity and ingress": ["R16F", "R22"],
            "production storage and restore": ["R69"],
            "production collector and alert delivery": ["R01"],
            "remote repository gate enforcement": ["R01"],
        }
        external_checks = audit.get("external_rg0_checks")
        valid_external_checks = external_checks if isinstance(external_checks, list) else []
        with self.subTest(contract="five mandatory RG0 external checks"):
            self.assertIsInstance(external_checks, list)
            self.assertEqual({check.get("check") for check in valid_external_checks}, expected_rg0_checks)
        for check in valid_external_checks:
            with self.subTest(external_check=check.get("check")):
                for field in [
                    "source_status",
                    "historical_status",
                    "current_status",
                    "current_freshness",
                    "owner_role",
                    "missing_human",
                    "owning_rg0_rows",
                    "remaining_dependency",
                    "external_increment",
                ]:
                    self.assertIn(field, check)
                    self.assertTrue(check[field], field)
                self.assertEqual(check["owning_rg0_rows"], expected_rg0_rows[check["check"]])
                self.assertIn(check["current_status"], {"NOT RUN", "HELD"})
        for row in audit["requirements"]:
            with self.subTest(row=row["id"], contract="phase ownership split"):
                self.assertIn("current_entry_residual", row)
                self.assertIn("preservation_obligations", row)
                self.assertIn("deferred_phase_owned_work", row)
                self.assertIsInstance(row["current_entry_residual"], list)
                self.assertIsInstance(row["preservation_obligations"], list)
                self.assertIsInstance(row["deferred_phase_owned_work"], list)
                self.assertFalse(any("prerequisite to P2" in text for text in row["current_entry_residual"]))
        self.assertIn("not a prerequisite to entering P2", audit["phase_ownership_boundary"]["p2_entry_rule"])
        matrix = json.loads(Path(__file__).with_name("p1-qualification-matrix.json").read_text())
        row03 = next(row for row in matrix["requirements"] if row["id"] == "R03")
        self.assertIn("7878208 is the earlier Task 1–8 application candidate", row03["remaining_acceptance"])
        self.assertIn("182c4e4", row03["remaining_acceptance"])
        stale_a4_status = matrix["historical_a4_qualification"]["status"]
        self.assertNotIn("current 7878208", stale_a4_status)
        row17 = next(row for row in audit["requirements"] if row["id"] == "R17")
        self.assertTrue(any(item["owning_phase"] == "P2" for item in row17["deferred_phase_owned_work"]))
        for row_id in ["R35", "R53"]:
            row = next(row for row in audit["requirements"] if row["id"] == row_id)
            self.assertTrue(row["deferred_phase_owned_work"])
        decisions = audit["increment_decisions"]
        self.assertEqual(len(decisions), 5)
        self.assertTrue(all(decision["decision"] == "REQUIRED" for decision in decisions))
        required_language = {"Acceptance", "Tests", "Migration", "Rollback", "Evidence", "Exclusions"}
        for decision in decisions:
            spec = CHECK.ROOT / decision["spec"]
            self.assertTrue(spec.is_file(), decision["spec"])
            content = spec.read_text()
            for phrase in required_language:
                self.assertIn(phrase.casefold(), content.casefold(), f"{decision['spec']}: {phrase}")
            for phrase in {"production", "effects", "disabled"}:
                self.assertIn(phrase, content.casefold(), f"{decision['spec']}: {phrase}")
            self.assertIn("P2", content)
            self.assertIn("HELD", content)

    def test_external_collector_acceptance_maps_rg0_owner_and_related_rg1_rows(self):
        audit = json.loads(Path(__file__).with_name("p1-rg0-rg1-initial-gap-audit.json").read_text())
        check = next(item for item in audit["external_rg0_checks"] if item["check"] == "production collector and alert delivery")
        self.assertEqual(check["owning_rg0_rows"], ["R01"])
        self.assertEqual(check["related_p1_rows"], ["R18", "R32", "R34"])

        spec_path = CHECK.ROOT / "docs/superpowers/specs/2026-10-13-rg0-rg1-external-acceptance-design.md"
        spec_text = spec_path.read_text()
        external_row = next(line for line in spec_text.splitlines() if line.startswith("| Production collector and alert delivery |"))
        cells = [cell.strip() for cell in external_row.strip("|").split("|")]
        self.assertEqual(cells[1], "`R01` (RG0 owner); related RG1 rows: `R18`, `R32`, `R34`")

        r01_start = spec_text.index("| `R01` canonical ownership and modular deployment (RG0) |")
        r01_end = spec_text.index("\n| `R22`", r01_start)
        r01_procedure = spec_text[r01_start:r01_end]
        for evidence_item in [
            "collector export/delivery evidence",
            "external destination receipt",
            "human acknowledgement",
        ]:
            self.assertIn(evidence_item, r01_procedure)

    def test_active_program_status_and_sequence_evidence_bind_current_state(self):
        release = json.loads(Path(__file__).with_name("p0-release.json").read_text())
        matrix = json.loads(Path(__file__).with_name("p1-qualification-matrix.json").read_text())
        expected_status = (
            "COMPLETE_REVIEWED at frozen source 7c1ddcda280b510048ad61106c406171edd64f18; "
            "five required designs COMPLETE_REVIEWED; implementation and target qualification NOT RUN; "
            "22 final-entry rows non-PASS; RG0/RG1 HELD; P2 BLOCKED"
        )
        active_sequences = [
            release["resumed_development_goal"]["program_sequence"],
            matrix["current_qualification"]["approved_program_sequence"],
        ]
        for sequence in active_sequences:
            self.assertEqual(sequence["slice_1"], expected_status)
            self.assertEqual(sequence["status"], "APPROVED")
        for active in [
            release["resumed_development_goal"],
            release["later_phase_boundaries"]["P1"],
            matrix["current_qualification"],
        ]:
            active_text = json.dumps(active)
            self.assertNotIn("NEXT / GATED: commit-bound P1/RG0/RG1 gap-inventory audit", active_text)
            self.assertIn("five required designs COMPLETE_REVIEWED", active_text)
            self.assertIn("implementation and target qualification NOT RUN", active_text)
        self.assertEqual(matrix["initial_commit_bound_gap_audit"]["implementation_status"], "NOT RUN")
        self.assertEqual(matrix["initial_commit_bound_gap_audit"]["target_qualification_status"], "NOT RUN")

        sequence_path = CHECK.ROOT / "docs/superpowers/specs/2026-10-13-v4-multiplatform-autonomy-program-sequence.md"
        source_text = sequence_path.read_text()
        diagrams = re.findall(r"```mermaid\n(.*?)\n```", source_text, re.S)
        self.assertTrue(diagrams)
        source_sha256 = hashlib.sha256(diagrams[0].encode()).hexdigest()
        manifest = json.loads(Path(__file__).with_name("documentation-status-audit.json").read_text())
        sequence_evidence = manifest["verification"]["documentation_verification"]["sequence_diagram"]
        self.assertEqual(sequence_evidence["source_sha256"], source_sha256)
        self.assertIs(sequence_evidence["source_equality_assertion"], True)
        self.assertEqual(sequence_evidence["status"], "PASS")
        self.assertEqual(sequence_evidence["version"], "12.0.0")
        self.assertEqual(
            sequence_evidence["source_path"],
            "docs/superpowers/specs/2026-10-13-v4-multiplatform-autonomy-program-sequence.md",
        )
        self.assertRegex(sequence_evidence["results_sha256"], r"^[a-f0-9]{64}$")
        self.assertRegex(sequence_evidence["rendered_svg_sha256"], r"^[a-f0-9]{64}$")
        self.assertRegex(sequence_evidence["rendered_png_sha256"], r"^[a-f0-9]{64}$")
        self.assertTrue(sequence_evidence["results_path"].endswith("/results.json"))
        self.assertTrue(sequence_evidence["output"].startswith("/tmp/salience-"))
        historical = sequence_evidence["historical"]
        self.assertEqual(historical["results_sha256"], "72a959686d5c2d69316dee0481e6e73c6b2b0be0980b9e60e66db97b95f003e9")
        self.assertNotEqual(historical["source_sha256"], source_sha256)

    def test_corrected_blueprint_is_acyclic(self):
        self.assertEqual(CHECK.gate_errors(BLUEPRINT), [])
        self.assertEqual(CHECK.requirement_errors(BLUEPRINT), [])

    def test_development_entry_is_independent_but_fail_closed(self):
        release = json.loads(Path(__file__).with_name("p0-release.json").read_text())
        entry = {"status": "PASS", "checks": [{"check": name, "status": "PASS", "evidence": "local test evidence"} for name in CHECK.DEVELOPMENT_CHECKS]}
        self.assertEqual(CHECK.release_errors(BLUEPRINT, release | {"development_entry": entry}), [])
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release | {"development_entry": entry | {"checks": []}}))
        self.assertTrue(CHECK.release_errors(BLUEPRINT, release | {"development_entry": entry, "local_qualification": "FAIL"}))

    def test_p1_spec_boundaries_cover_production_authority_recovery_and_evidence(self):
        spec_root = Path(__file__).parents[1] / "superpowers" / "specs"
        policy = (spec_root / "2026-10-13-p1-production-policy-and-revocation-design.md").read_text().casefold()
        routing = (spec_root / "2026-10-13-p1-production-routing-and-worker-fencing-design.md").read_text().casefold()
        recovery = (spec_root / "2026-10-13-p1-production-recovery-and-alerts-design.md").read_text().casefold()
        external = (spec_root / "2026-10-13-rg0-rg1-external-acceptance-design.md").read_text().casefold()

        policy_contract = [
            "production permit persistence",
            "v4_production_effect_permits",
            "v4_production_effect_permit_claims",
            "v4_production_current_dispatch",
            "cross-mode",
            "route-fence decision",
        ]
        routing_contract = [
            "route-action serialization",
            "authorize_action",
            "linearizable",
            "in-flight liability",
            "rollback-launcher abi",
            "inventory digest",
        ]
        recovery_contract = [
            "source discriminants and valid state/reason pairs",
            "intent_case",
            "operationless",
            "no permission to perform any provider read",
            "production case-command and deadline controller",
            "fixture routes disabled",
            "complete affected workspace/source/work inventory",
            "zero, one, or many",
        ]
        external_contract = [
            "planned authorized transition",
            "subsequent or unplanned drift",
            "test_planned_revocation_with_verified_final_denial_passes",
            "test_unplanned_or_subsequent_revision_change_fails",
            "test_no_mutation_requires_equal_start_and_end_state",
            "never restore the old credential",
        ]
        for phrase in policy_contract:
            with self.subTest(spec="policy", phrase=phrase):
                self.assertTrue(phrase in policy, f"missing contract phrase: {phrase}")
        for phrase in routing_contract:
            with self.subTest(spec="routing", phrase=phrase):
                self.assertTrue(phrase in routing, f"missing contract phrase: {phrase}")
        for phrase in recovery_contract:
            with self.subTest(spec="recovery", phrase=phrase):
                self.assertTrue(phrase in recovery, f"missing contract phrase: {phrase}")
        with self.subTest(spec="recovery", phrase="production deadline ownership"):
            self.assertIn("at a due production case deadline", recovery)
            self.assertNotIn("the current wait contract may move", recovery)
        for phrase in external_contract:
            with self.subTest(spec="external", phrase=phrase):
                self.assertTrue(phrase in external, f"missing contract phrase: {phrase}")

    def test_initial_gap_audit_review_chain_is_durable_and_fail_closed(self):
        audit_path = Path(__file__).with_name("p1-rg0-rg1-initial-gap-audit.json")
        audit = json.loads(audit_path.read_text())
        review = audit["review"]
        review_root = CHECK.ROOT / "docs" / "v4" / "reviews"
        wrapper_path = CHECK.ROOT / "docs" / "v4" / "p1-rg0-rg1-audit-and-spec-review.md"
        wrapper = wrapper_path.read_text() if wrapper_path.is_file() else ""
        expected_reports = {
            "initial-audit-row-review.md.txt": "54e296b0ca00dc397886f188265a9f9cd2015ab0c395faf39d6d234a1c581c3d",
            "initial-audit-row-rereview-round1.md.txt": "b268cd962222ca35dcf558d45339c576e32d9bcb14da1a7e1932d80664ac848e",
            "initial-audit-row-rereview-round2.md.txt": "dccf878b83638d38a288fc2e12012a1582c08b49de33f92fba35287a629a52a6",
            "initial-cross-spec-review.md.txt": "9c310cecc839cfe334ec8a6c9c1fc940e4637e70aeb9e37e9ef0e2a1c2e6d229",
            "initial-cross-spec-rereview.md.txt": "daacc06aefec84c1a665addb2ebc42d660789101cc0897cc6df4f8698fecbc95",
        }
        final_review_reports = {
            "final-whole-change-review.md.txt": "0f1b136cbb0f24adb596bcb628848f1ec5fdf7723178cd9c0b6bfad31b1aa104",
            "final-whole-change-fix.md.txt": "d2449d8760eb4978f796d29c4b3f88a49ccb658aaeb01bd1c2df37fa0b5d8e8f",
            "final-whole-change-rereview.md.txt": "a9618776e2114435b12f147a4221a8018914517e4e21900130a5b259da2c0b5b",
        }
        self.assertEqual(audit["status"], "COMPLETE_REVIEWED")
        self.assertEqual(review.get("row_by_row"), "PASS")
        self.assertEqual(review.get("cross_spec"), "PASS")
        self.assertEqual(review.get("documentation_whole_change_review"), "PASS_AFTER_FIX_ROUND_1")
        self.assertEqual(review.get("documentation_whole_change_initial"), "FAIL: 0 Critical / 3 Important / 0 Minor")
        self.assertEqual(review.get("documentation_whole_change_fix_round"), "F1-F3 bounded documentation/evidence fixes")
        self.assertEqual(review.get("documentation_whole_change_rereview"), "PASS: 0 new Critical / 0 new Important")
        self.assertEqual(review.get("documentation_whole_change_candidate_manifest_sha256"), "cafa0bd46ede2cc8aef51058f46541068a07cd031774c8b5501270bc2385ee9a")
        self.assertEqual(review.get("final_exact_candidate_environment_audit"), "NOT RUN")
        self.assertEqual(review.get("final_exact_candidate_environment_audit_gate"), "BLOCKED")
        self.assertEqual(review.get("review_record"), "docs/v4/p1-rg0-rg1-audit-and-spec-review.md")
        self.assertEqual(len(audit["increment_decisions"]), 5)
        self.assertTrue(all(item["decision"] == "REQUIRED" for item in audit["increment_decisions"]))
        self.assertEqual(len(audit["requirements"]), 22)
        self.assertTrue(all(row["audit_status"] in {"FAIL", "NOT RUN", "HELD"} for row in audit["requirements"]))
        self.assertEqual(audit["gate_disposition"], {**audit["gate_disposition"], "RG0": "HELD", "RG1": "HELD", "P2": "BLOCKED"})

        for name, expected_sha256 in expected_reports.items():
            report_path = review_root / name
            self.assertTrue(report_path.is_file(), name)
            actual_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
            self.assertEqual(actual_sha256, expected_sha256, name)
            self.assertIn(f"reviews/{name}", wrapper)
            self.assertIn(expected_sha256, wrapper)
        for name, expected_sha256 in final_review_reports.items():
            report_path = review_root / name
            self.assertTrue(report_path.is_file(), name)
            actual_sha256 = hashlib.sha256(report_path.read_bytes()).hexdigest()
            self.assertEqual(actual_sha256, expected_sha256, name)
            self.assertIn(f"reviews/{name}", wrapper)
            self.assertIn(expected_sha256, wrapper)
        self.assertIn("`cafa0bd46ede2cc8aef51058f46541068a07cd031774c8b5501270bc2385ee9a`", wrapper)
        self.assertIn("documentation_whole_change_review: PASS_AFTER_FIX_ROUND_1", wrapper)
        self.assertIn("final exact-candidate/environment P1/RG0/RG1 audit remains NOT RUN and BLOCKED", wrapper)
        self.assertEqual(CHECK.link_errors(wrapper_path, wrapper, CHECK.ROOT), [])
        self.assertEqual(CHECK.markdown_structure_errors(wrapper_path, wrapper), [])
        current_digest_paths = [
            "docs/v4/p1-rg0-rg1-initial-gap-audit.json",
            "docs/v4/p1-rg0-rg1-initial-gap-audit.md",
            "docs/v4/p0-release.json",
            "docs/v4/p1-qualification-matrix.json",
            "docs/superpowers/specs/2026-10-13-p1-production-policy-and-revocation-design.md",
            "docs/superpowers/specs/2026-10-13-p1-production-routing-and-worker-fencing-design.md",
            "docs/superpowers/specs/2026-10-13-p1-live-schedule-fencing-design.md",
            "docs/superpowers/specs/2026-10-13-p1-production-recovery-and-alerts-design.md",
            "docs/superpowers/specs/2026-10-13-rg0-rg1-external-acceptance-design.md",
            "docs/v4/test_checks.py",
            "README.md",
            "docs/README.md",
            "docs/v4/IMPLEMENTATION-BLUEPRINT.md",
            "docs/v4/progress.md",
            "docs/v4/validation.md",
            "docs/superpowers/specs/2026-10-13-v4-multiplatform-autonomy-program-sequence.md",
            "docs/superpowers/plans/2026-10-27-p1-rg0-rg1-initial-gap-audit.md",
        ]
        for relative_path in current_digest_paths:
            current_sha256 = hashlib.sha256((CHECK.ROOT / relative_path).read_bytes()).hexdigest()
            self.assertIn(f"| `{relative_path}` | `{current_sha256}` |", wrapper)

        release = json.loads(Path(__file__).with_name("p0-release.json").read_text())
        matrix = json.loads(Path(__file__).with_name("p1-qualification-matrix.json").read_text())
        for manifest in [release["initial_commit_bound_gap_audit"], matrix["initial_commit_bound_gap_audit"]]:
            with self.subTest(manifest=manifest.get("status")):
                self.assertEqual(manifest["status"], "COMPLETE_REVIEWED")
                self.assertEqual(manifest["audit_commit"], "7c1ddcda280b510048ad61106c406171edd64f18")
                self.assertEqual(manifest["environment_manifest_sha256"], "18b15955b9a3be547bd1998ac2c1fce759c99d647fc3eeaa0ea21858e7a81436")
                self.assertEqual(manifest["RG0"], "HELD")
                self.assertEqual(manifest["RG1"], "HELD")
                self.assertEqual(manifest["P2"], "BLOCKED")
                self.assertEqual(manifest["review_record"], "docs/v4/p1-rg0-rg1-audit-and-spec-review.md")
                self.assertEqual(manifest["documentation_whole_change_review"], "PASS_AFTER_FIX_ROUND_1")
                self.assertEqual(manifest["documentation_whole_change_candidate_manifest_sha256"], "cafa0bd46ede2cc8aef51058f46541068a07cd031774c8b5501270bc2385ee9a")
                self.assertEqual(manifest["final_whole_change_review"], "PASS_AFTER_FIX_ROUND_1")
                self.assertEqual(manifest["final_exact_candidate_environment_p1_rg0_rg1_audit"], "NOT RUN")
                self.assertEqual(manifest["final_exact_candidate_environment_audit_gate"], "BLOCKED")

        spec_names = [item["spec"] for item in audit["increment_decisions"]]
        for spec_name in spec_names:
            spec_path = CHECK.ROOT / spec_name
            spec_text = spec_path.read_text()
            status_line = next((line for line in spec_text.splitlines() if "**Status:**" in line), "")
            with self.subTest(spec=spec_name):
                self.assertIn("COMPLETE_REVIEWED", status_line)
                self.assertIn("implementation and target qualification NOT RUN", status_line)
                self.assertIn("Production remains disabled", status_line)

        navigation = [
            CHECK.ROOT / "README.md",
            CHECK.ROOT / "docs/README.md",
            CHECK.ROOT / "docs/v4/IMPLEMENTATION-BLUEPRINT.md",
            CHECK.ROOT / "docs/v4/progress.md",
            CHECK.ROOT / "docs/v4/validation.md",
            CHECK.ROOT / "docs/superpowers/specs/2026-10-13-v4-multiplatform-autonomy-program-sequence.md",
            CHECK.ROOT / "docs/superpowers/plans/2026-10-27-p1-rg0-rg1-initial-gap-audit.md",
        ]
        for path in navigation:
            content = path.read_text()
            with self.subTest(navigation=path.relative_to(CHECK.ROOT)):
                self.assertIn("Initial audit/design checkpoint", content)
                self.assertIn("COMPLETE_REVIEWED", content)
                self.assertIn("five designs COMPLETE_REVIEWED", content)
                self.assertIn("implementation and target qualification NOT RUN", content)
                self.assertIn("22 final-entry rows remain non-PASS", content)
                self.assertIn("RG0/RG1 HELD", content)
                self.assertIn("Slice 7/P2 BLOCKED", content)
                self.assertIn("production disabled", content)
                self.assertIn("main unmerged", content)

    def test_active_p1_handoffs_match_reviewed_manifest_and_final_audit_state(self):
        manifest = "cafa0bd46ede2cc8aef51058f46541068a07cd031774c8b5501270bc2385ee9a"
        status = (
            f"Documentation whole-change review is PASS_AFTER_FIX_ROUND_1 for reviewer-reported manifest `{manifest}`; "
            "that review does not transfer to later content or exact-candidate CI. "
            "The final exact-candidate/environment P1/RG0/RG1 audit remains NOT RUN / BLOCKED. "
            "Implementation and target qualification remain NOT RUN."
        )
        review_status = status.rsplit("Implementation and target qualification remain NOT RUN.", 1)[0].rstrip()
        scoped_status_pages = {"docs/README.md", "docs/v4/progress.md", "docs/v4/validation.md"}
        pending_review_claim = "Post-review packaging changes have not received another whole-change rereview."

        def section(source, start, end):
            self.assertIn(start, source)
            self.assertIn(end, source)
            return source.split(start, 1)[1].split(end, 1)[0]

        readme = (CHECK.ROOT / "README.md").read_text()
        docs_readme = (CHECK.ROOT / "docs/README.md").read_text()
        blueprint = (CHECK.ROOT / "docs/v4/IMPLEMENTATION-BLUEPRINT.md").read_text()
        progress = (CHECK.ROOT / "docs/v4/progress.md").read_text()
        validation = (CHECK.ROOT / "docs/v4/validation.md").read_text()
        sequence = (CHECK.ROOT / "docs/superpowers/specs/2026-10-13-v4-multiplatform-autonomy-program-sequence.md").read_text()
        audit = (CHECK.ROOT / "docs/v4/p1-rg0-rg1-initial-gap-audit.md").read_text()
        handoffs = {
            "README.md": section(readme, "## Current status and evidence", "## Historical and separate qualification records"),
            "docs/README.md": section(docs_readme, "# Documentation", "The latest local implementation"),
            "docs/v4/IMPLEMENTATION-BLUEPRINT.md": section(blueprint, "# Salience V4 production implementation blueprint", "## 6. Requirement traceability"),
            "docs/v4/progress.md": section(progress, "## Initial commit-bound P1/RG0/RG1 residual-gap audit", "## Current publication recovery-hold integration"),
            "docs/v4/validation.md": section(validation, "## Initial commit-bound P1/RG0/RG1 residual-gap audit", "## Frozen 0043 candidate evidence summary"),
            "docs/superpowers/specs/2026-10-13-v4-multiplatform-autonomy-program-sequence.md": (
                section(sequence, "## Purpose and current boundary", "## Program control and shared invariants")
                + section(sequence, "### Slice 1 —", "### 2. Bounded residual P1 policy and revocation")
            ),
            "docs/v4/p1-rg0-rg1-initial-gap-audit.md": section(audit, "## Review and next gate", "## Audit method"),
        }
        stale_claims = [
            "Documentation whole-change review is NOT RUN",
            "the whole-change review is NOT RUN",
            "whole-change review remains NOT RUN",
            "whole-change review NOT RUN",
            "the separate whole-change review remains NOT RUN",
            "the separate whole-change review is NOT RUN",
            "the separate whole-change review and implementation/target qualification are NOT RUN",
            "the separate whole-change review, implementation, and target qualification remain NOT RUN",
            "the separate whole-change review and final exact-candidate/environment audit remain NOT RUN",
            "the separate whole-change review, implementation, target qualification, and production evidence remain NOT RUN",
            "final whole-change review NOT RUN",
        ]
        for path, content in handoffs.items():
            with self.subTest(handoff=path):
                if path in scoped_status_pages:
                    self.assertIn(review_status, content)
                else:
                    self.assertIn(status, content)
                self.assertIn("that review does not transfer to later content or exact-candidate CI.", content)
                self.assertNotIn(pending_review_claim.casefold(), content.casefold())
                for stale in stale_claims:
                    self.assertNotIn(stale.casefold(), content.casefold())

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


    def test_documentation_status_audit_covers_current_artifacts_and_hashes(self):
        import os
        import subprocess
        from collections import Counter

        audit_path = CHECK.ROOT / "docs/v4/documentation-status-audit.json"
        audit = json.loads(audit_path.read_text())
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=CHECK.ROOT).split(b"\0")
        untracked = subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=CHECK.ROOT
        ).split(b"\0")
        repository_paths = {os.fsdecode(path) for path in tracked + untracked if path}
        artifact_roots = (".agents/", ".superpowers/sdd/", "docs/", "docs-new-arch/", "prompts/")
        artifact_suffixes = {".md", ".json", ".html", ".snapshot", ".mermaid", ".b64", ".txt"}
        expected_paths = {
            path
            for path in repository_paths
            if (path in {"AGENTS.md", "README.md"} or path.startswith(artifact_roots))
            and Path(path).suffix.casefold() in artifact_suffixes
        }

        rows = audit["files"]
        paths = [row["path"] for row in rows]
        self.assertEqual(len(rows), len(expected_paths))
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(len(paths), len(set(paths)))
        self.assertEqual(set(paths), expected_paths)

        self_hash_rows = [row for row in rows if row["path"] == "docs/v4/documentation-status-audit.json"]
        self.assertEqual(len(self_hash_rows), 1)
        self.assertIsNone(self_hash_rows[0]["sha256"])
        self.assertEqual(sum(row.get("sha256") is None for row in rows), 1)
        for row in rows:
            if row["path"] == "docs/v4/documentation-status-audit.json":
                continue
            current = hashlib.sha256((CHECK.ROOT / row["path"]).read_bytes()).hexdigest()
            self.assertEqual(row["sha256"], current, row["path"])

        markdown_paths = sorted(path for path in expected_paths if Path(path).suffix.casefold() == ".md")
        self.assertEqual(audit["tracked_markdown_paths"], markdown_paths)
        self.assertEqual(audit["tracked_markdown_files"], len(markdown_paths))
        self.assertEqual(audit["documentation_artifacts"], len(rows))
        self.assertEqual(audit["current_documentation_artifacts"], len(rows))
        self.assertEqual(audit["verification"]["tracked_markdown_files"], len(markdown_paths))
        self.assertEqual(audit["verification"]["tracked_markdown_paths"], markdown_paths)

        disposition_counts = Counter(row["disposition"] for row in rows)
        summary_counts = {key: value for key, value in audit["summary"].items() if isinstance(value, int)}
        self.assertEqual(summary_counts, dict(disposition_counts))
        markdown_disposition_counts = Counter(
            row["disposition"] for row in rows if Path(row["path"]).suffix.casefold() == ".md"
        )
        markdown_summary = audit["tracked_markdown_disposition_counts"]
        self.assertEqual(markdown_summary["dispositions"], dict(markdown_disposition_counts))
        self.assertEqual(markdown_summary["tracked_markdown_paths"], len(markdown_paths))
        self.assertEqual(
            markdown_summary["update_now_paths"] + markdown_summary["non_update_paths_preserved"],
            len(markdown_paths),
        )
        self.assertEqual(
            markdown_summary["historical_immutable"]
            + markdown_summary["generated_vendor"]
            + markdown_summary["already_current"],
            markdown_summary["non_update_paths_preserved"],
        )

        for path, expected_sha256 in audit["summary"]["current_handoff_hashes"].items():
            current = hashlib.sha256((CHECK.ROOT / path).read_bytes()).hexdigest()
            self.assertEqual(expected_sha256, current, path)

    def test_policy_revocation_evidence_is_fixture_scoped_and_gate_held(self):
        evidence_path = CHECK.ROOT / "docs/v4/p1-production-policy-revocation-evidence.json"
        evidence = json.loads(evidence_path.read_text())
        self.assertEqual(
            evidence["schema_version"],
            "salience.p1-production-policy-revocation-evidence.v1",
        )
        self.assertEqual(evidence["scope"], "fixture")
        self.assertEqual(evidence["status"], "PASS_FIXTURE_ONLY")
        self.assertEqual(
            evidence["application_candidate"]["commit"],
            "3cd1e28d6358d1432e9e90a51a4fc58faeeb5c1d",
        )
        artifact = evidence["application_candidate"]["artifact_or_image"]
        self.assertTrue(
            re.fullmatch(r"[a-f0-9]{64}", artifact.get("sha256") or "")
            or artifact.get("unavailable_reason"),
            artifact,
        )
        self.assertEqual(evidence["migration_head"], "0045_production_policy_approval_guard_repair")

        environment = evidence["environment"]
        self.assertEqual(environment["scope"], "fixture")
        self.assertTrue(environment["name"])
        self.assertRegex(environment["manifest"]["sha256"], r"^[a-f0-9]{64}$")
        issuer_audience = evidence["issuer_audience"]
        self.assertTrue(issuer_audience["issuer_identifier"])
        self.assertTrue(issuer_audience["audience_identifier"])
        self.assertNotIn("key", issuer_audience)
        self.assertNotIn("token", issuer_audience)

        self.assertRegex(evidence["started_at_utc"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertRegex(evidence["finished_at_utc"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertTrue(evidence["operator"]["identifier"])
        self.assertEqual(evidence["operator"]["status"], "EXECUTED")
        self.assertEqual(evidence["reviewer"]["status"], "NOT INDEPENDENTLY REVIEWED")

        commands = evidence["test_commands"]
        self.assertGreater(len(commands), 0)
        self.assertTrue(all(command["scope"] == "fixture" for command in commands))
        migration_command = next(
            command for command in commands if command["id"] == "task6-migration-upgrade-head"
        )
        self.assertEqual(migration_command["result"]["exit_code"], 0)
        self.assertEqual(
            migration_command["result"]["migration_head"],
            "0045_production_policy_approval_guard_repair",
        )
        self.assertRegex(migration_command["log"]["sha256"], r"^[a-f0-9]{64}$")
        application_command = next(
            command for command in commands if command["id"] == "task6-final-focused-fixture-suite"
        )
        result = application_command["result"]
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["passed"], 153)
        for field in ["failed", "skipped", "errors"]:
            self.assertEqual(result[field], 0)
        self.assertRegex(application_command["log"]["sha256"], r"^[a-f0-9]{64}$")
        self.assertRegex(evidence["database_readback"]["sha256"], r"^[a-f0-9]{64}$")
        self.assertRegex(evidence["application_run"]["sha256"], r"^[a-f0-9]{64}$")
        self.assertFalse(application_command["junit"]["available"])
        self.assertTrue(application_command["junit"]["unavailable_reason"])
        self.assertIn(
            "tests/integration/test_v4_migrations.py::test_0045_repairs_stale_approval_guard_without_weakening_v1_binding",
            application_command["command"],
        )
        self.assertIn(
            "tests/integration/test_p1_production_policy_revocation.py::test_issue_denies_missing_or_wrong_current_authority",
            application_command["included_regression_cases"],
        )

        self.assertEqual(evidence["production"]["target_configuration"], "NOT RUN")
        self.assertEqual(evidence["production"]["acceptance"], "NOT RUN")
        self.assertFalse(evidence["production"]["effects_enabled"])
        self.assertEqual(evidence["production"]["route_fence_platform_acceptance"], "NOT RUN")
        self.assertEqual(evidence["gates"]["RG0"], "HELD")
        self.assertEqual(evidence["gates"]["RG1"], "HELD")
        self.assertEqual(evidence["gates"]["P2"], "BLOCKED")
        self.assertFalse(evidence["gates"]["main_merged"])

        matrix = json.loads(Path(__file__).with_name("p1-qualification-matrix.json").read_text())
        extension = matrix["p1_production_policy_revocation_fixture_extension"]
        self.assertEqual(extension["scope"], "fixture")
        self.assertEqual(extension["application_commit"], evidence["application_candidate"]["commit"])
        fixture_counts = extension["fixture_result"]
        for field in ["passed", "failed", "skipped", "errors"]:
            self.assertEqual(fixture_counts[field], result[field], f"matrix {field}")
        self.assertEqual(extension["evidence_path"], "docs/v4/p1-production-policy-revocation-evidence.json")
        self.assertEqual(
            extension["evidence_sha256"],
            hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        )
        self.assertEqual(extension["production_release_status"], "HELD")
        self.assertEqual(extension["gate_disposition"], {"RG0": "HELD", "RG1": "HELD", "P2": "BLOCKED"})
        expected_rows = {"R05", "R07", "R10", "R13", "R15", "R17", "R18", "R23F"}
        self.assertEqual(set(extension["applicable_rows"]), expected_rows)
        requirements = {row["id"]: row for row in matrix["requirements"]}
        for row_id in expected_rows - {"R23F"}:
            with self.subTest(requirement=row_id):
                row = requirements[row_id]
                self.assertEqual(row["production_release_status"], "HELD")
                self.assertEqual(row["policy_revocation_fixture_extension"]["evidence_path"], extension["evidence_path"])
                self.assertEqual(row["policy_revocation_fixture_extension"]["scope"], "fixture")
        r23f = matrix["initial_commit_bound_gap_audit"]["rows"]["R23F"]
        self.assertEqual(r23f["status"], "HELD")
        self.assertEqual(r23f["policy_revocation_fixture_extension"]["evidence_path"], extension["evidence_path"])
        self.assertEqual(r23f["policy_revocation_fixture_extension"]["scope"], "fixture")

        status_audit = json.loads((CHECK.ROOT / "docs/v4/documentation-status-audit.json").read_text())
        status_checkpoints = [
            (status_audit["current_task6_fixture_checkpoint"], "status audit"),
            (status_audit["verification"]["current_task6_fixture_checkpoint"], "verification status"),
        ]
        for status_checkpoint, label in status_checkpoints:
            self.assertEqual(status_checkpoint["application_commit"], evidence["application_candidate"]["commit"])
            self.assertEqual(status_checkpoint["migration_head"], evidence["migration_head"])
            for field in ["passed", "failed", "skipped", "errors"]:
                self.assertEqual(status_checkpoint[field], result[field], f"{label} {field}")
        documentation_checks = status_audit["verification"]["task6_documentation_checks"]
        self.assertTrue(documentation_checks["docs_v4_test_checks"].startswith("PASS"))
        self.assertTrue(documentation_checks["phase9_documentation_contract"].startswith("PASS"))
        self.assertTrue(documentation_checks["verify_phase9"].startswith("FAILED_PRE_CANDIDATE"))
        self.assertIn("not acceptance evidence", documentation_checks["verify_phase9"])

        scoped_checkpoint_lines = [
            ("docs/README.md", "**Current checkpoint — 2026-10-27:**"),
            ("docs/README.md", "P0 and DG1 remain locally qualified."),
            ("docs/README.md", "The [initial audit]"),
            ("docs/v4/validation.md", "**Current checkpoint — 2026-10-27:**"),
            ("docs/v4/validation.md", "See the [audit report]"),
            ("docs/v4/progress.md", "**Current checkpoint — 2026-10-27:**"),
            ("docs/v4/progress.md", "**APPROVED program sequence:**"),
            ("docs/v4/progress.md", "See the [audit report]"),
        ]
        for relative_path, prefix in scoped_checkpoint_lines:
            with self.subTest(path=relative_path, prefix=prefix):
                lines = (CHECK.ROOT / relative_path).read_text().splitlines()
                line = next(item for item in lines if item.startswith(prefix))
                self.assertIn("production integration and target qualification", line.casefold())
                self.assertNotIn("implementation and target qualification", line.casefold())
        initial_audit_lines = [
            (CHECK.ROOT / path).read_text().splitlines()[2]
            for path in ["docs/README.md", "docs/v4/validation.md", "docs/v4/progress.md"]
        ]
        self.assertTrue(all("implementation and target qualification NOT RUN" in line for line in initial_audit_lines))


if __name__ == "__main__":
    unittest.main()
