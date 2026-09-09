/**
 * Merge simple JSON metadata hits with Tantivy article hits.
 * Pure function: no DOM, no fetch — testable from web/src-tests.html.
 *
 * Semantics: union of both hit sets deduped by paper date. A date present in
 * both becomes source "both" with score = max of the two scores; metadata
 * fields missing from the article hit are filled in from the JSON hit.
 * Output is sorted by score desc, ties break to the newer date, sliced to
 * the limit.
 */

/**
 * @typedef {object} MergedHit
 * @property {number} score
 * @property {string} date
 * @property {"article" | "json" | "both"} source
 * @property {string} [title]
 * @property {ReadonlyArray<string>} [authors]
 * @property {number} [year]
 * @property {string} [venue]
 * @property {string} [slug]
 * @property {number} [shardIndex]
 */

/**
 * @param {ReadonlyArray<import("./types.js").SearchResult>} jsonHits
 * @param {ReadonlyArray<Record<string, unknown>>} articleHits
 * @param {number} limit
 * @returns {ReadonlyArray<import("./types.js").SearchResult>}
 */
export function mergeResults(jsonHits, articleHits, limit) {
  /** @type {Map<string, Record<string, unknown>>} */
  const byDate = new Map();
  for (const hit of articleHits) {
    byDate.set(/** @type {string} */ (hit.date), { ...hit, source: "article" });
  }
  for (const hit of jsonHits) {
    const existing = byDate.get(hit.date);
    if (existing) {
      for (const [key, value] of Object.entries(hit)) {
        if (existing[key] === undefined) existing[key] = value;
      }
      existing.score = Math.max(/** @type {number} */ (existing.score), hit.score);
      existing.source = "both";
    } else {
      byDate.set(hit.date, { ...hit, source: "json" });
    }
  }
  return /** @type {import("./types.js").SearchResult[]} */ (
    [...byDate.values()]
      .toSorted((a, b) => b.score - a.score || b.date.localeCompare(a.date))
      .slice(0, limit)
  );
}
