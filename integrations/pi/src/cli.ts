import type { ExecOptions, ExecResult } from "@earendil-works/pi-coding-agent";
import { parseV1Envelope, type SmartSearchEnvelope } from "./result.ts";

export type SearchMode = "fast" | "balanced" | "research";
export type SmartSearchOperation = "search" | "read" | "research";

export const SEARCH_TIMEOUT_MS = 30_000;
export const READ_TIMEOUT_MS = 30_000;
export const RESEARCH_TIMEOUT_MS = 120_000;

export interface SmartSearchExecutor {
  exec(command: string, args: string[], options?: ExecOptions): Promise<ExecResult>;
}

export function buildSmartSearchArgs(
  operation: SmartSearchOperation,
  value: string,
  mode?: SearchMode,
): string[] {
  if (operation === "search") {
    return mode ? ["search", value, "--mode", mode] : ["search", value];
  }
  return [operation, value];
}

function timeoutFor(operation: SmartSearchOperation): number {
  return operation === "research" ? RESEARCH_TIMEOUT_MS : operation === "read" ? READ_TIMEOUT_MS : SEARCH_TIMEOUT_MS;
}

export async function runSmartSearch(
  pi: SmartSearchExecutor,
  operation: SmartSearchOperation,
  value: string,
  signal?: AbortSignal,
  mode?: SearchMode,
): Promise<SmartSearchEnvelope> {
  const args = buildSmartSearchArgs(operation, value, mode);
  let result: ExecResult;

  try {
    result = await pi.exec("smart-search", args, {
      signal,
      timeout: timeoutFor(operation),
    });
  } catch {
    if (signal?.aborted) {
      throw new Error(`Smart Search ${operation} was cancelled`);
    }
    throw new Error(`Smart Search ${operation} could not start the CLI`);
  }

  if (result.killed) {
    throw new Error(`Smart Search ${operation} timed out or was cancelled`);
  }
  if (result.code !== 0) {
    throw new Error(`Smart Search ${operation} failed (exit code ${result.code})`);
  }
  if (typeof result?.stdout !== "string") {
    throw new Error(`Smart Search ${operation} returned invalid CLI output`);
  }
  return parseV1Envelope(result.stdout, operation);
}
