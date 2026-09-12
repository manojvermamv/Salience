# Governed Browser Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install revision-matched Chromium and prove that the existing Salience browser adapter captures governed, traceable, untrusted evidence through a real headless session.

**Architecture:** Keep `PlaywrightBrowserResearchTool` as the sole browser adapter and extend its owned request/result boundary with safe execution lineage and evidence metadata. A project-owned setup/verification pair owns the optional dependency, browser runtime, disk-backed test evidence, and repeatable integration suite; the fixture grants private TLS access only explicitly and only for test process scope.

**Tech Stack:** Python 3.13, Playwright 1.62.0, bundled Chromium, pytest/pytest-asyncio, Python standard-library HTTPS fixture, existing Salience `ObjectStore`, `TrustPolicy`, agent execution, Bash.

**Spec:** `docs/superpowers/specs/2026-09-12-browser-evidence-design.md`

## Global Constraints

- Preserve `playwright==1.62.0`; install only Playwright Chromium and its documented Linux dependencies.
- Use `python -m playwright install-deps chromium` then `python -m playwright install chromium`; do not install Firefox, WebKit, or a distro Chromium package.
- Do not perform destructive Docker/system cleanup, delete caches, use paid APIs, or require credentials.
- Keep browser execution headless, isolated per request, read-only, HTTPS/domain allowlisted, bounded, and download-disabled.
- Deny loopback/private/link-local/multicast/unspecified/reserved IP literals unless a test-only explicit option permits the fixture.
- Keep browser text as untrusted artifact evidence; it must not gain authority or become verified memory.
- `bash scripts/verify-browser-evidence.sh [--install]` is the single operator/CI command and must preserve evidence output on failure.
- Update `docs/implementation-progress.md` and commit each verified task before beginning the next task.

---

## File Structure

- `src/salience/browser/contracts.py` — immutable browser request/result DTOs and safe failure type.
- `src/salience/browser/playwright.py` — one governed Playwright implementation and artifact metadata writer.
- `src/salience/research/http.py` — reusable literal-address network-scope validation.
- `src/salience/agents/execution.py` — stable agent-run ID before runtime invocation.
- `src/salience/agents/intelligence.py` — browser agent forwards owned lineage and keeps output untrusted.
- `tests/unit/test_browser_policy.py` — deterministic network-policy, limits, and lineage tests without a browser.
- `tests/integration/test_browser_evidence.py` — real TLS JavaScript/page/trace/screenshot security test fixture.
- `scripts/setup-browser-evidence.sh` — idempotent operator-owned virtualenv/dependency/browser installer.
- `scripts/verify-browser-evidence.sh` — prerequisite report, integration runner, and evidence summary.
- `README.md`, `docs/deployment.md`, `docs/research.md`, `docs/verification.md`, `docs/limitations.md`, `docs/implementation-progress.md` — supported workflow, evidence retention, capabilities, and verification checkpoint.

## Task 1: Owned Browser Contracts And Network Scope

**Files:**
- Modify: `src/salience/browser/contracts.py`
- Modify: `src/salience/research/http.py`
- Modify: `tests/unit/test_browser_policy.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `ObjectStore.put(key, data, content_type, metadata)` and `NetworkScopeDenied`.
- Produces: `BrowserResearchRequest(url, timeout_seconds, step_limit, max_response_bytes, agent_run_id, tool_run_id, trace_id)`, `BrowserResearchResult(..., browser_version, playwright_version, fetched_at, screenshot_hash, trace_hash)`, `BrowserEvidenceFailure`, and `assert_network_scope(url, allowed_domains, allow_private_network=False)`.

- [x] **Step 1: Write failing unit tests for validation and deterministic metadata DTOs**

```python
def test_browser_request_rejects_non_positive_limits() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        BrowserResearchRequest(url="https://allowed.test", timeout_seconds=0)

def test_network_scope_rejects_private_literal_even_when_allowlisted() -> None:
    with pytest.raises(NetworkScopeDenied, match="private"):
        assert_network_scope("https://127.0.0.1/page", frozenset({"127.0.0.1"}))
```

- [x] **Step 2: Run the focused unit test to verify red**

Run: `python -m pytest tests/unit/test_browser_policy.py -v`

Expected: FAIL because the request accepts invalid limits and `assert_network_scope` has no private-address policy argument.

- [x] **Step 3: Implement immutable DTO validation and literal-host policy**

```python
@dataclass(frozen=True)
class BrowserResearchRequest:
    url: str
    timeout_seconds: float = 30
    step_limit: int = 20
    max_response_bytes: int = 1_000_000
    agent_run_id: str | None = None
    tool_run_id: str | None = None
    trace_id: str | None = None

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.step_limit <= 0 or self.max_response_bytes <= 0:
            raise ValueError("browser limits must be positive")
