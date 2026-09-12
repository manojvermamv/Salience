# Browser, App and Autonomous Research Control

Browser control is required for both the **engineering/build agent's research** and the finished system's runtime research/integration needs. It is a tool capability that may be used directly by the Lead Agent or by a callable Browser/Research specialist agent.

## Access ladder

Use the least brittle method that can complete the job:

```text
1. Official API / SDK / feed / protocol
2. Direct HTTP fetch + parser
3. Clone repository + local source/docs inspection
4. Deterministic Playwright browser script / Playwright MCP
5. Higher-level adaptive browser agent
6. App automation such as Appium for truly app-only paths
7. Human handoff for CAPTCHA/MFA/policy/legal ambiguity
```

Do not use GUI automation merely because it is convenient. Stable API/scripted paths are easier to test, secure and maintain.

## Build-agent research behavior

Before building or adopting a capability, the engineering agent should:

- search for maintained open/free/self-hostable libraries, frameworks, agent/tool protocols, MCP servers, A2A agents and reference implementations;
- inspect official docs plus source/tests/issues/releases;
- inspect `LICENSE`, `SECURITY`, deployment, schemas and examples;
- use browser control for JavaScript/interactive docs only when static retrieval is insufficient;
- clone repos for deeper verification rather than trusting README claims;
- capture exact release/commit and findings in project research/ADR docs;
- test the local UI/API it builds;
- use screenshots/traces/logs when debugging;
- verify current vendor capabilities before encoding them as assumptions.

## Runtime browser contract

A browser job specifies:

```text
job_id / parent agent_run_id
task / expected output schema
allowed origins/domains
read-only vs write/action permission
auth-profile reference
time / step / network budget
download/upload permissions
screenshot/trace policy
expected artifacts/evidence
idempotency/reconciliation information
```

The worker returns structured output plus traceable artifacts and never returns hidden credentials.

## Browser specialist agent

A `browser_research` specialist can be a first-class callable agent when semantic navigation, multi-step investigation or adaptive interaction is valuable. It uses the same canonical agent invocation contract as other specialists.

Do **not** force all browser jobs through an AI agent. Recurring stable flows should become deterministic Playwright/API tools where possible.

## Recommended implementations

- **Playwright**: deterministic browser base for stable interactions, testing, downloads/uploads, authenticated dashboards and traces.
- **Browser Use or equivalent**: adaptive browser worker for changing/unfamiliar UIs.
- **Appium/platform-native automation**: only for app-only requirements.

A successful adaptive browser exploration that becomes recurring should be promoted into a tested deterministic integration when practical.

## Authentication and safety

- store auth profiles outside job payloads;
- least-privilege scopes per worker/agent;
- separate research identities from publishing identities;
- encrypt persisted browser state;
- redact cookies/tokens from logs;
- prefer OAuth/service credentials over username/password automation;
- MFA/CAPTCHA normally triggers human handoff rather than bypass;
- write-capable browser/app actions use the same approval, budget and idempotency policy as API actions.

Development defaults: no purchases, destructive deletes, live publishing, account/security changes or access-control bypass.

## Observability

Record:

- agent/tool job identity;
- engine/version/browser version;
- start/end/status;
- visited origins;
- action log;
- failure traces/screenshots where permitted;
- downloaded/uploaded artifact hashes;
- external IDs after writes;
- cost/time/step usage when available.

The audit trail must explain what happened without leaking secrets.

## References

- Playwright: https://github.com/microsoft/playwright
- Browser Use: https://github.com/browser-use/browser-use
- Appium: https://appium.io/
- MCP: https://modelcontextprotocol.io/
- A2A: https://a2a-protocol.org/
