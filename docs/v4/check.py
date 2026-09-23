"""Read-only verification of the V4 documentation contract and its evidence."""

from __future__ import annotations

import argparse
import base64
from collections import Counter
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT = "docs/v4/IMPLEMENTATION-BLUEPRINT.md"
EXPECTED = {f"R{number:02d}" for number in range(1, 71)} | {"R14F", "R16F", "R23F", "R25F", "R50F"}
EXTERNAL_CHECKS = {
    "independent safety review",
    "production identity and ingress",
    "production storage and restore",
    "production collector and alert delivery",
    "remote repository gate enforcement",
}
DEVELOPMENT_CHECKS = {"isolated no-effects profile", "restricted database role", "P0 regression", "documentation contract", "deferral contract"}


def digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def requirement_errors(content: str) -> list[str]:
    errors = []
    rows = {}
    for line in content.splitlines():
        if not re.match(r"\| R\d\dF? \|", line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 7 or any(not cell for cell in cells):
            errors.append(f"invalid mapping: {line[:50]}")
            continue
        identity, requirement, phase, dependencies, acceptance, migration, gate = cells
        if identity in rows:
            errors.append(f"duplicate requirement {identity}")
        rows[identity] = cells
        if not re.fullmatch(r"P[0-7]", phase):
            errors.append(f"invalid phase {identity}: {phase}")
            continue
        if f"### {phase} —" not in content:
            errors.append(f"missing phase definition {phase}")
        if not migration.startswith(f"M{phase[1]}:") or gate != f"RG{phase[1]}":
            errors.append(f"unbound migration/release gate {identity}")
        if len(acceptance) < 45 or len(migration) < 35 or "§" not in requirement:
            errors.append(f"incomplete acceptance/source/migration {identity}")
    if set(rows) != EXPECTED:
        errors.append(f"requirement set mismatch: {sorted(EXPECTED ^ set(rows))}")
    graph = {}
    for identity, cells in rows.items():
        phase = cells[2]
        graph[identity] = re.findall(r"R\d\dF?", cells[3])
        for dependency in graph[identity]:
            if dependency not in rows:
                errors.append(f"unknown dependency {identity}: {dependency}")
            elif rows[dependency][2] > phase:
                errors.append(f"future-phase dependency {identity}: {dependency}")
        for dependency in re.findall(r"P[0-7]", cells[3]):
            if dependency >= phase:
                errors.append(f"invalid phase prerequisite {identity}: {dependency}")
    visited = set()

    def visit(identity: str, active: set[str]) -> None:
        if identity in active:
            errors.append(f"cyclic requirement dependency {identity}")
            return
        if identity in visited:
            return
        for dependency in graph.get(identity, []):
            visit(dependency, active | {identity})
        visited.add(identity)

    for identity in graph:
        visit(identity, set())
    return errors


def gate_errors(content: str) -> list[str]:
    errors = []
    rows = {}
    for line in content.splitlines():
        if re.match(r"\| R\d\dF? \|", line):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) == 7:
                rows[cells[0]] = cells[2]
    phases = re.findall(r"### (P[0-7]) —(.*?)(?=### P[0-7] —|## 6\.)", content, re.S)
    if len(phases) != 8:
        errors.append("missing/duplicate phase gate")
    for phase, section in phases:
        gate = re.search(r"\*\*Gate RG[0-7]:\*\*([^\n]+)", section)
        if not gate or not gate[0].startswith(f"**Gate RG{phase[1]}:**") or "all matrix rows assigned to " + phase not in gate[1]:
            errors.append(f"gate must bind exactly its matrix rows: {phase}")
        if gate:
            referenced = re.findall(r"R\d\dF?", gate[1])
            for first, last in re.findall(r"R(\d\d)[–-]R?(\d\d)", gate[1]):
                referenced.extend(f"R{number:02d}" for number in range(int(first), int(last) + 1))
            for identity in referenced:
                if identity not in rows or rows[identity] > phase:
                    errors.append(f"forward gate dependency {phase}: {identity}")
        dependencies = re.search(r"\*\*Dependencies:\*\*([^\n]+)", section)
        if not dependencies:
            errors.append(f"missing phase prerequisites {phase}")
        else:
            declared = dependencies[1].split("**Delivers:**")[0]
            prerequisites = re.findall(r"\bP[0-7]\b", declared) + ["P" + number for number in re.findall(r"\bRG([0-7])\b", declared)]
            for dependency in prerequisites:
                if dependency >= phase:
                    errors.append(f"cyclic/forward phase prerequisite {phase}: {dependency}")
    return errors


