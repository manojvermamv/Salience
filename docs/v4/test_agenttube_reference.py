"""Contract tests for the reference-only AgentTube evidence boundary."""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VERIFIER_PATH = Path(__file__).with_name("verify_agenttube_reference.py")
EVIDENCE_PATH = Path(__file__).with_name("agenttube-adoption-evidence.json")
UPSTREAM_URL = "https://github.com/darkzOGx/youtube-automation-agent"
PIN = "e26792ae00dc1ec2434faf32632896609396dfad"
TREE = "3c6b1d6c4a636ba75ac743f4c6e7fe6a181aef45"
REPORTS = {
    "docs/research/youtube-agent/architecture-audit.md": "23daa3962eda5f876be12f166966dc84fc06e9395d904992ff7672ed75564485",
    "docs/research/youtube-agent/ops-quality-audit.md": "6364f88a7acdb24e6e5f1449248a4b0c0e89c142441c8d460d255baa0eee28f8",
    "docs/research/youtube-agent/salience-gap-audit.md": "93c0822ca6ed4b965b699d8b28567ad51d537464783e3a482c9c621990fc331a",
    "docs/research/youtube-agent/orchestrator-synthesis.md": "8055a07720d022fdd469266c9e90ae69264b5b129f0f6c7cebfe7e359dea7a14",
}
HISTORICAL_INSPECTED_HEAD = "e7a24a38ef920da3af071a51a288b859db9aaef1"
HISTORICAL_QUALIFIED_CANDIDATE = "7878208e7a353f93bb70279ad0dd7f0760f93ec4"
IMPLEMENTATION_BASE = "dc2fe1c497f5a4aca27e7960985c5b91b3bba21e"
REMOTE_FEATURE_REF = "refs/heads/feat/archv4-p0-foundation"
REMOTE_FEATURE_SHA = "d0311725686d81b7b4747c2d6225986e5ea3a524"


_SPEC = importlib.util.spec_from_file_location("agenttube_reference_verifier", VERIFIER_PATH)
VERIFIER = importlib.util.module_from_spec(_SPEC) if _SPEC else None
try:
    if VERIFIER is not None and _SPEC is not None and _SPEC.loader is not None:
        _SPEC.loader.exec_module(VERIFIER)
    else:
        VERIFIER = None
except FileNotFoundError:
    VERIFIER = None


def manifest_fixture() -> dict:
    return {
        "schema_version": 1,
        "upstream": {
            "url": UPSTREAM_URL,
            "branch_hint": "master",
            "pin": PIN,
            "fetched_master_tip": PIN,
            "pin_reachable_from_master": True,
            "tree": TREE,
            "commit": {
                "author_date": "2026-10-01T18:30:26-05:00",
                "author": "darkzOGx <haithemn8@gmail.com>",
            },
            "license": {
                "path": "LICENSE",
                "sha256": "0637d0e4accdfba32d1f8723892a882fe85057ea4860ec35bfcca578782e49a0",
            },
        },
        "reports": [
            {"path": path, "sha256": digest} for path, digest in REPORTS.items()
        ],
        "upstream_verification": {
            "evidence_class": "upstream_verification",
            "checkout_commit": PIN,
            "npm_ci": "PASS",
            "npm_ci_command": "npm ci --no-audit --no-fund",
            "lint": "PASS",
            "lint_command": "npm run lint",
            "system_cases": {"command": "npm test", "passed": 47, "total": 47, "result": "PASS"},
            "effects": {
                "setup_or_oauth_ran": False,
                "live_provider_calls_ran": False,
                "paid_generation_ran": False,
                "real_publishing_calls_ran": False,
            },
        },
        "salience_evidence": {
            "research_inspected_head": HISTORICAL_INSPECTED_HEAD,
            "historical_qualification": {
                "evidence_class": "historical_salience_qualification",
                "application_candidate": HISTORICAL_QUALIFIED_CANDIDATE,
                "focused_passed": 116,
                "full_non_live_passed": 614,
                "applies_only_to_application_candidate": True,
            },
            "current_head": {
                "evidence_class": "current_head_repository_verification",
                "implementation_branch": "work/agenttube-reference-adoption-20261013",
                "superproject_head_before_adoption_commit": IMPLEMENTATION_BASE,
                "remote_feature_ref": REMOTE_FEATURE_REF,
                "remote_feature_sha": REMOTE_FEATURE_SHA,
                "exact_head_ci": "NOT RUN",
                "exact_head_review": "NOT RUN",
            },
            "evidence_classes": [
                "upstream_verification",
                "historical_salience_qualification",
                "current_head_repository_verification",
                "release_approval",
            ],
        },
        "release_state": {
            "production_effects_enabled": False,
            "publication_budget": 0,
            "rg0": "HELD",
            "rg1": "HELD",
            "phases": {
                "P2": "GATED",
                "P3": "GATED",
                "P4": "GATED",
                "P5": "GATED",
                "P6": "DISABLED",
                "P7": "GATED",
            },
            "main": "UNMERGED",
            "release_approval": "NOT GRANTED",
        },
    }


