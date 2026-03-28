/**
 * Parse JSON text from MCP ``tools/call`` responses (GitHub #46).
 *
 * Kept separate from ``mcp_client.ts`` so Vitest can mock the client module
 * while still importing this pure helper.
 */

/**
 * Extract JSON text from an MCP ``tools/call`` result.
 *
 * The official SDK returns ``{ content: [{ type: "text", text: "..." }] }``.
 * Tests and older mocks may return a raw string. Empty content yields ``""``.
 */
export function extractMcpToolText(result: unknown): string {
  if (result == null) {
    return "";
  }
  if (typeof result === "string") {
    return result;
  }
  if (typeof result === "object" && result !== null && "content" in result) {
    const content = (result as { content?: unknown }).content;
    if (!Array.isArray(content)) {
      return "";
    }
    const parts: string[] = [];
    for (const item of content) {
      if (item && typeof item === "object" && "text" in item) {
        const t = (item as { text?: unknown }).text;
        if (typeof t === "string") {
          parts.push(t);
        }
      }
    }
    return parts.join("");
  }
  return "";
}