def markdown_anchors(content: str) -> set[str]:
    anchors = set(re.findall(r'<a\s+id="([^"]+)"', content))
    counts: Counter[str] = Counter()
    fenced = False
    for line in content.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if fenced or not re.match(r"^#{1,6} ", line):
            continue
        heading = re.sub(r"^#+\s+", "", line).strip().lower()
        heading = re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")
        suffix = f"-{counts[heading]}" if counts[heading] else ""
        anchors.add(heading + suffix)
        counts[heading] += 1
    return anchors


def markdown_structure_errors(path: Path, content: str) -> list[str]:
    errors = []
    fence = None
    table_width = None
    for number, line in enumerate(content.splitlines(), 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        if "\ufffd" in line or "\x00" in line:
            errors.append(f"damaged text {path}:{number}")
        if re.match(r"^#{1,6}[^#\s]", line):
            errors.append(f"malformed heading {path}:{number}")
        if line.startswith("|") and line.endswith("|"):
            width = len(re.split(r"(?<!\\)\|", line))
            if table_width is not None and width != table_width:
                errors.append(f"table width mismatch {path}:{number}")
            table_width = width
        else:
            table_width = None
    if fence:
        errors.append(f"unclosed code fence {path}")
    return errors


def link_errors(path: Path, content: str, root: Path) -> list[str]:
    errors = []
    plain = re.sub(r"```.*?```", "", content, flags=re.S)
    targets = re.findall(r"\[[^\]\n]*\]\((<[^>]+>|[^)\n]+)\)", plain)
    targets += re.findall(r"^\[[^\]]+\]:\s*(\S+)", plain, flags=re.M)
    for value in targets:
        target = value[1:-1] if value.startswith("<") else value.split(' "')[0]
        if urlsplit(target).scheme or target.startswith("//"):
            continue
        target = unquote(target)
        pathname, separator, fragment = target.partition("#")
        resolved = (root / pathname.lstrip("/")) if pathname.startswith("/") else (path.parent / pathname if pathname else path)
        if not resolved.exists():
            errors.append(f"broken link {path.relative_to(root)}: {target}")
        elif separator and fragment and resolved.suffix == ".md":
            if fragment not in markdown_anchors(resolved.read_text()):
                errors.append(f"broken anchor {path.relative_to(root)}: {target}")
    return errors


def source_errors(content: str, coverage: dict, root: Path) -> list[str]:
    errors = []
    for source in coverage["sources"]:
        path = root / source["path"]
        raw = path.read_bytes()
        if digest(raw) != source["sha256"]:
            errors.append(f"source intent drift: {source['path']}")
        embedded = content.split(source["begin"], 1)[-1].split(source["end"], 1)[0].strip()
        expected = raw.decode().strip()
        if source["path"].endswith(".mermaid"):
            expected = "```mermaid\n" + expected + "\n```"
        if embedded != expected:
            errors.append(f"incorporated source differs: {source['path']}")
        actual_lines = {number for number, line in enumerate(raw.decode().splitlines(), 1) if line.strip()}
        mapped = set()
        for block in source["coverage"]:
            if not block["requirements"] or set(block["requirements"]) - EXPECTED:
                errors.append(f"invalid source mapping: {source['path']}")
            mapped.update(range(block["start"], block["end"] + 1))
        if actual_lines - mapped:
            errors.append(f"unmapped source lines: {source['path']}: {sorted(actual_lines - mapped)}")
    return errors


def hash_errors(records: list[dict], root: Path, location_key: str = "path") -> list[str]:
    errors = []
    for record in records:
        path = root / record[location_key]
        data = path.read_bytes() if path.is_file() else b""
        if record.get("encoding") == "base64":
            try:
                data = base64.b64decode(data, validate=False)
            except ValueError:
                data = b""
        if not path.is_file() or digest(data) != record["sha256"]:
            errors.append(f"missing/changed evidence: {record[location_key]}")
    return errors


def historical_navigation_errors(records: list[dict], root: Path) -> list[str]:
    errors = []
    for record in records:
        if record["path"] == record["location"]:
            continue
        archive = root / record["location"]
        if not archive.is_file():
            continue
        raw = archive.read_bytes()
        if record.get("encoding") == "base64":
            raw = base64.b64decode(raw)
        missing = markdown_anchors(raw.decode()) - markdown_anchors((root / record["path"]).read_text())
        if missing:
            errors.append(f"lost historical anchors: {record['path']}: {sorted(missing)}")
    return errors


class HtmlLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        self.targets.extend(value for key, value in attributes if key in {"href", "src"} and value and not urlsplit(value).scheme and not value.startswith(("#", "//")))


def html_link_errors(path: Path, root: Path) -> list[str]:
    parser = HtmlLinks()
    parser.feed(path.read_text())
    errors = []
    for target in parser.targets:
        pathname = unquote(target.split("#")[0].split("?")[0])
        resolved = root / pathname.lstrip("/") if pathname.startswith("/") else path.parent / pathname
        if pathname and not resolved.exists():
            errors.append(f"broken HTML link {path.relative_to(root)}: {target}")
    return errors


def release_errors(content: str, release: dict) -> list[str]:
    errors = []
    p0_requirements = {line.split("|")[1].strip() for line in content.splitlines() if re.match(r"\| R\d\dF? \|", line) and line.split("|")[3].strip() == "P0"}
    requirements = release.get("requirements", [])
    if not isinstance(requirements, list) or Counter(requirements) != Counter(p0_requirements):
        errors.append("P0 release ledger does not cover its mapped obligations")
    if release.get("phase") != "P0" or release.get("release_gate") != "RG0":
        errors.append("invalid release ledger identity")
    if release.get("production_effects_enabled") is not False:
        errors.append("P0 must not enable production effects")
    if release.get("release_status") not in {"PASS", "HELD", "FAIL"}:
        errors.append("invalid release status")
    checks = release.get("external_checks")
    errors += qualification_errors(checks, EXTERNAL_CHECKS)
    if release.get("release_status") == "PASS" and (not isinstance(checks, list) or not checks or any(not isinstance(check, dict) or check.get("status") != "PASS" for check in checks)):
        errors.append("unverified external qualification cannot pass RG0")
    local = release.get("local_qualification")
    if local not in {"PASS", "FAIL", "NOT RUN"}:
        errors.append("invalid local qualification status")
    if release.get("release_status") == "PASS" and local != "PASS":
        errors.append("unsuccessful local qualification cannot pass RG0")
    verification = release.get("verification")
    if not isinstance(verification, dict) or not verification:
        errors.append("missing local verification")
    elif local == "PASS":
        for name in ["focused_p0", "full_non_live", "documentation_tests"]:
            result = verification.get(name, {})
            if not isinstance(result, dict) or type(result.get("passed")) is not int or result["passed"] <= 0 or any(result.get(field, 0) != 0 for field in ["failed", "skipped", "errors", "exit_code"]):
                errors.append(f"unsuccessful local suite: {name}")
        diagrams = verification.get("mermaid_diagrams", {})
        if not isinstance(diagrams, dict) or diagrams.get("rendered") != 6 or diagrams.get("failed") != 0:
            errors.append("missing successful diagram qualification")
    artifacts = release.get("evidence_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("missing qualification evidence")
    else:
        if all(artifact.get("local_only", False) for artifact in artifacts if isinstance(artifact, dict)):
            errors.append("qualification requires durable repository evidence")
        for artifact in artifacts:
            if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str) or not artifact["path"] or not re.fullmatch(r"[a-f0-9]{64}", str(artifact.get("sha256", ""))):
                errors.append("invalid qualification evidence")
            elif Path(artifact["path"]).is_absolute() or ".." in Path(artifact["path"]).parts:
                errors.append("qualification evidence must be repository-relative")
            elif not artifact.get("local_only", False) and (not artifact["path"].startswith("docs/v4/") or not artifact["path"].endswith("-evidence.json")):
                errors.append("durable qualification must use a repository evidence report")
    if "development_entry" in release:
        entry = release["development_entry"]
        if not isinstance(entry, dict):
            errors.append("invalid development entry")
        else:
            errors += qualification_errors(entry.get("checks"), DEVELOPMENT_CHECKS)
            if entry.get("status") not in {"PASS", "HELD", "FAIL"}:
                errors.append("invalid development entry status")
            development_checks = entry.get("checks")
            if entry.get("status") == "PASS" and (local != "PASS" or not isinstance(development_checks, list) or any(not isinstance(check, dict) or check.get("status") != "PASS" for check in development_checks)):
                errors.append("unqualified local development entry")
    return errors


def qualification_errors(checks, required: set[str]) -> list[str]:
    if not isinstance(checks, list) or any(not isinstance(check, dict) for check in checks):
        return ["missing or invalid qualification checklist"]
    errors = []
    if Counter(str(check.get("check")) for check in checks) != Counter(required):
        errors.append("mandatory qualification checks must each appear exactly once")
    for check in checks:
        if check.get("status") not in {"PASS", "FAIL", "NOT RUN"}:
            errors.append("invalid qualification status")
        field = "evidence" if check.get("status") == "PASS" else "reason"
        if not isinstance(check.get(field), str) or not check[field].strip():
            errors.append(f"qualification requires {field}: {check.get('check')}")
    return errors


def evidence_report_errors(report) -> list[str]:
    if not isinstance(report, dict) or not isinstance(report.get("commands"), list) or not report["commands"]:
        return ["missing durable command evidence"]
    errors = []
    for command in report["commands"]:
        if not isinstance(command, dict) or not command.get("command") or type(command.get("tests")) is not int or command["tests"] <= 0 or any(type(command.get(field)) is not int or command[field] != 0 for field in ["exit_code", "failures", "errors", "skipped"]):
            errors.append("unsuccessful or incomplete durable command evidence")
        elif not re.fullmatch(r"[a-f0-9]{64}", str(command.get("junit_sha256", ""))):
            errors.append("missing durable execution evidence digest")
    return errors


def verify(root: Path, draft: bool = False) -> tuple[list[str], dict]:
    content = (root / BLUEPRINT).read_text()
    coverage = json.loads((root / "docs/v4/coverage.json").read_text())
    evidence = json.loads((root / "docs/v4/evidence-lock.json").read_text())
    release = json.loads((root / "docs/v4/p0-release.json").read_text())
    inventory = json.loads((root / "docs/v4/inventory.json").read_text())
    errors = requirement_errors(content) + gate_errors(content) + source_errors(content, coverage, root)
    errors += release_errors(content, release)
    for record in release.get("evidence_artifacts", []):
        if not (root / record["path"]).resolve().is_relative_to(root.resolve()):
            errors.append("qualification evidence resolves outside repository")
        elif not record.get("local_only", False) and (root / record["path"]).is_file():
            try:
                errors += evidence_report_errors(json.loads((root / record["path"]).read_text()))
            except (ValueError, UnicodeError):
                errors.append("invalid durable evidence JSON")
    errors += hash_errors([record for record in release.get("evidence_artifacts", []) if not record.get("local_only") or (root / record["path"]).exists()], root)
    errors += hash_errors(evidence["files"], root)
    expected_paths = {record["path"] for record in evidence["files"]}
    actual_paths = {path for path in expected_paths if (root / path).is_file()}
    for directory in ["src", "tests", "migrations", "scripts"]:
        actual_paths.update(str(path.relative_to(root)) for path in (root / directory).rglob("*") if path.is_file() and "__pycache__" not in path.parts and not any(part.endswith(".egg-info") for part in path.parts) and path.suffix != ".pyc")
    if actual_paths != expected_paths:
        errors.append(f"application evidence file set changed: {sorted(actual_paths ^ expected_paths)}")
    if draft:
        errors += hash_errors(inventory["files"], root)
        documents = [root / BLUEPRINT, root / "docs/v4/validation.md"]
    else:
        dispositions = json.loads((root / "docs/v4/dispositions.json").read_text())
        paths = [record["path"] for record in dispositions["files"]]
        if len(set(paths)) != len(paths) or set(paths) != {record["path"] for record in inventory["files"]}:
            errors.append("inventory disposition is not one-to-one")
        original = {record["path"]: record for record in inventory["files"]}
        for record in dispositions["files"]:
            if record["sha256"] != original[record["path"]]["sha256"]:
                errors.append(f"archive hash does not match inventory: {record['path']}")
            if original[record["path"]]["category"] == "instruction" and record["location"] != record["path"]:
                errors.append(f"instruction moved: {record['path']}")
        durable = [record for record in dispositions["files"] if not record["path"].startswith((".worktrees/", "artifacts/")) and not record.get("local_only", False)]
        errors += hash_errors(durable, root, "location")
        errors += historical_navigation_errors(dispositions["files"], root)
        local = [record for record in dispositions["files"] if record not in durable]
        errors += hash_errors([record for record in local if (root / record["location"]).exists()], root, "location")
        documents = [root / "README.md"]
        for folder in ["docs", "docs-new-arch", "prompts", ".agents"]:
            documents.extend((root / folder).rglob("*.md"))
    for path in documents:
        errors += link_errors(path, path.read_text(), root)
        errors += markdown_structure_errors(path.relative_to(root), path.read_text())
    html_documents = list((root / "docs").glob("*.html"))
    for path in html_documents:
        errors += html_link_errors(path, root)
    return errors, {"requirements": len(EXPECTED), "source_files": len(coverage["sources"]), "evidence_files": len(evidence["files"]), "inventory_files": len(inventory["files"]), "markdown_files_checked": len(documents), "html_files_checked": len(html_documents), "mode": "draft" if draft else "consolidated"}


def self_test(root: Path) -> list[str]:
    import tempfile

    failures = []
    content = (root / BLUEPRINT).read_text()
    removed = "\n".join(line for line in content.splitlines() if not line.startswith("| R01 |"))
    if not requirement_errors(removed):
        failures.append("self-test failed to detect missing requirement")
    broken = content.replace("| P1 | R03,R05 |", "| P1 | R64 |", 1)
    if not requirement_errors(broken):
        failures.append("self-test failed to detect future dependency")
    coverage = json.loads((root / "docs/v4/coverage.json").read_text())
    if not source_errors(content.replace("Temporal and the Salience workers execute cycles", "Tampered source", 1), coverage, root):
        failures.append("self-test failed to detect altered source")
    with tempfile.TemporaryDirectory(prefix="salience-doc-check-") as directory:
        temporary = Path(directory)
        sample = temporary / "sample.md"
        sample.write_text("[missing](missing.md)\n[anchor](#absent)\n")
        if len(link_errors(sample, sample.read_text(), temporary)) != 2:
            failures.append("self-test failed to detect broken link/anchor")
        if not hash_errors([{"path": "sample.md", "sha256": "0" * 64}], temporary):
            failures.append("self-test failed to detect evidence/archive corruption")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    arguments = parser.parse_args()
    errors, summary = verify(ROOT, arguments.draft)
    if arguments.self_test:
        errors += self_test(ROOT)
        summary["negative_controls"] = 6
    print(json.dumps({"status": "FAIL" if errors else "PASS", **summary, "errors": errors}, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
