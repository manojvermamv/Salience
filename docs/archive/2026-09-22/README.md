# Historical documentation archive — 2026-09-22

These snapshots preserve original bytes from the pre-consolidation inventory. They are historical text, not current instructions or deployment claims. The [V4 blueprint](../../v4/IMPLEMENTATION-BLUEPRINT.md) contains the actionable intent and operational path; no archived plan is required to execute it.

The [disposition manifest](../../v4/dispositions.json) maps all 360 inventory paths, including retained instructions, source diagrams and local/worktree evidence. Exactly 62 superseded Markdown documents were archived. Former paths forward to the blueprint and retain historical heading anchors. No AGENTS.md or SKILL.md instruction was changed.

## Restoration

For a normal `.snapshot`, restore its bytes to the manifest's original `path`. Five originals lacked a final newline; their `.snapshot.b64` files store the exact base64-encoded original bytes. Decode these with `base64 --decode` before restoration. Verify SHA-256 against the manifest and inventory, then restore through a normal documentation commit. Do not reset or rewrite Git history. `python3 docs/v4/check.py` checks decoded bytes as well as ordinary snapshots.

Snapshots intentionally retain original examples and links as evidence text; `.snapshot` is not active Markdown navigation. Current navigation is in the forwarding pages, this index and the blueprint. Historical heading bookmarks resolve through the forwarding pages.

## Preservation and incorporation

| Original group | Preserved knowledge | Active blueprint location |
| --- | --- | --- |
| Master README and operator guides | Commands, configuration, deployment caveats, schemas, interfaces, historical claims | Sections 1, 4 and 7 verify/correct present facts and define startup/restore procedures |
| Core product pack | Niche-first product, Lead/callable agents, memory, packaging, creative capabilities, reusable infrastructure, engineering method | Sections 3–6 retain the intent and implementation requirements |
| ADRs and compatibility/dependency guides | Original choices, rejected alternatives, licenses, protocol revisions, transport workaround, exit paths | Section 3 preserves boundaries and mandatory fresh qualification; original dated evidence retained here |
| Superpowers plans/specs/reviews and progress | Decisions, test results, failed attempts, independent-review limitations and chronology | Sections 1/7/8 distinguish executable baseline from historical reports; snapshots retain full history |
| Prior phase prompts including Phase 10 | Provider-neutral publishing, analytics semantics, experiments, callable analyst and learning handoff | Sections 3/5/6 retain unique requirements under V4 sequencing |
| Browser setup direction | Original provisioning request and later externally provisioned behavior | E17 and C13/C20 preserve the later source behavior and fresh environment limitation |

The original worker-side Psycopg choice followed a reported asyncpg/Python 3.13 activity crash; this historical rationale is preserved in ADR 0003, while actual dependencies remain verified in code. Historical provider/license/advisory statements are dated evidence and require fresh review before production. The engineering discipline is Karpathy-inspired; the exact Spec–Verifier–Environment phrase is not presented as a verified quotation.

## Original documents

<!-- ARCHIVE INDEX -->

