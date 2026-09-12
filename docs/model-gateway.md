# Model gateway

Agent manifests do not select a model runtime. `ModelGateway` receives a prompt
and output schema and returns provider-neutral output, usage, latency, and
runtime metadata. The deterministic static adapter is the clean-deployment
default and never contacts a provider.

`OpenAICompatibleAdapter` is an optional plain-HTTP edge adapter. It requires a
configured key, requests JSON output, validates the returned JSON against the
requested schema, and records only runtime/model metadata. It is intentionally
not configured by default and does not add an OpenAI SDK dependency.