def copy_reports(root: Path) -> None:
    for relative in REPORTS:
        source = ROOT / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            shutil.copyfile(source, destination)
        else:
            destination.write_text("placeholder until the reviewed report is copied\n")


def init_gitlink_repository(root: Path, *, initialized_head: str | None = None) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-c", "init.defaultBranch=main", "init", str(root)],
        check=True,
        capture_output=True,
        text=True,
    )
    (root / ".gitmodules").write_text(
        '[submodule "youtube-automation-agent"]\n'
        '\tpath = youtube-automation-agent\n'
        f"\turl = {UPSTREAM_URL}\n"
        "\tbranch = master\n"
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "update-index",
            "--add",
            "--cacheinfo",
            f"160000,{PIN},youtube-automation-agent",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    if initialized_head is not None:
        submodule = root / "youtube-automation-agent"
        subprocess.run(
            ["git", "-c", "init.defaultBranch=main", "init", str(submodule)],
            check=True,
            capture_output=True,
            text=True,
        )
        # rev-parse reads a detached HEAD hash without requiring the object body.
        (submodule / ".git" / "HEAD").write_text(f"{initialized_head}\n")
    return manifest_fixture()


def copy_workflows(root: Path) -> None:
    source_dir = ROOT / ".github" / "workflows"
    target_dir = root / ".github" / "workflows"
    target_dir.mkdir(parents=True, exist_ok=True)
    if source_dir.is_dir():
        for source in source_dir.iterdir():
            if source.suffix in {".yml", ".yaml"}:
                shutil.copyfile(source, target_dir / source.name)


class AgentTubeReferenceContracts(unittest.TestCase):
    def verifier(self):
        self.assertIsNotNone(VERIFIER, "reference verifier is missing")
        return VERIFIER

    def actual_manifest(self) -> dict:
        self.assertTrue(EVIDENCE_PATH.is_file(), "evidence manifest is missing")
        return json.loads(EVIDENCE_PATH.read_text())

    def test_manifest_pins_expected_repo_branch_and_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_reports(root)
            errors = self.verifier().validate_manifest(manifest_fixture(), root)
        self.assertEqual(errors, [])
        for field, value in (("url", "https://example.invalid/fork"), ("branch_hint", "main"), ("pin", "a" * 40)):
            damaged = manifest_fixture()
            damaged["upstream"][field] = value
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                copy_reports(root)
                errors = self.verifier().validate_manifest(damaged, root)
            with self.subTest(field=field):
                self.assertTrue(errors, f"altered upstream {field} must be rejected")

    def test_manifest_keeps_approved_pin_when_master_tip_advances(self):
        manifest = manifest_fixture()
        manifest["upstream"]["fetched_master_tip"] = "f" * 40
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_reports(root)
            errors = self.verifier().validate_manifest(manifest, root)
        self.assertEqual(errors, [])

    def test_pin_not_reachable_from_master_is_rejected(self):
        manifest = manifest_fixture()
        manifest["upstream"]["pin_reachable_from_master"] = False
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_reports(root)
            errors = self.verifier().validate_manifest(manifest, root)
        self.assertTrue(any("reachable" in error.lower() for error in errors), errors)

    def test_research_report_hashes_match_tracked_copies(self):
        manifest = self.actual_manifest()
        self.assertEqual(set(item["path"] for item in manifest["reports"]), set(REPORTS))
        self.assertEqual(self.verifier().validate_manifest(manifest, ROOT), [])

    def test_frozen_research_citations_are_exempt_only_in_the_four_reports(self):
        checker_spec = importlib.util.spec_from_file_location(
            "blueprint_check_for_agenttube", Path(__file__).with_name("check.py")
        )
        checker = importlib.util.module_from_spec(checker_spec)
        checker_spec.loader.exec_module(checker)
        for relative in REPORTS:
            report = ROOT / relative
            with self.subTest(report=relative):
                self.assertEqual(checker.link_errors(report, report.read_text(), ROOT), [])

        broken_target = "/tmp/youtube-automation-agent-research-20261012/package.json:7"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            other_report = root / "docs" / "research" / "other" / "audit.md"
            other_report.parent.mkdir(parents=True)
            content = f"[source]({broken_target})\n"
            other_report.write_text(content)
            errors = checker.link_errors(other_report, content, root)
        self.assertEqual(len(errors), 1)
        self.assertIn(broken_target, errors[0])

    def test_report_path_must_stay_inside_repository(self):
        manifest = manifest_fixture()
        manifest["reports"][0]["path"] = "../outside/architecture-audit.md"
        with tempfile.TemporaryDirectory() as temporary:
            errors = self.verifier().validate_manifest(manifest, Path(temporary))
        self.assertTrue(any("repository" in error.lower() or "path" in error.lower() for error in errors), errors)

    def test_changed_report_copy_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_reports(root)
            changed = root / next(iter(REPORTS))
            changed.write_text("changed report\n")
            errors = self.verifier().validate_manifest(manifest_fixture(), root)
        self.assertTrue(any("hash" in error.lower() for error in errors), errors)

    def test_gitmodules_and_gitlink_match_the_reviewed_pin(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "valid"
            manifest = init_gitlink_repository(root)
            self.assertEqual(self.verifier().validate_gitlink(root, manifest), [])

            (root / ".gitmodules").write_text(
                '[submodule "youtube-automation-agent"]\n'
                '\tpath = youtube-automation-agent\n'
                "\turl = https://example.invalid/fork\n"
                "\tbranch = master\n"
            )
            errors = self.verifier().validate_gitlink(root, manifest)
            self.assertTrue(errors, "a changed submodule URL must be rejected")

    def test_initialized_submodule_must_match_gitlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "valid"
            manifest = init_gitlink_repository(root, initialized_head=PIN)
            self.assertEqual(self.verifier().validate_gitlink(root, manifest), [])
            (root / "youtube-automation-agent" / ".git" / "HEAD").write_text("a" * 40 + "\n")
            errors = self.verifier().validate_gitlink(root, manifest)
        self.assertTrue(any("head" in error.lower() or "initialized" in error.lower() for error in errors), errors)

    def test_normal_ci_does_not_initialize_or_install_submodule(self):
        errors = self.verifier().validate_reference_boundary(ROOT)
        self.assertEqual(errors, [])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_workflows(root)
            workflow = root / ".github" / "workflows" / "unsafe.yml"
            workflow.write_text(
                "steps:\n"
                "  - uses: actions/checkout@v4\n"
                "    with:\n"
                "      submodules: recursive\n"
                "  - run: git submodule update --init --recursive\n"
            )
            errors = self.verifier().validate_reference_boundary(root)
        self.assertTrue(any("workflow" in error.lower() or "submodule" in error.lower() for error in errors), errors)

    def test_runtime_and_package_have_no_submodule_reference(self):
        self.assertEqual(self.verifier().validate_reference_boundary(ROOT), [])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copy_workflows(root)
            source = root / "src" / "unsafe_adapter.py"
            source.parent.mkdir(parents=True)
            source.write_text("import youtube_automation_agent\n")
            errors = self.verifier().validate_reference_boundary(root)
        self.assertTrue(any("src/unsafe_adapter.py" in error for error in errors), errors)

    def test_evidence_keeps_all_current_release_gates(self):
        manifest = self.actual_manifest()
        self.assertEqual(self.verifier().validate_manifest(manifest, ROOT), [])
        for update in [
            {"production_effects_enabled": True},
            {"publication_budget": 1},
            {"rg0": "PASS"},
            {"rg1": "PASS"},
            {"phases": {**manifest["release_state"]["phases"], "P2": "OPEN"}},
            {"phases": {**manifest["release_state"]["phases"], "P6": "ENABLED"}},
            {"main": "MERGED"},
        ]:
            damaged = copy.deepcopy(manifest)
            damaged["release_state"].update(update)
            with self.subTest(update=update):
                self.assertTrue(self.verifier().validate_manifest(damaged, ROOT))


if __name__ == "__main__":
    unittest.main()
