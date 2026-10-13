"""Check the pinned, reference-only AgentTube research snapshot."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = Path("docs/v4/agenttube-adoption-evidence.json")
UPSTREAM_URL = "https://github.com/darkzOGx/youtube-automation-agent"
BRANCH_HINT = "master"
PIN = "e26792ae00dc1ec2434faf32632896609396dfad"
PINNED_TREE = "3c6b1d6c4a636ba75ac743f4c6e7fe6a181aef45"
REPORT_HASHES = {
    "docs/research/youtube-agent/architecture-audit.md": "23daa3962eda5f876be12f166966dc84fc06e9395d904992ff7672ed75564485",
    "docs/research/youtube-agent/ops-quality-audit.md": "6364f88a7acdb24e6e5f1449248a4b0c0e89c142441c8d460d255baa0eee28f8",
    "docs/research/youtube-agent/salience-gap-audit.md": "93c0822ca6ed4b965b699d8b28567ad51d537464783e3a482c9c621990fc331a",
    "docs/research/youtube-agent/orchestrator-synthesis.md": "8055a07720d022fdd469266c9e90ae69264b5b129f0f6c7cebfe7e359dea7a14",
}
EXPECTED_LICENSE_SHA256 = "0637d0e4accdfba32d1f8723892a882fe85057ea4860ec35bfcca578782e49a0"
EXPECTED_AUTHOR_DATE = "2026-10-01T18:30:26-05:00"
EXPECTED_AUTHOR = "darkzOGx <haithemn8@gmail.com>"
RESEARCH_INSPECTED_HEAD = "e7a24a38ef920da3af071a51a288b859db9aaef1"
HISTORICAL_APPLICATION_CANDIDATE = "7878208e7a353f93bb70279ad0dd7f0760f93ec4"
IMPLEMENTATION_BASE = "dc2fe1c497f5a4aca27e7960985c5b91b3bba21e"
REMOTE_FEATURE_REF = "refs/heads/feat/archv4-p0-foundation"
REMOTE_FEATURE_SHA = "d0311725686d81b7b4747c2d6225986e5ea3a524"
EVIDENCE_CLASSES = {
    "upstream_verification",
    "historical_salience_qualification",
    "current_head_repository_verification",
    "release_approval",
}
REFERENCE_GITLINK = "youtube-automation-agent"
SUBMODULE_CONFIG = {
    "submodule.youtube-automation-agent.path": REFERENCE_GITLINK,
    "submodule.youtube-automation-agent.url": UPSTREAM_URL,
    "submodule.youtube-automation-agent.branch": BRANCH_HINT,
}
TEXT_SUFFIXES = {
    ".bash",
    ".cfg",
    ".env",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".lock",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
TEXT_FILENAMES = {"Dockerfile", "Containerfile", "Makefile", "Procfile"}
SKIP_SCAN_DIRS = {".git", ".venv", "__pycache__", "node_modules", "youtube-automation-agent"}
UPSTREAM_REFERENCE = re.compile(
    r"youtube[-_]automation[-_]agent|darkzogx/youtube-automation-agent|agenttube",
    re.IGNORECASE,
)
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
GIT_SUBMODULE_COMMAND = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?submodule\b", re.IGNORECASE)
DIRECT_AGENTTUBE_NODE_COMMAND = re.compile(
    r"\bcd\s+['\"]?(?:\./)?youtube-automation-agent/?['\"]?"
    r"\s*(?:&&[ \t]*|\r?\n[ \t]*)(?:node|nodejs)\b",
    re.IGNORECASE,
)


def _is_mapping(value: object) -> bool:
    return isinstance(value, dict)


def _require_equal(errors: list[str], actual: object, expected: object, label: str) -> None:
    if actual != expected:
        errors.append(f"{label} must be {expected!r}; found {actual!r}")


def _report_path(root: Path, value: object, errors: list[str]) -> Path | None:
    if not isinstance(value, str) or not value:
        errors.append("report path must be a non-empty repository-relative path")
        return None
    if "\\" in value:
        errors.append(f"report path must use repository-relative POSIX syntax: {value!r}")
        return None
    relative = PurePosixPath(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        errors.append(f"report path must stay inside the repository: {value!r}")
        return None
    resolved_root = root.resolve()
    resolved_path = (resolved_root / Path(*relative.parts)).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        errors.append(f"report path resolves outside the repository: {value!r}")
        return None
    return resolved_path


def validate_manifest(manifest: dict, root: Path) -> list[str]:
    """Validate immutable upstream provenance, tracked report hashes, and gates."""
    errors: list[str] = []
    if not _is_mapping(manifest):
        return ["evidence manifest must be a JSON object"]
    _require_equal(errors, manifest.get("schema_version"), 1, "schema_version")

    upstream = manifest.get("upstream")
    if not _is_mapping(upstream):
        errors.append("upstream evidence must be an object")
        upstream = {}
    _require_equal(errors, upstream.get("url"), UPSTREAM_URL, "upstream.url")
    _require_equal(errors, upstream.get("branch_hint"), BRANCH_HINT, "upstream.branch_hint")
    _require_equal(errors, upstream.get("pin"), PIN, "upstream.pin")
    fetched_tip = upstream.get("fetched_master_tip")
    if not isinstance(fetched_tip, str) or not FULL_SHA.fullmatch(fetched_tip):
        errors.append("upstream.fetched_master_tip must be a full lowercase commit SHA")
    if upstream.get("pin_reachable_from_master") is not True:
        errors.append("upstream pin must be recorded as reachable from the fetched master tip")
    _require_equal(errors, upstream.get("tree"), PINNED_TREE, "upstream.tree")

    commit = upstream.get("commit")
    if not _is_mapping(commit):
        errors.append("upstream.commit must record pinned commit metadata")
        commit = {}
    _require_equal(errors, commit.get("author_date"), EXPECTED_AUTHOR_DATE, "upstream.commit.author_date")
    _require_equal(errors, commit.get("author"), EXPECTED_AUTHOR, "upstream.commit.author")

    license_evidence = upstream.get("license")
    if not _is_mapping(license_evidence):
        errors.append("upstream.license must record static license evidence")
        license_evidence = {}
    _require_equal(errors, license_evidence.get("path"), "LICENSE", "upstream.license.path")
    _require_equal(errors, license_evidence.get("sha256"), EXPECTED_LICENSE_SHA256, "upstream.license.sha256")

    reports = manifest.get("reports")
    if not isinstance(reports, list):
        errors.append("reports must be a list of the four reviewed research reports")
        reports = []
    seen_paths: list[str] = []
    for index, report in enumerate(reports):
        label = f"reports[{index}]"
        if not _is_mapping(report):
            errors.append(f"{label} must be an object")
            continue
        relative = report.get("path")
        resolved = _report_path(root, relative, errors)
        if isinstance(relative, str):
            seen_paths.append(relative)
        if relative not in REPORT_HASHES:
            errors.append(f"{label}.path is not an approved repository-relative research report: {relative!r}")
            continue
        expected_hash = REPORT_HASHES[relative]
        _require_equal(errors, report.get("sha256"), expected_hash, f"{label}.sha256")
        if resolved is None:
            continue
        try:
            actual_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except OSError as exc:
            errors.append(f"{label} report is missing or unreadable at {relative!r}: {exc}")
            continue
        if actual_hash != expected_hash:
            errors.append(f"{label} report hash mismatch for {relative!r}: expected {expected_hash}, found {actual_hash}")
    if len(reports) != len(REPORT_HASHES) or set(seen_paths) != set(REPORT_HASHES) or len(seen_paths) != len(set(seen_paths)):
        errors.append("reports must contain each of the four approved report paths exactly once")

    verification = manifest.get("upstream_verification")
    if not _is_mapping(verification):
        errors.append("upstream_verification must be an object")
        verification = {}
    _require_equal(errors, verification.get("evidence_class"), "upstream_verification", "upstream_verification.evidence_class")
    _require_equal(errors, verification.get("checkout_commit"), PIN, "upstream_verification.checkout_commit")
    _require_equal(errors, verification.get("npm_ci"), "PASS", "upstream_verification.npm_ci")
    _require_equal(errors, verification.get("npm_ci_command"), "npm ci --no-audit --no-fund", "upstream_verification.npm_ci_command")
    _require_equal(errors, verification.get("lint"), "PASS", "upstream_verification.lint")
    _require_equal(errors, verification.get("lint_command"), "npm run lint", "upstream_verification.lint_command")
    system_cases = verification.get("system_cases")
    if not _is_mapping(system_cases):
        errors.append("upstream_verification.system_cases must record the system suite result")
        system_cases = {}
    _require_equal(errors, system_cases.get("command"), "npm test", "upstream_verification.system_cases.command")
    _require_equal(errors, system_cases.get("passed"), 47, "upstream_verification.system_cases.passed")
    _require_equal(errors, system_cases.get("total"), 47, "upstream_verification.system_cases.total")
    _require_equal(errors, system_cases.get("result"), "PASS", "upstream_verification.system_cases.result")
    effects = verification.get("effects")
    if not _is_mapping(effects):
        errors.append("upstream_verification.effects must record the no-live boundary")
        effects = {}
    for effect_key in (
        "setup_or_oauth_ran",
        "live_provider_calls_ran",
        "paid_generation_ran",
        "real_publishing_calls_ran",
    ):
        if effects.get(effect_key) is not False:
            errors.append(f"upstream_verification.effects.{effect_key} must be false")

    salience = manifest.get("salience_evidence")
    if not _is_mapping(salience):
        errors.append("salience_evidence must keep historical and current evidence separate")
        salience = {}
    _require_equal(errors, salience.get("research_inspected_head"), RESEARCH_INSPECTED_HEAD, "salience_evidence.research_inspected_head")
    historical = salience.get("historical_qualification")
    if not _is_mapping(historical):
        errors.append("salience_evidence.historical_qualification must be an object")
        historical = {}
    _require_equal(errors, historical.get("evidence_class"), "historical_salience_qualification", "historical_qualification.evidence_class")
    _require_equal(errors, historical.get("application_candidate"), HISTORICAL_APPLICATION_CANDIDATE, "historical_qualification.application_candidate")
    _require_equal(errors, historical.get("focused_passed"), 116, "historical_qualification.focused_passed")
    _require_equal(errors, historical.get("full_non_live_passed"), 614, "historical_qualification.full_non_live_passed")
    if historical.get("applies_only_to_application_candidate") is not True:
        errors.append("historical Salience qualification must remain bound only to its application candidate")

    current = salience.get("current_head")
    if not _is_mapping(current):
        errors.append("salience_evidence.current_head must record the implementation and remote feature heads")
        current = {}
    _require_equal(errors, current.get("evidence_class"), "current_head_repository_verification", "current_head.evidence_class")
    _require_equal(errors, current.get("implementation_branch"), "work/agenttube-reference-adoption-20261013", "current_head.implementation_branch")
    _require_equal(errors, current.get("superproject_head_before_adoption_commit"), IMPLEMENTATION_BASE, "current_head.superproject_head_before_adoption_commit")
    _require_equal(errors, current.get("remote_feature_ref"), REMOTE_FEATURE_REF, "current_head.remote_feature_ref")
    _require_equal(errors, current.get("remote_feature_sha"), REMOTE_FEATURE_SHA, "current_head.remote_feature_sha")
    _require_equal(errors, current.get("exact_head_ci"), "NOT RUN", "current_head.exact_head_ci")
    _require_equal(errors, current.get("exact_head_review"), "NOT RUN", "current_head.exact_head_review")
    classes = salience.get("evidence_classes")
    if not isinstance(classes, list) or set(classes) != EVIDENCE_CLASSES or len(classes) != len(EVIDENCE_CLASSES):
        errors.append("salience_evidence.evidence_classes must label upstream, historical, current-head, and release evidence separately")

    release = manifest.get("release_state")
    if not _is_mapping(release):
        errors.append("release_state must preserve the current V4 release gates")
        release = {}
    if release.get("production_effects_enabled") is not False:
        errors.append("release_state.production_effects_enabled must remain false")
    if type(release.get("publication_budget")) is not int or release.get("publication_budget") != 0:
        errors.append("release_state.publication_budget must remain zero")
    _require_equal(errors, release.get("rg0"), "HELD", "release_state.rg0")
    _require_equal(errors, release.get("rg1"), "HELD", "release_state.rg1")
    phases = release.get("phases")
    if not _is_mapping(phases):
        errors.append("release_state.phases must record P2 through P7")
        phases = {}
    for phase in ("P2", "P3", "P4", "P5", "P7"):
        _require_equal(errors, phases.get(phase), "GATED", f"release_state.phases.{phase}")
    _require_equal(errors, phases.get("P6"), "DISABLED", "release_state.phases.P6")
    _require_equal(errors, release.get("main"), "UNMERGED", "release_state.main")
    _require_equal(errors, release.get("release_approval"), "NOT GRANTED", "release_state.release_approval")
    return errors


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )


def validate_gitlink(root: Path, manifest: dict) -> list[str]:
    """Check .gitmodules and the staged gitlink without initializing it."""
    errors: list[str] = []
    if not _is_mapping(manifest):
        return ["gitlink validation requires an evidence manifest object"]
    upstream = manifest.get("upstream")
    expected_pin = upstream.get("pin") if _is_mapping(upstream) else None
    if expected_pin != PIN:
        errors.append(f"manifest pin for the gitlink must be {PIN}")

    modules_file = root / ".gitmodules"
    if not modules_file.is_file():
        errors.append(".gitmodules is missing")
    else:
        config = subprocess.run(
            ["git", "config", "--file", str(modules_file), "--get-regexp", r"^submodule\..*"],
            capture_output=True,
            text=True,
            check=False,
        )
        if config.returncode != 0:
            errors.append(f"could not read .gitmodules entries: {config.stderr.strip() or config.returncode}")
        else:
            records: list[tuple[str, str]] = []
            for line in config.stdout.splitlines():
                key, separator, value = line.partition(" ")
                if not separator:
                    errors.append(f"invalid .gitmodules entry: {line!r}")
                else:
                    records.append((key.lower(), value))
            if len(records) != len(SUBMODULE_CONFIG) or dict(records) != SUBMODULE_CONFIG:
                errors.append(".gitmodules must contain only the approved path, HTTPS URL, and master branch hint")

    index = _git(root, "ls-files", "--stage", "--", REFERENCE_GITLINK)
    if index.returncode != 0:
        errors.append(f"could not read the Git index for {REFERENCE_GITLINK}: {index.stderr.strip() or index.returncode}")
    else:
        entries = [line for line in index.stdout.splitlines() if line]
        if len(entries) != 1:
            errors.append(f"Git index must contain one gitlink entry for {REFERENCE_GITLINK}; found {len(entries)}")
        else:
            metadata, separator, indexed_path = entries[0].partition("\t")
            fields = metadata.split()
            if not separator or len(fields) != 3:
                errors.append(f"invalid Git index entry for {REFERENCE_GITLINK}: {entries[0]!r}")
            else:
                mode, object_id, stage = fields
                if indexed_path != REFERENCE_GITLINK or mode != "160000" or object_id != PIN or stage != "0":
                    errors.append(
                        f"Git index must contain mode 160000, pin {PIN}, stage 0 at {REFERENCE_GITLINK}; found {entries[0]!r}"
                    )

    worktree = root / REFERENCE_GITLINK
    git_metadata = worktree / ".git"
    if git_metadata.exists():
        result = _git(worktree, "rev-parse", "HEAD")
        if result.returncode != 0:
            errors.append(f"initialized submodule HEAD could not be read: {result.stderr.strip() or result.returncode}")
        elif result.stdout.strip() != PIN:
            errors.append(f"initialized submodule HEAD must match gitlink pin {PIN}; found {result.stdout.strip()!r}")

        symbolic_ref = _git(worktree, "symbolic-ref", "--quiet", "HEAD")
        if symbolic_ref.returncode == 0:
            errors.append(
                f"initialized submodule must be detached at gitlink pin; HEAD names {symbolic_ref.stdout.strip()!r}"
            )
        elif symbolic_ref.returncode != 1:
            errors.append(
                "initialized submodule detached HEAD check failed: "
                f"{symbolic_ref.stderr.strip() or symbolic_ref.returncode}"
            )

        status = _git(worktree, "status", "--porcelain", "--untracked-files=all")
        if status.returncode != 0:
            errors.append(
                f"initialized submodule status check failed: {status.stderr.strip() or status.returncode}"
            )
        elif status.stdout:
            errors.append(
                f"initialized submodule worktree must be clean; git status found changes: {status.stdout!r}"
            )
    return errors


def _workflow_errors(root: Path) -> list[str]:
    errors: list[str] = []
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return [".github/workflows is missing; normal CI submodule setup cannot be checked"]
    workflows = sorted(
        path for path in workflow_dir.iterdir() if path.is_file() and path.suffix in {".yml", ".yaml"}
    )
    for workflow in workflows:
        relative = workflow.relative_to(root).as_posix()
        try:
            content = workflow.read_text(encoding="utf-8")
            lines = content.splitlines()
        except (OSError, UnicodeError) as exc:
            errors.append(f"cannot read workflow {relative}: {exc}")
            continue
        for number, line in enumerate(lines, start=1):
            submodules = re.match(r"^\s*submodules\s*:\s*(.*?)\s*(?:#.*)?$", line, re.IGNORECASE)
            if submodules:
                value = submodules.group(1).strip().strip("\"'").lower()
                if value not in {"false", "no"}:
                    errors.append(f"workflow {relative}:{number} opts into submodule checkout")
            if GIT_SUBMODULE_COMMAND.search(line):
                errors.append(f"workflow {relative}:{number} invokes git submodule setup")
            if re.search(r"\b(npm|pnpm|yarn)\s+(ci|install|start|run)\b", line, re.IGNORECASE):
                errors.append(f"workflow {relative}:{number} runs a Node package command in normal CI")
        runtime = DIRECT_AGENTTUBE_NODE_COMMAND.search(content)
        if runtime:
            number = content.count("\n", 0, runtime.start()) + 1
            errors.append(f"workflow {relative}:{number} directly runs Node from {REFERENCE_GITLINK}")
    return errors


def _application_reference_errors(root: Path) -> list[str]:
    errors: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        parts = relative.parts
        if any(part in SKIP_SCAN_DIRS for part in parts):
            continue
        if not parts or parts[0] in {"docs", "tests", ".github"}:
            continue
        if relative.as_posix() == ".gitmodules":
            continue
        if path.name not in TEXT_FILENAMES and path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if UPSTREAM_REFERENCE.search(content):
            errors.append(f"application/package/deployment file references the AgentTube submodule: {relative.as_posix()}")
    return errors


def validate_reference_boundary(root: Path) -> list[str]:
    """Reject CI initialization and app/package/deployment coupling to upstream."""
    resolved_root = root.resolve()
    return _workflow_errors(resolved_root) + _application_reference_errors(resolved_root)


def verify(root: Path = ROOT) -> list[str]:
    """Run the evidence, Git metadata, and runtime-boundary checks."""
    root = Path(root).resolve()
    evidence_file = root / EVIDENCE_PATH
    errors: list[str] = []
    if not evidence_file.is_file():
        errors.append(f"evidence manifest is missing: {EVIDENCE_PATH.as_posix()}")
        manifest: dict = {}
    else:
        try:
            manifest = json.loads(evidence_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"could not read evidence manifest: {exc}")
            manifest = {}
    errors.extend(validate_manifest(manifest, root))
    errors.extend(validate_gitlink(root, manifest))
    errors.extend(validate_reference_boundary(root))
    return errors


def main() -> int:
    errors = verify()
    if errors:
        print("FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