```

Use `ipaddress.ip_address(hostname)` only when `hostname` is a literal. Deny `.is_loopback`, `.is_private`, `.is_link_local`, `.is_multicast`, `.is_unspecified`, and `.is_reserved` unless `allow_private_network=True`; always require HTTPS and an exact allowed hostname.

- [x] **Step 4: Add the `browser` pytest marker and run the focused test green**

Run: `python -m pytest tests/unit/test_browser_policy.py -v`

Expected: PASS without importing or launching Playwright.

- [x] **Step 5: Record the checkpoint and commit**

```bash
git add src/salience/browser/contracts.py src/salience/research/http.py tests/unit/test_browser_policy.py pyproject.toml docs/implementation-progress.md
git commit -m "feat: define governed browser evidence contracts"
```

## Task 2: Trace-Safe Playwright Evidence Adapter

**Files:**
- Modify: `src/salience/browser/playwright.py`
- Modify: `tests/unit/test_browser_policy.py`

**Interfaces:**
- Consumes: Task 1 DTOs and `assert_network_scope(..., allow_private_network=...)`.
- Produces: `PlaywrightBrowserResearchTool(object_store, allowed_domains, allow_private_network=False, ignore_https_errors=False)` that writes text, PNG, and ZIP receipts with provenance metadata and raises `BrowserEvidenceFailure` with non-secret diagnostics.

- [x] **Step 1: Write failing adapter tests with a fake Playwright module**

```python
async def test_browser_writes_complete_evidence_metadata(monkeypatch) -> None:
    store = MemoryObjectStore()
    tool = PlaywrightBrowserResearchTool(object_store=store, allowed_domains=frozenset({"allowed.test"}))
    result = await tool.read(BrowserResearchRequest(
        url="https://allowed.test/page", agent_run_id="agent-1", tool_run_id="tool-1", trace_id="trace-1"
    ))
    assert store.get(result.text_artifact_key).metadata["agent_run_id"] == "agent-1"
    assert result.screenshot_hash and result.trace_hash
```

- [x] **Step 2: Run the focused test to verify red**

Run: `python -m pytest tests/unit/test_browser_policy.py -v`

Expected: FAIL because receipts currently contain only `url` metadata and result has no evidence hashes/version/fetch time.

- [x] **Step 3: Implement the smallest adapter extension**

Use a new `TemporaryDirectory` for every call. Start tracing before navigation, increment a counter in `enforce_route`, abort once it exceeds `request.step_limit`, and call `assert_network_scope` for each routed request and final page URL. Launch with `headless=True`; create a context with `accept_downloads=False` and explicit `ignore_https_errors` only from the test-only constructor setting. Record `playwright.__version__`, `browser.version`, and an ISO-8601 UTC timestamp. Store each artifact with the same metadata keys:

```python
metadata = {
    "source_url": page.url,
    "fetched_at": fetched_at,
    "playwright_version": playwright_version,
    "browser_version": browser.version,
    "agent_run_id": request.agent_run_id or "",
    "tool_run_id": request.tool_run_id or "",
    "trace_id": request.trace_id or "",
    "artifact_type": artifact_type,
}
```

Stop tracing in `finally` whenever it started. Persist a nonempty trace before raising `BrowserEvidenceFailure` for navigation/timeout/security failures. Do not include a page body, credentials, or a URL query string in the error message.

- [x] **Step 4: Run focused unit tests and static syntax verification**

Run: `python -m pytest tests/unit/test_browser_policy.py -v && python -m compileall -q src`

Expected: PASS and no compile output.

- [x] **Step 5: Record the checkpoint and commit**

```bash
git add src/salience/browser/playwright.py tests/unit/test_browser_policy.py docs/implementation-progress.md
git commit -m "feat: persist governed browser evidence"
```

## Task 3: Stable Agent And Tool Run Lineage

**Files:**
- Modify: `src/salience/agents/execution.py`
- Modify: `src/salience/agents/intelligence.py`
- Modify: `tests/unit/test_agent_execution.py`
- Modify: `tests/unit/test_intelligence_agents.py`

**Interfaces:**
- Consumes: Task 1 browser request lineage fields and existing `TraceContext`.
- Produces: `AgentExecutionContext(run_id: UUID, ...)`, an `AgentRun.id` equal to `context.run_id`, and browser calls with canonical agent/tool/trace IDs.

- [x] **Step 1: Write failing lineage tests**

```python
assert runtime.context.run_id == run.id
assert browser_request.agent_run_id == str(run.id)
assert browser_request.trace_id == run.trace_context.trace_id
assert output["trust_level"] == "untrusted_external"
```

- [x] **Step 2: Run the focused tests to verify red**

Run: `python -m pytest tests/unit/test_agent_execution.py tests/unit/test_intelligence_agents.py -v`

Expected: FAIL because agent IDs are generated after runtime invocation and the browser runtime constructs an unadorned request.

- [x] **Step 3: Allocate the run UUID before the runtime invocation**

Create `run_id = uuid4()` in `AgentService._invoke`, set it on `AgentExecutionContext`, and use the same value when constructing the successful `AgentRun`. In `BrowserResearchAgentRuntime`, construct a `BrowserResearchRequest` with `agent_run_id=str(context.run_id)`, `tool_run_id=f"browser:{context.run_id}"`, and `trace_id=context.trace_context.trace_id`.

- [x] **Step 4: Run lineage and trust regression tests**

Run: `python -m pytest tests/unit/test_agent_execution.py tests/unit/test_intelligence_agents.py tests/unit/test_research_trust.py -v`

Expected: PASS; browser output stays artifact-only and untrusted.

- [x] **Step 5: Record the checkpoint and commit**

```bash
git add src/salience/agents/execution.py src/salience/agents/intelligence.py tests/unit/test_agent_execution.py tests/unit/test_intelligence_agents.py docs/implementation-progress.md
git commit -m "feat: link browser evidence to agent runs"
```

## Task 4: Idempotent Setup And One-Command Verification

**Files:**
- Create: `scripts/setup-browser-evidence.sh`
- Create: `scripts/verify-browser-evidence.sh`
- Create: `tests/scripts/test_verify_browser_evidence.sh`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: project `pyproject.toml`, `/usr/bin/python3`, `/usr/bin/sudo`, and Playwright CLI.
- Produces: idempotent installer and `PASS`/`FAIL`/`NOT RUN` verifier that reports a durable evidence directory.

- [x] **Step 1: Write a failing shell smoke test with an absent venv**

```bash
SALIENCE_BROWSER_EVIDENCE_VENV="$(mktemp -d)/missing" \
  bash scripts/verify-browser-evidence.sh
