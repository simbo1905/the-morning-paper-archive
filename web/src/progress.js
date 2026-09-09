/**
 * Fetch with byte-level progress and a small aggregation state machine.
 * Pure module: no DOM access, so it is testable from web/src-tests.html.
 */

/**
 * @callback ProgressCallback
 * @param {number} received - bytes received so far
 * @param {number | null} total - total bytes when Content-Length is present, else null
 */

/**
 * Fetch a URL and accumulate the body bytes, reporting progress per chunk.
 * @param {string} url
 * @param {ProgressCallback} [onProgress]
 * @returns {Promise<Uint8Array>}
 */
export async function fetchWithProgress(url, onProgress) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`HTTP ${response.status} for ${url}`);
  const totalHeader = response.headers.get("content-length");
  const total = totalHeader ? Number(totalHeader) : null;
  const reader = response.body.getReader();
  /** @type {Uint8Array[]} */
  const chunks = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    if (onProgress) onProgress(received, total);
  }
  const bytes = new Uint8Array(received);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return bytes;
}

/**
 * Fetch a URL as text with the same progress reporting.
 * @param {string} url
 * @param {ProgressCallback} [onProgress]
 * @returns {Promise<string>}
 */
export async function fetchTextWithProgress(url, onProgress) {
  const bytes = await fetchWithProgress(url, onProgress);
  return new TextDecoder().decode(bytes);
}

/**
 * Aggregate progress across all initial fetches: expect() declares bytes we
 * are going to download, add() accounts bytes received. percent() is
 * floor(loaded / expected), clamped to 100.
 */
export class ProgressReporter {
  /** @type {number} */
  #loaded = 0;
  /** @type {number} */
  #expected = 0;
  /** @type {((percent: number) => void) | null} */
  #onUpdate;

  /**
   * @param {((percent: number) => void)} [onUpdate]
   */
  constructor(onUpdate) {
    this.#onUpdate = onUpdate ?? null;
  }

  /**
   * Declare that `bytes` more are expected (sums across fetches).
   * @param {number} bytes
   * @returns {void}
   */
  expect(bytes) {
    this.#expected += bytes;
    this.#emit();
  }

  /**
   * Remove previously expected bytes that will not be fetched (warm-visit
   * IndexedDB hit), so the bar renormalizes instead of ending short of 100%.
   * @param {number} bytes
   * @returns {void}
   */
  relinquish(bytes) {
    this.#expected = Math.max(0, this.#expected - bytes);
    this.#emit();
  }

  /**
   * Account `bytes` received.
   * @param {number} bytes
   * @returns {void}
   */
  add(bytes) {
    this.#loaded += bytes;
    this.#emit();
  }

  /**
   * @returns {number} 0..100
   */
  percent() {
    if (this.#expected === 0) return 0;
    return Math.min(100, Math.floor((100 * this.#loaded) / this.#expected));
  }

  #emit() {
    if (this.#onUpdate) this.#onUpdate(this.percent());
  }
}

/**
 * @param {number} n
 * @returns {string}
 */
export function fmtBytes(n) {
  return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${(n / 1024).toFixed(1)} KB`;
}
