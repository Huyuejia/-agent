import { createInterface } from "node:readline";
import { Agent, type AgentTool } from "@earendil-works/pi-agent-core";
import { Unsafe, type TSchema } from "typebox";
import {
  fauxAssistantMessage,
  fauxText,
  fauxToolCall,
  getModel,
  registerFauxProvider,
  streamSimple,
  type AssistantMessage,
  type ToolResultMessage,
} from "@earendil-works/pi-ai/compat";

type JsonObject = Record<string, unknown>;
type RunRequest = {
  type: "run";
  objective: string;
  taskState: JsonObject;
  toolSchemas: Record<string, JsonObject>;
  provider: string;
  model: string;
  scriptedScenario?: string | null;
};

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
const waiting = new Map<string, (result: JsonObject) => void>();
let resolveRun: ((request: RunRequest) => void) | undefined;
const firstRequest = new Promise<RunRequest>((resolve) => {
  resolveRun = resolve;
});

rl.on("line", (line) => {
  const message = JSON.parse(line) as JsonObject;
  if (message.type === "run") {
    resolveRun?.(message as RunRequest);
    resolveRun = undefined;
    return;
  }
  if (message.type === "tool_result" && typeof message.callId === "string") {
    waiting.get(message.callId)?.((message.result ?? {}) as JsonObject);
    waiting.delete(message.callId);
  }
});

function send(message: JsonObject): void {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

async function callPythonTool(
  callId: string,
  toolName: string,
  args: JsonObject,
): Promise<JsonObject> {
  const result = new Promise<JsonObject>((resolve) => waiting.set(callId, resolve));
  send({ type: "tool_call", callId, toolName, arguments: args });
  return result;
}

function makeTool(
  name: string,
  label: string,
  description: string,
  schema: JsonObject,
): AgentTool {
  return {
    name,
    label,
    description,
    // Python owns validation and sends the Pydantic-generated schema. Wrapping it
    // gives Pi its required TypeBox marker without duplicating constraints here.
    parameters: Unsafe(schema as TSchema),
    executionMode: "sequential",
    async execute(callId: string, rawParams: unknown) {
      const params = rawParams as JsonObject;
      const result = await callPythonTool(callId, name, params);
      return {
        content: [{ type: "text", text: JSON.stringify(result) }],
        details: result,
      };
    },
  };
}

const allowListedTools = {
  exact_lookup: {
    label: "Exact lookup",
    description: "Resolve one explicit SKU, order, serial, or error-code identifier.",
  },
  graph_lookup: {
    label: "Graph lookup",
    description: "Read a product warranty, protocol list, or compatibility relation.",
  },
  knowledge_search: {
    label: "Knowledge search",
    description: "Search uploaded policy knowledge without modifying business data.",
  },
};

function messageText(message: AssistantMessage | ToolResultMessage): string {
  return message.content
    .filter((block) => block.type === "text")
    .map((block) => block.text)
    .join("\n");
}

function lastToolResult(context: { messages: Array<AssistantMessage | ToolResultMessage | any> }): JsonObject {
  const message = [...context.messages].reverse().find((item) => item.role === "toolResult");
  if (!message) throw new Error("scripted model expected a tool result");
  return JSON.parse(messageText(message)) as JsonObject;
}

function scriptedModel(scenario: string) {
  if (scenario !== "error-code-warranty") {
    throw new Error(`unknown scripted scenario: ${scenario}`);
  }
  const faux = registerFauxProvider();
  faux.setResponses([
    fauxAssistantMessage(
      [fauxText("Resolve the error code first."), fauxToolCall("exact_lookup", {
        entity_type: "error_code",
        identifier: "E1001",
      }, { id: "exact-1" })],
      { stopReason: "toolUse" },
    ),
    (context) => {
      const observation = lastToolResult(context);
      const data = observation.data as { entities: Array<{ attributes: { product_sku: string } }> };
      const observedSku = data.entities[0].attributes.product_sku;
      return fauxAssistantMessage(
        [fauxText("Use the product discovered in the observation."), fauxToolCall("graph_lookup", {
          operation: "warranty",
          product_a: observedSku,
        }, { id: "graph-1" })],
        { stopReason: "toolUse" },
      );
    },
    (context) => {
      const observation = lastToolResult(context);
      const evidence = observation.evidence as Array<{ evidence_id: string; text: string }>;
      return fauxAssistantMessage(JSON.stringify({
        answer: evidence[0].text,
        intent: "agent_dynamic_task",
        confidence: 1,
        source_type: "knowledge_graph",
        evidence_refs: evidence.map((item) => item.evidence_id),
        handoff_required: false,
      }));
    },
  ]);
  return { model: faux.getModel(), unregister: () => faux.unregister() };
}

function parseCandidate(text: string): JsonObject {
  const trimmed = text.trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "");
  return JSON.parse(trimmed) as JsonObject;
}

async function main(): Promise<void> {
  const request = await firstRequest;
  const enabledNames = Object.keys(request.toolSchemas);
  if (enabledNames.some((name) => !(name in allowListedTools))) {
    throw new Error("Python requested a tool outside the fixed allow-list");
  }
  const tools = enabledNames.map((name) => {
    const metadata = allowListedTools[name as keyof typeof allowListedTools];
    return makeTool(name, metadata.label, metadata.description, request.toolSchemas[name]);
  });
  const scripted = request.scriptedScenario
    ? scriptedModel(request.scriptedScenario)
    : undefined;
  const model = scripted?.model ?? getModel(request.provider as any, request.model as any);
  if (!model) throw new Error(`unknown Pi model ${request.provider}/${request.model}`);
  const agent = new Agent({
    streamFn: streamSimple,
    toolExecution: "sequential",
    initialState: {
      systemPrompt:
        "You are a constrained customer-service task runner. Use only the supplied " +
        "read-only tools. Later tool arguments must come from observations when required. " +
        "Return one JSON candidate with answer, intent, confidence, source_type, " +
        "evidence_refs, and handoff_required. Cite only evidence IDs returned by tools.",
      model,
      thinkingLevel: "off",
      tools,
    },
  });
  agent.subscribe((event) => {
    if (event.type === "message_end" && event.message.role === "assistant") {
      const actions = event.message.content
        .filter((block) => block.type === "toolCall")
        .map((block) => ({ toolName: block.name, arguments: block.arguments }));
      send({
        type: "runtime_event",
        eventType: "model_action",
        payload: { actions, hasCandidateText: messageText(event.message).length > 0 },
      });
    }
  });
  await agent.prompt(
    `Objective: ${request.objective}\nTaskState: ${JSON.stringify(request.taskState)}`,
  );
  const last = agent.state.messages[agent.state.messages.length - 1];
  if (!last || last.role !== "assistant") throw new Error("Pi produced no candidate");
  send({ type: "completed", candidate: parseCandidate(messageText(last)) });
  scripted?.unregister();
}

main().catch((error: unknown) => {
  send({ type: "error", message: error instanceof Error ? error.message : String(error) });
  process.exitCode = 1;
});
