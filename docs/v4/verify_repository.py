"""Read-only, commit-bound GitHub CI and merge-enforcement evidence."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from urllib.error import HTTPError
from urllib.request import Request, urlopen


REQUIRED = {"verify", "p0-regression"}
APP_ID = 15368


def enforcement_errors(protection):
    errors = []
    checks = protection.get("required_status_checks") or {}
    configured = {(item.get("context"), item.get("app_id")) for item in checks.get("checks", [])}
    if not checks.get("strict") or not {(name, APP_ID) for name in REQUIRED} <= configured:
        errors.append("missing strict app-bound required checks")
    if not protection.get("enforce_admins", {}).get("enabled"):
        errors.append("administrator bypass is enabled")
    reviews = protection.get("required_pull_request_reviews") or {}
    if reviews.get("required_approving_review_count", 0) < 1 or not reviews.get("dismiss_stale_reviews") or not reviews.get("require_last_push_approval"):
        errors.append("missing current independent pull-request approval")
    if reviews.get("bypass_pull_request_allowances") and any(reviews["bypass_pull_request_allowances"].values()):
        errors.append("pull-request bypass allowance exists")
    for flag in ["allow_force_pushes", "allow_deletions"]:
        if protection.get(flag, {}).get("enabled", True):
            errors.append(f"unsafe branch permission: {flag}")
    return errors


def ci_errors(checks, commit):
    errors = []
    for name in sorted(REQUIRED):
        matches = [item for item in checks if item.get("name") == name and item.get("app", {}).get("id") == APP_ID and item.get("head_sha") == commit]
        if not matches or max(matches, key=lambda item: item["id"]).get("conclusion") != "success" or max(matches, key=lambda item: item["id"]).get("status") != "completed":
            errors.append(f"current-commit CI not successful: {name}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    commit = subprocess.check_output(["git", "rev-parse", args.commit], text=True).strip()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        result = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True, check=True)
        token = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line).get("password")
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = "Bearer " + token
    responses = {}
    for name, path in {
        "protection": "/branches/main/protection",
        "rulesets": "/rulesets?includes_parents=true",
        "effective_rules": "/rules/branches/main",
        "checks": f"/commits/{commit}/check-runs?per_page=100",
    }.items():
        try:
            with urlopen(Request("https://api.github.com/repos/manojvermamv/Salience" + path, headers=headers), timeout=30) as response:
                responses[name] = json.load(response)
        except HTTPError as error:
            responses[name] = {"http_status": error.code}
    enforcement = enforcement_errors(responses["protection"])
    checks = responses["checks"].get("check_runs", [])
    ci = ci_errors(checks, commit)
    inspection = []
    for name in ["rulesets", "effective_rules"]:
        if not isinstance(responses[name], list):
            inspection.append(f"cannot inspect {name}")
    report = {"inspected_at": datetime.now(timezone.utc).isoformat(), "commit": commit, "enforcement_status": "FAIL" if enforcement or inspection else "PASS", "ci_status": "FAIL" if ci else "PASS", "merge_status": "HELD" if enforcement or inspection or ci else "CHECKS PASS; independent PR approval still required", "errors": enforcement + inspection + ci, "responses": responses}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "responses"}, indent=2))
    return int(bool(report["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
