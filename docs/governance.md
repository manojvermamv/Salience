# Governance Controls

The Phase-1 governance services are deterministic, provider-neutral control-plane
components. They decide whether a proposed effect may proceed; they do not perform the
effect.

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
The reservation is idempotent per run identifier. Settlement releases the reservation
and records actual cost separately; repeated settlement with the same amount returns
the original ledger entry rather than creating a duplicate. The ledger distinguishes:

- `estimated`
- `reserved`
- `released`
- `actual`

The service deliberately uses integer micro-units to avoid floating-point currency
errors. A database-backed repository will persist these canonical values using the
`budgets`, `budget_reservations`, and `cost_ledger_entries` schema in a later
durable-job slice.

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
OpenTelemetry SDK object.

## Future Hooks

The canonical rows already reserve fields for tenant isolation, jurisdictional policy,
data classification/retention, delegated authority, trust classification, provenance
(C2PA), and protocol compatibility. Those fields do not grant access themselves:
production enforcement must remain in the policy, scope, approval, and workflow
boundaries.

