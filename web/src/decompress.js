/**
 * Client-side gzip decompression using the native DecompressionStream.
 * Pure module: no DOM access, so it is testable from web/src-tests.html.
 */

/**
 * Decompress a gzip stream (chunked reads, assembled like the fetch path).
 * @param {Uint8Array} bytes - gzip bytes read off the wire
 * @returns {Promise<Uint8Array>}
 * @throws {Error} named DecompressionUnsupported when the browser lacks
 *   DecompressionStream — callers surface this in the overlay, they never
 *   fall back to raw rungs silently.
 */
export async function decompressGzip(bytes) {
  if (typeof DecompressionStream === "undefined") {
    const err = new Error("browser too old for gzip index — needs DecompressionStream");
    err.name = "DecompressionUnsupported";
    throw err;
  }
  const input = new ReadableStream({
    start(controller) {
      controller.enqueue(bytes);
      controller.close();
    },
  });
  const stream = input.pipeThrough(new DecompressionStream("gzip"));
  const reader = stream.getReader();
  /** @type {Uint8Array[]} */
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
  }
  const out = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.length;
  }
  return out;
}
