# Governance Controls

The Phase 1–9 governance services are deterministic, provider-neutral
control-plane components. They decide whether a proposed effect may proceed;
they do not perform the effect.

## Authorization

`PolicyEngine` denies by default. An authorization request must reference an active,
unexpired policy, use an allowed effect class, hold only known and granted scopes, and
fit the currently available budget. A non-dry-run effect configured as
approval-required remains denied until an explicit approval is supplied.

Every decision is sent to the governance journal with the policy version and W3C trace
identity. The journal records a policy-decision provenance record for both approvals
and denials so downstream activity can explain why it was permitted or blocked.

## Budgets and Costs

`BudgetService` reserves an estimated integer micro-unit amount before work begins.
For non-dry creative work, the caller must supply the canonical `budget_id` and
the workflow reserves each provider effect before submission. Settlement records
actual cost separately; repeated settlement with the same amount returns the
original ledger entry rather than creating a duplicate. Unknown actual cost and
overage remain non-final states, so they cannot pass the ready-package gate. The
ledger distinguishes:

- `estimated`
- `reserved`
- `released`
- `actual`

The service deliberately uses integer micro-units to avoid floating-point
currency errors. The canonical schema reserves `budgets`,
`budget_reservations`, and `cost_ledger_entries` for durable accounting, while
job inspection and recorded model invocations project estimated/reserved and
actual-cost hooks without requiring a specific model or payment provider.

## Creative Gates

Phase 7–8 applies the same controls before a provider job and before a ready
package is finalized. A versioned capability registry first selects only an
enabled, compatible provider whose declared format, aspect ratio, duration,
async/reconciliation features, rate/concurrency state, allow-list, cost ceiling,
and bounded variant quota satisfy the request. `RightsPolicy` fails closed for real likeness or voice
clone use unless active, unrevoked, unexpired consent permits the channel,
territory, and commercial use. `PlatformValidator` checks only the immutable
platform-profile rules. `OriginalityValidator` rejects exact repeated title,
narrative, or thumbnail fingerprints while retaining deterministic metrics.

`DisclosurePolicy` derives synthetic-media labels from governed media facts and
profile/jurisdiction references, never from an unverified provider assertion.
The package retains the disclosure decision and only an approved final decision
can become `ReadyToPublishPackage@v1`. This approval is a Phase-9 handoff, not
permission to make a publish effect.

Verified provider callbacks are converted at the adapter boundary to a
credential-free event projection before persistence. Their delivery identity and
safe payload hash make replay idempotent; a repeated delivery identity with
different canonical facts is rejected. Lifecycle transitions remain conditional
and terminal. An approved decision graph is immutable: identical
replay returns the original row, while altered governed input must allocate a
new distribution and ready-package version.

## Secrets and Permissions

`SecretResolver` receives a URI and a required scope set. It returns a
`SecretValue` only when all scopes are present. A `SecretValue` has a redacted
string/repr representation; callers must explicitly call `reveal()` at the
provider boundary. No secret value is represented in the canonical database schema.

Audit redaction removes sensitive field names and registered literal secret values
from nested structured data. Raw credentials must never be placed in a job payload,
checkpoint, provenance record, exception, or log entry.

## Tracing

`TraceContext` produces W3C `traceparent`-compatible identifiers, preserves parent
lineage in an additional product header, and projects the context into OpenTelemetry.
The emitted span uses the same trace ID and carries the canonical Salience span IDs as
attributes. Canonical rows retain the trace/span IDs without coupling them to an
OpenTelemetry SDK object. The intelligence workflow carries the same trace
through source, signal, agent, strategy, package, claim, and brief lineage.

## Future Hooks

The canonical rows already reserve fields for tenant isolation, jurisdictional policy,
data classification/retention, delegated authority, trust classification, provenance
(C2PA), and protocol compatibility. Those fields do not grant access themselves:
production enforcement must remain in the policy, scope, approval, and workflow
boundaries.

## Publisher Gates

Phase 9 introduces a separate fail-closed authorization immediately before a
publisher effect. It requires the ready package to remain approved and bound to
the same workspace/program; an active account/connection with current required
scopes; a compatible, verified account- and platform-bound capability profile;
allowed platform, destination, locale,
territory, and visibility; current policy/rights/disclosure/approval facts; an
explicit budget reservation; and available rate/quota facts. A creative approval
or credential never confers publishing authority.

The durable workflow rechecks that decision immediately before remote submission, writes an
external-effect plan and reservation first, and settles known actual cost before
terminal publication. Publisher callbacks are adapter-verified before a
credential-free hash/identity projection reaches PostgreSQL. The private-only
YouTube session boundary requires the official `youtube.upload` lease scope and
a durable edge-session store;
neither its bearer value nor opaque upload URI enters audit, provenance,
checkpoint, or canonical publication records.