test "$?" -eq 2
```

Assert the verifier prints `NOT RUN` and an actionable missing-prerequisite message rather than invoking a browser test.

- [x] **Step 2: Run the shell smoke test to verify red**

Run: `bash tests/scripts/test_verify_browser_evidence.sh`

Expected: FAIL because neither project script exists.

- [x] **Step 3: Implement the scripts**

`setup-browser-evidence.sh` must use a repository-relative `.venv`, run `python3 -m venv`, `"$VENV/bin/python" -m pip install --upgrade pip`, `"$VENV/bin/python" -m pip install '.[browser,dev]'`, `sudo -n "$VENV/bin/python" -m playwright install-deps chromium`, and `"$VENV/bin/python" -m playwright install chromium`. It must measure `df -h .` before/after and print the chosen browser executable/version without printing environment secrets.

`verify-browser-evidence.sh` accepts only optional `--install`. It validates the venv and Chromium with an inline Python `sync_playwright()` launch/version probe, creates `artifacts/browser-evidence/$(date -u +%Y%m%dT%H%M%SZ)-$$`, runs `"$VENV/bin/python" -m pytest tests/integration/test_browser_evidence.py -m browser -v`, and prints the final state and root. Missing prerequisites return status 2 with `NOT RUN`; a test failure returns nonzero with `FAIL`.

Add `artifacts/` to `.gitignore` so payload evidence is retained locally for operators but not committed.

- [x] **Step 4: Run script shell checks and the unavailable-prerequisite path**

Run: `bash -n scripts/setup-browser-evidence.sh scripts/verify-browser-evidence.sh && bash tests/scripts/test_verify_browser_evidence.sh`

Expected: shell smoke test passes; the verifier reports `NOT RUN` and does not claim browser evidence before the integration suite exists.

- [x] **Step 5: Record the checkpoint and commit**

```bash
git add scripts/setup-browser-evidence.sh scripts/verify-browser-evidence.sh tests/scripts/test_verify_browser_evidence.sh .gitignore docs/implementation-progress.md
git commit -m "feat: automate browser evidence verification"
```

## Task 5: Real Chromium Browser-Evidence Integration Suite

**Files:**
- Create: `tests/integration/test_browser_evidence.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: Tasks 1–4, actual Playwright Chromium, and `SALIENCE_BROWSER_EVIDENCE_DIR`.
- Produces: browser-marked tests that write payloads and `.metadata.json` receipts beneath the evidence root and prove all required runtime/security/trust cases.

