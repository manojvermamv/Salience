"""Emit reversible documentation patches from the pre-consolidation inventory."""

from pathlib import Path
import base64
import hashlib
import json
import os
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = json.loads((ROOT / "docs/v4/inventory.json").read_text())
SELECTED = [
    row for row in INVENTORY["files"]
    if (row["path"] == "README.md" or row["path"].startswith(("docs/", "prompts/")))
    and row["path"].endswith(".md")
]
EXTRAS = {
    "docs/api.md": "Existing `CreativeProductionRequest@v1` and private publication contracts are summarized in the blueprint evidence table; production subject/account authorization is still required.",
    "docs/architecture.md": "`CreativeService` and `YouTubePublisherAdapter` are reusable boundaries. Current configured deployment has no live social publishing; see blueprint E06–E09.",
    "docs/database.md": "Historical migration `0009_governed_publication` is followed by hardening through 0012, including triggers that reject direct mutation of approved decisions. See blueprint E01 and E07.",
    "docs/dependencies.md": "Live provider capabilities are disabled by default. Current pins remain in `pyproject.toml`; fresh production license/advisory review is required by P0.",
    "docs/deployment.md": "Run `bash scripts/verify-browser-evidence.sh` only with an externally provisioned Playwright environment. Use the blueprint startup/restore runbooks; current Compose is a fixture topology.",
    "docs/verification.md": "Current evidence is in [V4 validation](v4/validation.md): 226 passed, six missing-browser failures; browser verification is NOT RUN. Historical `bash scripts/verify-phases-7-8.sh` covers durable reservation/actual settlement and verified webhook deduplication. `bash scripts/verify-browser-evidence.sh` requires external provisioning and retains output under `artifacts/browser-evidence/`.",
    "docs/limitations.md": "The current YouTube boundary does not publish a video. Fixtures cover explicit budget reservations and actual-cost settlement. External research remains `untrusted_external`; production readiness limitations are in blueprint E01–E17 and C01–C20.",
    "docs/research.md": "Browser text remains `untrusted_external`; retained local evidence belongs under `artifacts/browser-evidence/`. The blueprint specifies source quality, permissions, freshness and citation requirements.",
    "docs/phase-9-handoff.md": "The historical handoff named `ReadyToPublishPackage@v1` the only valid Phase-9 publishing input. It is an artifact input, never publication authority; V4 additionally requires the current account-bound request and G3 permit.",
    "docs/adr/0006-governed-publishing.md": "The historical decision selected an official YouTube Data API private-session boundary. Its original evidence is archived; complete production transfer/readback qualification is planned in V4 P2.",
    "docs/implementation-progress.md": "The authoritative execution sequence is now blueprint P0–P7. Documentation transition progress is in [V4 progress](v4/progress.md); no V4 application implementation is claimed.",
}
README = """# Salience

Salience is a self-hostable, provider-neutral content operating system built around PostgreSQL, Temporal, typed agents, governed artifacts and replaceable adapters.

**Start with the [V4 production implementation blueprint](docs/v4/IMPLEMENTATION-BLUEPRINT.md).** It is the single source of truth for target architecture, verified starting state, implementation phases, requirement mappings, migration/rollback and release gates. V4 is a plan, not a production-completion claim.

The existing Phase 1–9 foundation provides fixture-tested research, creative and governed publication workflows. Live media publishing, observation, learning, experiments and V4 admission/release controls still require the work identified in the blueprint. Approved creative changes produce a new distribution and ready-package version.

Current evidence: 226 non-live tests passed; six browser tests failed because Playwright Chromium is absent. See [verification details](docs/v4/validation.md). Historical all-green results are retained with their original dates.

```bash
python3 docs/v4/check.py --self-test
```

This documentation gate verifies source intent, 70 mapped requirements, evidence drift, preserved archives and links. The documentation CI workflow runs it on pushes and pull requests.

Existing fixture verification commands remain available:

```bash
bash scripts/verify-phase-9.sh
bash scripts/verify-phases-7-8.sh
bash scripts/verify-browser-evidence.sh
```

The browser verifier requires externally provisioned Playwright Chromium and reports NOT RUN when unavailable. The current Compose configuration is for local fixtures; use the blueprint's production qualification and startup instructions before enabling real accounts.

- [Inventory and preservation record](docs/v4/inventory.json)
- [Documentation transition checkpoint](docs/v4/progress.md)
- [Historical archive and restoration instructions](docs/archive/2026-09-22/README.md)
- [Original V4 design sources](docs-new-arch/README.md)
- [Historical Phase 1–9 interactive architecture](docs/salience-phase-1-9.architecture.html)

Historical diagrams describe prior implementation boundaries; the blueprint contains the complete V4 target overview and focused workflows.
"""


def original_anchors(content: str) -> list[str]:
    counts = {}
    anchors = []
    fenced = False
    for line in content.splitlines():
        if line.startswith("```"):
            fenced = not fenced
        if fenced or not re.match(r"^#{1,6} ", line):
            continue
        heading = re.sub(r"^#+\s+", "", line).strip().lower()
        heading = re.sub(r"[^\w\- ]", "", heading).replace(" ", "-")
        occurrence = counts.get(heading, 0)
        counts[heading] = occurrence + 1
        anchors.append(heading + (f"-{occurrence}" if occurrence else ""))
    return anchors


def add(path: str, content: str) -> None:
    print("*** Add File: " + path)
    for line in content.splitlines():
        print("+" + line)


def main() -> None:
    start, end = map(int, sys.argv[1:3])
    print("*** Begin Patch")
    for row in SELECTED[start:end]:
        path = Path(row["path"])
        raw = (ROOT / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise RuntimeError("changed original " + str(path))
        original = raw.decode()
        archive = "docs/archive/2026-09-22/" + str(path) + ".snapshot"
        if raw.endswith(b"\n"):
            stored = original
        else:
            archive += ".b64"
            stored = base64.b64encode(raw).decode() + "\n"
        add(archive, stored)
        if str(path) == "README.md":
            replacement = README
        else:
            target = os.path.relpath("docs/v4/IMPLEMENTATION-BLUEPRINT.md", path.parent)
            archived = os.path.relpath(archive, path.parent)
            title = next((line.lstrip("# ").strip() for line in original.splitlines() if line.startswith("# ")), path.stem)
            replacement = f"# {title}\n\nSuperseded by the [V4 production implementation blueprint](<{target}>). Use that document for all future work; this page preserves navigation only.\n\n[Original historical evidence](<{archived}>) is retained with its inventory hash. "
            replacement += "This snapshot is base64-encoded to preserve its original missing final newline; decode using the archive instructions." if archive.endswith(".b64") else "It is superseded source text, not a current execution plan."
            replacement += "\n"
            if str(path) in EXTRAS:
                replacement += "\n" + EXTRAS[str(path)] + "\n"
        anchors = original_anchors(original)
        if anchors:
            replacement += "\n<!-- Historical heading anchors retained for incoming links. -->\n"
            replacement += "\n".join('<a id="' + anchor + '"></a>' for anchor in anchors) + "\n"
        print("*** Update File: " + str(path))
        print("@@")
        for line in original.splitlines():
            print("-" + line)
        for line in replacement.splitlines():
            print("+" + line)
    print("*** End Patch")


if __name__ == "__main__":
    main()
