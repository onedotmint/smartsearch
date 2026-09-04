import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { runSmartSearch } from "../src/cli.ts";
import { formatToolResult } from "../src/result.ts";

const SearchParameters = Type.Object({
  query: Type.String({ minLength: 1, description: "Search query" }),
  mode: Type.Optional(
    Type.Union([
      Type.Literal("fast"),
      Type.Literal("balanced"),
      Type.Literal("research"),
    ], { description: "Search depth preset" }),
  ),
}, { additionalProperties: false });

const ReadParameters = Type.Object({
  url: Type.String({ minLength: 1, description: "Known HTTP(S) URL to read" }),
}, { additionalProperties: false });

const ResearchParameters = Type.Object({
  query: Type.String({ minLength: 1, description: "Research question" }),
}, { additionalProperties: false });

export default function smartSearchExtension(pi: ExtensionAPI): void {
  pi.registerTool({
    name: "web_search",
    label: "Web search",
    description: "Discover relevant web sources for a query. Output is limited to 50 KB / 2,000 lines; the complete parsed result remains available in details.",
    parameters: SearchParameters,
    execute: async (_toolCallId, params, signal) =>
      formatToolResult(await runSmartSearch(pi, "search", params.query, signal, params.mode)),
  });

  pi.registerTool({
    name: "web_read",
    label: "Read web page",
    description: "Retrieve evidence from a known URL. Output is limited to 50 KB / 2,000 lines; the complete parsed result remains available in details.",
    parameters: ReadParameters,
    execute: async (_toolCallId, params, signal) =>
      formatToolResult(await runSmartSearch(pi, "read", params.url, signal)),
  });

  pi.registerTool({
    name: "web_research",
    label: "Web research",
    description: "Gather staged web evidence and identify remaining gaps. Output is limited to 50 KB / 2,000 lines; the complete parsed result remains available in details.",
    parameters: ResearchParameters,
    execute: async (_toolCallId, params, signal) =>
      formatToolResult(await runSmartSearch(pi, "research", params.query, signal)),
  });
}
