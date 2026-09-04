import { strict as assert } from "node:assert";
import test from "node:test";
import { Check } from "typebox/value";
import { buildSmartSearchArgs, runSmartSearch, SEARCH_TIMEOUT_MS, RESEARCH_TIMEOUT_MS } from "../src/cli.ts";
import { formatToolResult, parseV1Envelope, type SmartSearchEnvelope } from "../src/result.ts";
import smartSearchExtension from "../extensions/index.ts";

const envelope = (operation: SmartSearchEnvelope["operation"]): SmartSearchEnvelope => ({
  version: 1,
  operation,
  status: "complete",
  data: { value: "safe" },
  attempts: [],
  warnings: [],
  error: null,
});

test("builds fixed argv and only search accepts mode", () => {
  assert.deepEqual(buildSmartSearchArgs("search", "a query"), ["search", "a query"]);
  assert.deepEqual(buildSmartSearchArgs("search", "a query", "fast"), ["search", "a query", "--mode", "fast"]);
  assert.deepEqual(buildSmartSearchArgs("read", "https://example.test/a --mode fast", "fast"), [
    "read",
    "https://example.test/a --mode fast",
  ]);
  assert.deepEqual(buildSmartSearchArgs("research", "a question", "research"), ["research", "a question"]);
});

test("forwards the tool signal and operation timeout", async () => {
  const signal = new AbortController().signal;
  let call: { command: string; args: string[]; options?: { signal?: AbortSignal; timeout?: number } } | undefined;
  const result = await runSmartSearch(
    {
      exec: async (command, args, options) => {
        call = { command, args, options };
        return { stdout: JSON.stringify(envelope("search")), stderr: "", code: 0, killed: false };
      },
    },
    "search",
    "query",
    signal,
    "balanced",
  );
  assert.equal(result.operation, "search");
  assert.deepEqual(call, {
    command: "smart-search",
    args: ["search", "query", "--mode", "balanced"],
    options: { signal, timeout: SEARCH_TIMEOUT_MS },
  });

  await runSmartSearch(
    {
      exec: async (_command, _args, options) => {
        assert.equal(options?.timeout, RESEARCH_TIMEOUT_MS);
        return { stdout: JSON.stringify(envelope("research")), stderr: "", code: 0, killed: false };
      },
    },
    "research",
    "question",
  );
});

test("parses one v1 envelope and rejects unsafe output shapes", () => {
  assert.equal(parseV1Envelope(` \n${JSON.stringify(envelope("read"))}\n`, "read").version, 1);
  for (const output of [
    "",
    "not json",
    `${JSON.stringify(envelope("search"))}${JSON.stringify(envelope("search"))}`,
    "[]",
    "null",
    JSON.stringify({ version: 2, operation: "search" }),
    JSON.stringify({ version: 1, operation: "read" }),
  ]) {
    assert.throws(() => parseV1Envelope(output, "search"), /Smart Search search/);
  }
});

test("turns command failures into safe errors", async () => {
  await assert.rejects(
    runSmartSearch(
      { exec: async () => ({ stdout: "", stderr: "secret-api-key", code: 4, killed: false }) },
      "search",
      "query",
    ),
    (error: unknown) => error instanceof Error && /exit code 4/.test(error.message) && !error.message.includes("secret-api-key"),
  );
  await assert.rejects(
    runSmartSearch(
      { exec: async () => ({ stdout: "", stderr: "", code: 1, killed: true }) },
      "research",
      "question",
    ),
    /timed out or was cancelled/,
  );
  await assert.rejects(
    runSmartSearch(
      { exec: async () => { throw new Error("secret-api-key"); } },
      "read",
      "url",
    ),
    (error: unknown) => error instanceof Error && /could not start/.test(error.message) && !error.message.includes("secret-api-key"),
  );
});

test("truncates deterministic tool content but retains the parsed result", () => {
  const large = envelope("search");
  large.data = { lines: Array.from({ length: 2_500 }, (_, index) => `line-${index}`) };
  const first = formatToolResult(large);
  const second = formatToolResult(large);
  assert.equal(first.content[0].text, second.content[0].text);
  assert.equal(first.details.result, large);
  assert.equal(first.details.truncation.truncated, true);
  assert.ok(first.details.truncation.outputBytes <= first.details.truncation.maxBytes);
  assert.ok(first.details.truncation.outputLines <= first.details.truncation.maxLines);
  assert.match(first.content[0].text, /\[Smart Search output truncated to 50 KB \/ 2,000 lines;/);
  assert.ok(new TextEncoder().encode(first.content[0].text).byteLength <= first.details.truncation.maxBytes);
  assert.ok(first.content[0].text.split("\n").length <= first.details.truncation.maxLines);
});

test("tool schemas reject unknown fields", () => {
  const tools: any[] = [];
  smartSearchExtension({ registerTool: (tool: any) => tools.push(tool) } as any);

  const validParameters = [
    { query: "query", mode: "fast" },
    { url: "https://example.test" },
    { query: "question" },
  ];
  assert.deepEqual(tools.map((tool) => tool.parameters.additionalProperties), [false, false, false]);
  for (const [index, parameters] of validParameters.entries()) {
    assert.equal(Check(tools[index].parameters, { ...parameters, unexpected: true }), false);
  }
});

test("registers exactly the three public tools and maps each operation", async () => {
  const tools: any[] = [];
  const calls: { args: string[]; timeout?: number }[] = [];
  const pi = {
    registerTool(tool: any) {
      tools.push(tool);
    },
    async exec(_command: string, args: string[], options?: { timeout?: number }) {
      calls.push({ args, timeout: options?.timeout });
      return { stdout: JSON.stringify(envelope(args[0] as SmartSearchEnvelope["operation"])), stderr: "", code: 0, killed: false };
    },
  };
  smartSearchExtension(pi as any);
  assert.deepEqual(tools.map((tool) => tool.name), ["web_search", "web_read", "web_research"]);

  await tools[0].execute("id", { query: "query", mode: "fast" }, undefined, undefined, undefined);
  await tools[1].execute("id", { url: "https://example.test" }, undefined, undefined, undefined);
  await tools[2].execute("id", { query: "question" }, undefined, undefined, undefined);
  assert.deepEqual(calls.map(({ args }) => args), [
    ["search", "query", "--mode", "fast"],
    ["read", "https://example.test"],
    ["research", "question"],
  ]);
});