- [ ] **Step 1: Write the browser-marked tests and disk store fixture**

Create `DirectoryObjectStore(root: Path)` that safely maps each slash-delimited object key below `root`, writes data plus `{key}.metadata.json`, and returns existing `ObjectReceipt` fields with SHA-256 hashes. Start a `ThreadingHTTPServer` wrapped in a temporary self-signed TLS context created by `/usr/bin/openssl`; expose routes `/page`, `/redirect-external`, `/redirect-private`, `/download`, and `/slow`. The JavaScript page must change its body after load and include a hostile instruction string.

Cover these assertions in independently named tests: real launch/browser version and JS text; result structure plus text/PNG/ZIP artifacts; all receipt metadata and hashes; unapproved initial URL; external/private redirects; download attempt without saved file; bounded timeout with ZIP trace and no `secret=` in exception; hostile text remains `untrusted_external` and is refused by `TrustPolicy` for authority/verified memory; and agent runtime passes matching run/trace IDs.

- [ ] **Step 2: Run the integration test before installation to verify an honest unavailable state**

Run: `python -m pytest tests/integration/test_browser_evidence.py -m browser -v`

Expected: FAIL or SKIP with a clear missing Playwright/Chromium message; do not mask an unavailable browser as a passing test.

- [ ] **Step 3: Install the project browser runtime using Task 4**

Run: `bash scripts/setup-browser-evidence.sh`

Expected: Playwright 1.62.0, only Chromium dependencies, revision-matched browser cache, and a recorded free-disk delta.

- [ ] **Step 4: Run the real one-command workflow and inspect evidence**

Run: `bash scripts/verify-browser-evidence.sh --install`

Expected: `PASS`, a real PNG and ZIP, and metadata receipts containing source URL, fetch time, versions, IDs, artifact type, and SHA-256 values beneath the printed evidence root.

- [ ] **Step 5: Record the checkpoint and commit**

```bash
git add tests/integration/test_browser_evidence.py pyproject.toml docs/implementation-progress.md
git commit -m "test: verify real governed browser evidence"
```

## Task 6: Operator Documentation, Full Verification, And Handoff

**Files:**
- Modify: `README.md`
- Modify: `docs/deployment.md`
- Modify: `docs/research.md`
- Modify: `docs/verification.md`
- Modify: `docs/limitations.md`
- Modify: `docs/implementation-progress.md`
- Create: `tests/scripts/test_browser_documentation.sh`

**Interfaces:**
- Consumes: Tasks 1–5 and actual script output.
- Produces: accurate user-facing install, verification, evidence-path, scope, retention, and limitations documentation.

- [ ] **Step 1: Write documentation assertions for required operator language**

```bash
grep -F 'bash scripts/verify-browser-evidence.sh --install' README.md docs/deployment.md docs/verification.md
grep -F 'untrusted_external' docs/research.md docs/limitations.md
```

- [ ] **Step 2: Run assertions to verify red**

Run: `bash tests/scripts/test_browser_documentation.sh`

Expected: FAIL because the one-command installer/verifier and evidence retention semantics are not yet documented.

- [ ] **Step 3: Document only verified behavior**

Document the exact command, no-credential requirement, `artifacts/browser-evidence/` output, Playwright browser cache location, version pin, headless/read-only/allowlist/private-network/download limits, untrusted evidence treatment, EC2 disk requirement, and no destructive cleanup. Update progress with actual OS, disk before/after, version strings, installation command, test count, and screenshot/trace evidence path from Task 5.

- [ ] **Step 4: Re-run complete browser and regression verification**

Run: `bash scripts/verify-browser-evidence.sh && python -m pytest tests/unit/test_browser_policy.py tests/unit/test_agent_execution.py tests/unit/test_intelligence_agents.py tests/unit/test_research_trust.py -v && python -m compileall -q src && git diff --check && bash tests/scripts/test_browser_documentation.sh`

Expected: browser verifier prints `PASS`; focused regressions pass; compiler/diff/doc checks produce no errors.

- [ ] **Step 5: Inspect generated evidence and checkpoint final commit**

Inspect the verifier-reported directory with `find ... -type f -maxdepth 8 -print | sort` and `du -sh ...`; confirm at least one PNG, one ZIP, text artifact, and metadata receipt exist. Then commit:

```bash
git add README.md docs/deployment.md docs/research.md docs/verification.md docs/limitations.md docs/implementation-progress.md tests/scripts/test_browser_documentation.sh
git commit -m "docs: document verified browser evidence workflow"
```
