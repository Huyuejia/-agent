import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";

const child = spawn(process.execPath, ["dist/index.js"], {
  cwd: new URL("..", import.meta.url),
  stdio: ["pipe", "pipe", "inherit"],
});
const calls = [];
const input = {
  type: "run",
  objective: "先查出错误码 E1001 对应的产品，再告诉我这个产品的保修政策",
  taskState: { status: "RUNNING" },
  toolSchemas: { exact_lookup: {}, graph_lookup: {}, knowledge_search: {} },
  provider: "faux",
  model: "faux",
  scriptedScenario: "error-code-warranty",
};
child.stdin.write(`${JSON.stringify(input)}\n`);

const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
let candidate;
for await (const line of lines) {
  const message = JSON.parse(line);
  if (message.type === "tool_call") {
    calls.push(message);
    const result = message.toolName === "exact_lookup"
      ? {
          status: "OK",
          data: { entities: [{ attributes: { product_sku: "Cam-A1" } }] },
          evidence: [{ evidence_id: "error_codes:error-1", text: "E1001" }],
          retryable: false,
          latency_ms: 1,
        }
      : {
          status: "OK",
          data: { product: "Cam-A1" },
          evidence: [{
            evidence_id: "neo4j:warranty:Cam-A1",
            text: "Cam-A1 适用标准保修，保修期 1 年。",
          }],
          retryable: false,
          latency_ms: 1,
        };
    child.stdin.write(`${JSON.stringify({ type: "tool_result", callId: message.callId, result })}\n`);
  }
  if (message.type === "completed") {
    candidate = message.candidate;
    child.stdin.end();
    break;
  }
  if (message.type === "error") throw new Error(message.message);
}

assert.deepEqual(calls.map((call) => call.toolName), ["exact_lookup", "graph_lookup"]);
assert.equal(calls[1].arguments.product_a, "Cam-A1");
assert.deepEqual(candidate.evidence_refs, ["neo4j:warranty:Cam-A1"]);
console.log("Pi smoke passed: model -> tool -> observation -> model -> tool -> candidate");
