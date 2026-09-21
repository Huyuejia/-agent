# Controlled Pi runtime

The Agent path uses the official `@earendil-works/pi-agent-core` package at
exact version `0.85.1`, paired with `@earendil-works/pi-ai@0.85.1`. The version
was checked against the official Pi repository and npm package on 2026-09-17.
The core package was selected instead of the coding-agent SDK because this
application needs only the in-memory model/tool loop; filesystem tools,
terminal UI, skill discovery, and Pi session files would widen the boundary.

`agent-runtime/` is a thin TypeScript JSONL-stdio adapter. It creates one
in-memory Pi `Agent`, registers only `exact_lookup`, `graph_lookup`, and
`knowledge_search`, forwards tool calls to Python, normalizes model-action
events, and returns an untrusted structured candidate. Python remains the
source of truth for business retrieval, validation, task state, verification,
message persistence, and trace persistence.

Install and verify the adapter with Node 22.19 or newer:

```bash
cd agent-runtime
npm ci
npm test
```

The smoke scenario uses Pi's official faux provider. It performs a real Pi
loop in which the first model action calls `exact_lookup(E1001)`, the next
model action reads `product_sku` from that Tool observation and calls
`graph_lookup(warranty, observed_sku)`, and the final model action submits a
candidate referencing only the graph Evidence. No model credential or
external database is required for this smoke lane.
