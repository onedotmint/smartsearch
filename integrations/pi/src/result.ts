import {
  DEFAULT_MAX_BYTES,
  DEFAULT_MAX_LINES,
  truncateHead,
  type TruncationResult,
} from "@earendil-works/pi-coding-agent";

export interface SmartSearchEnvelope extends Record<string, unknown> {
  version: 1;
  operation: "search" | "read" | "research";
}

export interface SmartSearchToolDetails {
  result: SmartSearchEnvelope;
  truncation: TruncationResult;
}

export interface SmartSearchToolResult {
  content: [{ type: "text"; text: string }];
  details: SmartSearchToolDetails;
}

export function parseV1Envelope(
  stdout: string,
  expectedOperation: SmartSearchEnvelope["operation"],
): SmartSearchEnvelope {
  const text = stdout.trim();
  if (!text) {
    throw new Error(`Smart Search ${expectedOperation} returned no JSON output`);
  }

  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(`Smart Search ${expectedOperation} returned malformed JSON output`);
  }

  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Smart Search ${expectedOperation} returned an invalid JSON envelope`);
  }
  const envelope = value as Record<string, unknown>;
  if (envelope.version !== 1) {
    throw new Error(`Smart Search ${expectedOperation} returned an unsupported JSON version`);
  }
  if (envelope.operation !== expectedOperation) {
    throw new Error(`Smart Search ${expectedOperation} returned the wrong operation`);
  }
  return envelope as SmartSearchEnvelope;
}

const TRUNCATION_NOTICE =
  "[Smart Search output truncated to 50 KB / 2,000 lines; the complete parsed result is available in details.]";

function formatTruncatedContent(serialized: string): { content: string; truncation: TruncationResult } {
  const truncation = truncateHead(serialized, {
    maxBytes: DEFAULT_MAX_BYTES,
    maxLines: DEFAULT_MAX_LINES,
  });
  if (!truncation.truncated) {
    return { content: truncation.content, truncation };
  }

  const noticeSuffix = `\n\n${TRUNCATION_NOTICE}`;
  const noticeBytes = new TextEncoder().encode(noticeSuffix).byteLength;
  const bounded = truncateHead(serialized, {
    maxBytes: DEFAULT_MAX_BYTES - noticeBytes,
    maxLines: DEFAULT_MAX_LINES - 2,
  });
  return { content: `${bounded.content}${noticeSuffix}`, truncation };
}

export function formatToolResult(envelope: SmartSearchEnvelope): SmartSearchToolResult {
  const serialized = JSON.stringify(envelope, null, 2);
  const formatted = formatTruncatedContent(serialized);
  return {
    content: [{ type: "text", text: formatted.content }],
    details: { result: envelope, truncation: formatted.truncation },
  };
}
