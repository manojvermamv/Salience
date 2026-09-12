# Acceptance Tests and Guardrails

These are system-level outcomes. The build agent may add stronger tests but should not silently weaken them.

## Niche-only autonomous experience

- A user can create a program using only a niche plus optional constraints.
- The system can bootstrap provisional audience/strategy/content pillars without requiring internal-agent configuration.
- Missing credentials or irreversible business choices produce a clear request for human input rather than silent guessing.
- "100M" is not represented as a guaranteed performance result.

## Lead Agent and multi-agent behavior

- One durable `LeadContentAgent` owns a complete dry-run and can resume after process/container restart.
- The Lead Agent can complete the supported thin slice using repeated calls to one model configuration.
- The Lead Agent can delegate a bounded task to one or more specialists and receive structured results/artifacts while retaining parent lineage.
- Parallel child-agent work is bounded by budgets/timeouts and can be cancelled.
- A specialist may use the same underlying model as the Lead Agent; no test assumes one deployment per role.
- Disabling an optional specialist degrades only dependent behavior or falls back cleanly.

## First-class callable specialists

- `GET /v1/agents` (or equivalent canonical API) discovers enabled specialists.
- Every enabled specialist exposes version, skills/capabilities, typed input/output and effect classification.
- At least one specialist can be called directly by an end user through API and CLI/SDK.
- The same specialist contract can be invoked internally by the Lead Agent.
- Long-running agent calls return a run/task identity with status/events/cancel support.
- Direct invocation and delegated invocation produce the same canonical artifact/result types.
- A team/crew composition can invoke multiple agents without making them non-callable individually.

## Framework and protocol independence

- Provider/framework-specific SDK objects do not appear in canonical domain entities.
- At least one representative agent can run through two eligible runtime/model configurations without upstream/downstream domain-code changes.
- MCP tool discovery/call works through the tool gateway.
- A fixture A2A v1.x remote agent can expose a discoverable card, receive a task and return/stream an artifact/result.
- The system does not require A2A for local agents or misuse MCP as the only remote-agent protocol.
- Removing CrewAI/OpenAI Agents/another framework adapter leaves canonical agent definitions/history readable.

## Durable execution

- A run survives worker/process restart from a durable checkpoint.
- Jobs use bounded retries/backoff and error/dead-letter state.
- Retrying an external-write step does not duplicate posts/assets/payments/actions.
- Long waits (async media jobs, approvals, schedules) do not depend on one process staying alive.
- Budgets/rate limits can stop runaway agent/tool loops.

## Memory and context

- Agents receive scoped context, not unconditional global-memory dumps.
- Lead and specialist runs preserve explicit parent/child lineage.
- Removing an integration does not orphan core content/agent history.
- Evidence and analytics memories remain traceable to their sources/events.

## Research and evidence

- Accepted factual claims that require verification link to evidence or an explicit verification status.
- Browser-researched evidence records the browser/tool/agent run that produced it.
- Source identity and fetch time are retained.
- Removing evidence cannot leave dependent claims falsely verified.

## Strategic packaging and scripting

- Multiple strategic package candidates are preserved before selection.
- Strategic packaging occurs before full script/copy generation.
- The selected package records audience, angle, hook, promise/payoff and format.
- Fact checking is logically independent from the drafting pass.
- Platform/length/format constraints that can be deterministic are code validators, not prompt-only instructions.

## Creative production

- A creative request is expressed through canonical capabilities rather than hard-coded provider classes.
- At least two creative implementations can satisfy one representative generic capability or one can fail cleanly with a documented missing capability.
- Async generation supports job IDs, polling/webhooks, cancellation and restart-safe reconciliation.
- Multiple variants can be generated and compared.
- Output assets are copied to owned storage when licensing/terms allow.
- Provider/model/tool version, normalized parameters, references, cost/usage and output hashes are recorded.
- A provider-specific feature can be requested without contaminating the generic domain schema.
- Advertised MCP/action capabilities are verified before production reliance.

## Distribution packaging and governance

- Final title/thumbnail/caption/SEO/metadata are represented separately from the earlier strategic package.
- Verification occurs at relevant intermediate stages and again before publish.
- Rights/provenance, brand, safety, platform policy, duplicate detection and technical checks can block progression.
- Human approval can be required by policy without regenerating upstream content.

## Browser/app control

- A test browser job can navigate a JavaScript site and extract structured evidence.
- Browser sessions are isolated and cannot read unrelated secrets.
- Failed browser jobs have useful traces without secret leakage.
- Write-capable browser/app actions are separately authorized.
- A semantic Browser Research Agent can be directly invoked, while stable recurring flows can also run as deterministic tools.
- App automation remains optional.

## Publishing

- Development defaults cannot publish live.
- Publication writes record external IDs.
- Timeout/retry reconciles remote state before another create.
- Platform payloads derive from canonical publication identity.
- Publishing credentials are least privilege.

## Analytics and learning

- Metrics are time-series snapshots.
- Metrics trace to topic, package, script, assets, distribution package and behavior/agent versions.
- Learning proposals cannot silently overwrite active strategy/prompt/policy.
- Every behavior change is versioned and associated with an experiment.
- Experiments specify hypothesis, primary metric, guardrails, minimum sample/time and keep/rollback result.
- Rollback preserves experiment history.

## Reuse and replaceability

- Major external capabilities sit behind owned contracts/adapters.
- Optional integrations can be removed without corrupting unrelated workflows.
- Adapter contract versions are explicit and tested.
- Dependency decisions record license, security, maintenance, operating cost and replacement path.
- Any custom subsystem duplicating a mature available tool has a documented long-term ownership reason.
- Any fork records upstream version and local patch/upgrade procedure.

## Security

- No secrets committed to source.
- Logs redact tokens/cookies/passwords.
- Agents/tools receive only required credential scopes.
- Browser auth state is encrypted if persisted.
- Live publishing, purchases, destructive deletes and account/security changes can be approval gated.
- Dependency licenses are reviewed before integration/distribution.

## Deployment

A clean Linux host can:

1. clone the repository;
2. configure environment/secret references;
3. deploy the documented container stack;
4. run migrations;
5. pass health checks;
6. run a niche-only seeded dry-run end to end;
7. invoke one specialist directly;
8. demonstrate restart/resume;
9. inspect complete provenance.

## Credible first release

It does not need every platform or dozens of agents. It does need:

- Lead Agent / Supervisor;
- 3-5 genuinely useful callable specialist agents;
- 2-3 signal/research sources;
- one strong strategic packaging/script path;
- one media path;
- one publishing adapter in sandbox/dry-run;
- analytics ingestion;
- one measured experiment loop;
- browser research capability;
- durable state/idempotency/audit foundations;
- framework-neutral agent registry and direct invocation API.
