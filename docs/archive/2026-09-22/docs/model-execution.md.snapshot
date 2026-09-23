# Model Execution

`ModelGateway` is provider-neutral: static fixtures, OpenAI-compatible HTTP endpoints, and future
custom gateways share one structured contract. Configure only `MODEL_RUNTIME_ID`, `MODEL_BASE_URL`,
`MODEL_NAME`, and an `env://` secret reference; secret values never enter canonical records.

`RecordedModelGateway` validates JSON Schema before output can be consumed and records
provider/model/version, input/output hashes, artifact references, usage, latency, cost hooks,
failure status, parent agent/job, and trace. Invalid JSON is recorded as `invalid_output` and
cannot become strategy, opportunity, memory, package, claim, or brief state.

No model provider is required for CI or the default loop. Semantic judgement is bounded and
supplemental; deterministic scoring and evidence rules remain authoritative.