| Original path | Preserved snapshot |
| --- | --- |
| `README.md` | [snapshot](<README.md.snapshot>) |
| `docs/adr/0001-durable-runtime.md` | [snapshot](<docs/adr/0001-durable-runtime.md.snapshot>) |
| `docs/adr/0002-object-storage.md` | [snapshot](<docs/adr/0002-object-storage.md.snapshot>) |
| `docs/adr/0003-api-persistence-and-contracts.md` | [snapshot](<docs/adr/0003-api-persistence-and-contracts.md.snapshot>) |
| `docs/adr/0004-governance-secrets-and-protocols.md` | [snapshot](<docs/adr/0004-governance-secrets-and-protocols.md.snapshot>) |
| `docs/adr/0005-mcp-a2a-and-browser-sdk-adoption.md` | [snapshot](<docs/adr/0005-mcp-a2a-and-browser-sdk-adoption.md.snapshot>) |
| `docs/adr/0006-governed-publishing.md` | [snapshot](<docs/adr/0006-governed-publishing.md.snapshot>) |
| `docs/agents.md` | [snapshot](<docs/agents.md.snapshot>) |
| `docs/api.md` | [snapshot](<docs/api.md.snapshot>) |
| `docs/architecture.md` | [snapshot](<docs/architecture.md.snapshot>) |
| `docs/bootstrap.md` | [snapshot](<docs/bootstrap.md.snapshot>) |
| `docs/compatibility.md` | [snapshot](<docs/compatibility.md.snapshot>) |
| `docs/contracts/adapter-contracts.md` | [snapshot](<docs/contracts/adapter-contracts.md.snapshot>) |
| `docs/core/00_BUILD_AGENT_PROMPT.md` | [snapshot](<docs/core/00_BUILD_AGENT_PROMPT.md.snapshot>) |
| `docs/core/01_PRODUCT_CONTEXT_AND_INTENT.md` | [snapshot](<docs/core/01_PRODUCT_CONTEXT_AND_INTENT.md.snapshot>) |
| `docs/core/02_REFERENCE_RESEARCH.md` | [snapshot](<docs/core/02_REFERENCE_RESEARCH.md.snapshot>) |
| `docs/core/03_TARGET_ARCHITECTURE.md` | [snapshot](<docs/core/03_TARGET_ARCHITECTURE.md.snapshot>) |
| `docs/core/04_BROWSER_APP_AND_RESEARCH_CONTROL.md` | [snapshot](<docs/core/04_BROWSER_APP_AND_RESEARCH_CONTROL.md.snapshot>) |
| `docs/core/05_IMPLEMENTATION_PLAN.md` | [snapshot](<docs/core/05_IMPLEMENTATION_PLAN.md.snapshot>) |
| `docs/core/06_ACCEPTANCE_TESTS_AND_GUARDRAILS.md` | [snapshot](<docs/core/06_ACCEPTANCE_TESTS_AND_GUARDRAILS.md.snapshot>) |
| `docs/core/07_REUSE_EXTENSION_AND_FUTURE_PROOFING.md` | [snapshot](<docs/core/07_REUSE_EXTENSION_AND_FUTURE_PROOFING.md.snapshot>) |
| `docs/core/08_LEAD_AGENT_AND_CREATIVE_TOOL_RUNTIME.md` | [snapshot](<docs/core/08_LEAD_AGENT_AND_CREATIVE_TOOL_RUNTIME.md.snapshot>) |
| `docs/core/09_CALLABLE_AGENTS_AND_ENGINEERING_METHOD.md` | [snapshot](<docs/core/09_CALLABLE_AGENTS_AND_ENGINEERING_METHOD.md.snapshot>) |
| `docs/core/README.md` | [snapshot](<docs/core/README.md.snapshot>) |
| `docs/database.md` | [snapshot](<docs/database.md.snapshot>) |
| `docs/dependencies.md` | [snapshot](<docs/dependencies.md.snapshot>) |
| `docs/deployment.md` | [snapshot](<docs/deployment.md.snapshot>) |
| `docs/governance.md` | [snapshot](<docs/governance.md.snapshot>) |
| `docs/implementation-progress.md` | [snapshot](<docs/implementation-progress.md.snapshot>) |
| `docs/limitations.md` | [snapshot](<docs/limitations.md.snapshot>) |
| `docs/local-development.md` | [snapshot](<docs/local-development.md.snapshot>) |
| `docs/mcp-a2a.md` | [snapshot](<docs/mcp-a2a.md.snapshot>) |
| `docs/memory.md` | [snapshot](<docs/memory.md.snapshot>) |
| `docs/model-execution.md` | [snapshot](<docs/model-execution.md.snapshot>) |
| `docs/model-gateway.md` | [snapshot](<docs/model-gateway.md.snapshot>) |
| `docs/phase-7-handoff.md` | [snapshot](<docs/phase-7-handoff.md.snapshot>) |
| `docs/phase-9-handoff.md` | [snapshot](<docs/phase-9-handoff.md.snapshot>) |
| `docs/plugins.md` | [snapshot](<docs/plugins.md.snapshot>) |
| `docs/recovery.md` | [snapshot](<docs/recovery.md.snapshot>) |
| `docs/research.md` | [snapshot](<docs/research.md.snapshot>) |
| `docs/superpowers/plans/2026-09-12-browser-evidence.md` | [snapshot](<docs/superpowers/plans/2026-09-12-browser-evidence.md.snapshot>) |
| `docs/superpowers/plans/2026-09-12-intelligence-loop-phases-5-6.md` | [snapshot](<docs/superpowers/plans/2026-09-12-intelligence-loop-phases-5-6.md.snapshot>) |
| `docs/superpowers/plans/2026-09-12-phases-1-4-foundation.md` | [snapshot](<docs/superpowers/plans/2026-09-12-phases-1-4-foundation.md.snapshot>) |
| `docs/superpowers/plans/2026-09-13-creative-production-phases-7-8.md` | [snapshot](<docs/superpowers/plans/2026-09-13-creative-production-phases-7-8.md.snapshot>) |
| `docs/superpowers/plans/2026-09-13-phase-7-8-release-gate-phase-9-publishing.md` | [snapshot](<docs/superpowers/plans/2026-09-13-phase-7-8-release-gate-phase-9-publishing.md.snapshot>) |
| `docs/superpowers/plans/2026-09-13-phase-7-8-review-repairs.md` | [snapshot](<docs/superpowers/plans/2026-09-13-phase-7-8-review-repairs.md.snapshot>) |
| `docs/superpowers/plans/2026-09-13-phase-9-independent-review-repairs.md` | [snapshot](<docs/superpowers/plans/2026-09-13-phase-9-independent-review-repairs.md.snapshot>) |
| `docs/superpowers/reviews/2026-09-13-phase-7-8-release-gate.md` | [snapshot](<docs/superpowers/reviews/2026-09-13-phase-7-8-release-gate.md.snapshot>) |
| `docs/superpowers/reviews/2026-09-13-phase-9-independent-review.md` | [snapshot](<docs/superpowers/reviews/2026-09-13-phase-9-independent-review.md.snapshot>) |
| `docs/superpowers/specs/2026-09-12-browser-evidence-design.md` | [snapshot](<docs/superpowers/specs/2026-09-12-browser-evidence-design.md.snapshot>) |
| `docs/superpowers/specs/2026-09-12-intelligence-loop-phases-5-6-design.md` | [snapshot](<docs/superpowers/specs/2026-09-12-intelligence-loop-phases-5-6-design.md.snapshot>) |
| `docs/superpowers/specs/2026-09-13-creative-production-phases-7-8-design.md` | [snapshot](<docs/superpowers/specs/2026-09-13-creative-production-phases-7-8-design.md.snapshot>) |
| `docs/superpowers/specs/2026-09-13-phase-7-8-release-gate-phase-9-publishing-design.md` | [snapshot](<docs/superpowers/specs/2026-09-13-phase-7-8-release-gate-phase-9-publishing-design.md.snapshot>) |
| `docs/trust-model.md` | [snapshot](<docs/trust-model.md.snapshot>) |
| `docs/verification.md` | [snapshot](<docs/verification.md.snapshot>) |
| `docs/workflows.md` | [snapshot](<docs/workflows.md.snapshot>) |
| `prompts/Build Creative Production Loop — Phases 7–8 End-to-End.md` | [snapshot](<prompts/Build Creative Production Loop — Phases 7–8 End-to-End.md.snapshot.b64>) |
| `prompts/Build Phase 10 — Multi-Platform Production Publishing, Analytics, Attribution & Experiments.md` | [snapshot](<prompts/Build Phase 10 — Multi-Platform Production Publishing, Analytics, Attribution & Experiments.md.snapshot.b64>) |
| `prompts/Build Phases 1–4 End-to-End.md` | [snapshot](<prompts/Build Phases 1–4 End-to-End.md.snapshot>) |
| `prompts/Build Real Intelligence Loop — Pre-Phase 5 + Phases 5–6 End-to-End.md` | [snapshot](<prompts/Build Real Intelligence Loop — Pre-Phase 5 + Phases 5–6 End-to-End.md.snapshot.b64>) |
| `prompts/Complete Phase 7–8 Release Gate, Then Build Phase 9 Governed Publishing.md` | [snapshot](<prompts/Complete Phase 7–8 Release Gate, Then Build Phase 9 Governed Publishing.md.snapshot.b64>) |
| `prompts/Install and Verify Browser Evidence on Linux EC2.md` | [snapshot](<prompts/Install and Verify Browser Evidence on Linux EC2.md.snapshot.b64>) |
