/**
 * Search module — Tantivy article search merged with simple JSON contains.
 * The JSON part always runs; it is an honest visible search of the paper
 * metadata, not a fallback. Search results are cached in sessionStorage
 * for tag post-filtering.
 */

import { getAllPapers } from "./db.js";
import { mergeResults } from "./merge.js";
import { fetchWithProgress, fmtBytes } from "./progress.js";
import { decompressGzip } from "./decompress.js";

/** @type {import("./types.js").PaperMeta[] | null} */
let allPapers = null;

/** @type {ReadonlyArray<import("./types.js").SearchResult> | null} */
let lastResults = null;

/** @type {ReadonlyMap<string, import("./types.js").PaperMeta>} */
let papersByDate = new Map();

/** @type {any} */
let tantivySearcher = null;

/** @type {boolean} */
let tantivyReady = false;

/**
 * One packed shard as declared by the manifest. `bytes` is the wire size
 * (compressed when encoding = "gzip"); rawBytes is the size after
 * decompression and the blob handed to add_shard.
 * @typedef {Object} ManifestShard
 * @property {string} file
 * @property {number} docs
 * @property {string} firstDate
 * @property {string} lastDate
 * @property {number} bytes
 * @property {number} [rawBytes]
 * @property {string} [encoding]
 */

/**
 * Shard index manifest. totalBytes/totalRawBytes cover the shards only;
 * jsonl describes the metadata JSONL wire rung (gzip or absent for raw).
 * @typedef {Object} Manifest
 * @property {number} numShards
 * @property {number} totalDocs
 * @property {number} [totalBytes]
 * @property {number} [totalRawBytes]
 * @property {{file: string, bytes: number, rawBytes: number, encoding?: string}} [jsonl]
 * @property {ReadonlyArray<ManifestShard>} shards
 */

/** @type {Promise<Manifest> | null} */
let manifestPromise = null;

/**
 * Fetch (once) the shard index manifest. Shared with app.js so the JSONL
 * rung selection and the shard loader agree on one manifest.
 * @returns {Promise<Manifest>} parsed manifest.json
 */
export function fetchIndexManifest() {
  if (!manifestPromise) {
    manifestPromise = (async () => {
      const resp = await fetch(new URL("../tantivy/manifest.json", import.meta.url).href);
      if (!resp.ok) throw new Error(`HTTP ${resp.status} for tantivy/manifest.json`);
      return resp.json();
    })();
  }
  return manifestPromise;
}

/**
 * Load all papers from IndexedDB into memory.
 * @returns {Promise<ReadonlyArray<import("./types.js").PaperMeta>>}
 */
async function ensureLoaded() {
  if (!allPapers) {
    allPapers = [...(await getAllPapers())].toSorted((a, b) => b.date.localeCompare(a.date));
    papersByDate = new Map(allPapers.map(p => [p.date, p]));
  }
  return allPapers;
}

/**
 * Load the Tantivy article index: fetch the 20 packed shard files in
 * parallel (wire byte progress aggregated into the reporter — the wire
 * bytes ARE the compressed rung, so the progress bar tracks real network
 * time), decompress gzip shards client-side, then unpack them into a
 * TantivySearcher. The rung comes from the manifest: shards declaring
 * encoding "gzip" are decompressed before add_shard; shards without the
 * field are used raw. A gzip rung on a browser without DecompressionStream
 * throws DecompressionUnsupported — never a silent fallback.
 * @param {{ reporter?: import("./progress.js").ProgressReporter, onManifest?: (info: {wireBytes: number, rawBytes: number}) => void }} [hooks]
 * @returns {Promise<{numDocs: number, initMs: number, loadMs: number, shardCount: number}>}
 */
export async function initArticleSearch({ reporter, onManifest } = {}) {
  const t0 = performance.now();
  // Assets live next to this module (web/tantivy_search.js, web/tantivy/),
  // so resolve relative to import.meta.url — document.baseURI differs between
  // index.html (site root) and web/src-tests.html (web/).
  const mod = await import(new URL("../tantivy_search.js", import.meta.url).href);
  await mod.default();
  const manifest = await fetchIndexManifest();
  console.log(`[shards] ${manifest.numShards} shards, ${manifest.totalDocs} docs expected`);

  let wireTotal = 0;
  let rawTotal = 0;
  for (const shard of manifest.shards) {
    if (reporter) reporter.expect(shard.bytes);
    wireTotal += shard.bytes;
    rawTotal += shard.rawBytes ?? shard.bytes;
  }
  if (onManifest) onManifest({ wireBytes: wireTotal, rawBytes: rawTotal });

  const loadStart = performance.now();
  const buffers = await Promise.all(manifest.shards.map(async (shard) => {
    const url = new URL(`../tantivy/${shard.file}`, import.meta.url).href;
    const started = performance.now();
    let last = 0;
    const wire = await fetchWithProgress(url, (received) => {
      if (reporter) reporter.add(received - last);
      last = received;
    });
    const ms = performance.now() - started;
    const bytes = shard.encoding === "gzip" ? await decompressGzip(wire) : wire;
    console.log(`${shard.file}: ${fmtBytes(wire.length)}${shard.encoding === "gzip" ? ` wire (raw ${fmtBytes(bytes.length)})` : ""} in ${ms.toFixed(1)} ms`);
    return bytes;
  }));
  const loadMs = performance.now() - loadStart;
  console.log(`[shards] ${buffers.length} files, ${fmtBytes(buffers.reduce((sum, b) => sum + b.length, 0))} raw in ${loadMs.toFixed(1)} ms`);

  const initStart = performance.now();
  tantivySearcher = new mod.TantivySearcher();
  for (const bytes of buffers) tantivySearcher.add_shard(bytes);
  const initMs = performance.now() - initStart;
  const numDocs = tantivySearcher.num_docs();
  tantivyReady = true;
  console.log(`[wasm] tantivy ready: ${numDocs} docs, unpack + index open in ${initMs.toFixed(1)} ms (module import ${((performance.now() - t0) - loadMs - initMs).toFixed(1)} ms)`);
  return { numDocs, initMs, loadMs, shardCount: manifest.shards.length };
}

/**
 * Run search — merged results: tantivy article hits plus simpleJsonSearch
 * hits, deduped by date with max score. An empty query is a listing of all
 * papers, newest first (no search engines involved).
 * @param {string} query
 * @param {number} [limit]
 * @returns {Promise<ReadonlyArray<import("./types.js").SearchResult>>}
 */
export async function search(query, limit = 500) {
  const results = await runSearch(query, limit, 0);
  lastResults = results;
  cacheResults(results);
  return results;
}

/**
 * Fuzzy search — tantivy fuzzy article hits merged with the JSON contains
 * search the same way (the JSON part stays plain contains).
 * @param {string} query
 * @param {number} [limit]
 * @param {number} [editDistance]
 * @returns {Promise<ReadonlyArray<import("./types.js").SearchResult>>}
 */
export async function fuzzySearch(query, limit = 500, editDistance = 2) {
  const results = await runSearch(query, limit, editDistance);
  lastResults = results;
  cacheResults(results);
  return results;
}

/**
 * Shared implementation for search and fuzzySearch.
 * @param {string} query
 * @param {number} limit
 * @param {number} editDistance
 * @returns {Promise<ReadonlyArray<import("./types.js").SearchResult>>}
 */
async function runSearch(query, limit, editDistance) {
  await ensureLoaded();
  const q = query.trim();
  if (!q) {
    // Empty query = listing, not a search fallback. Never call tantivy.
    return allPapers.map(p => ({
      score: 1.0,
      date: p.date,
      title: p.paper_title,
      authors: p.paper_authors,
      year: p.paper_year,
      venue: p.paper_venue,
      slug: p.slug,
    }));
  }
  const jsonHits = await simpleJsonSearch(q, limit);
  if (!tantivyReady || !tantivySearcher) {
    return mergeResults(jsonHits, [], limit);
  }
  const articleHits = editDistance > 0
    ? enrich(articleSearch(tantivySearcher.search_fuzzy(q, limit, editDistance)))
    : enrich(articleSearch(tantivySearcher.search(q, limit)));
  return mergeResults(jsonHits, articleHits, limit);
}

/**
 * Tantivy article search over the full article body text.
 * @param {string} jsonStr
 * @returns {ReadonlyArray<{score: number, date: string, shardIndex: number}>}
 */
function articleSearch(jsonStr) {
  return JSON.parse(jsonStr);
}

/**
 * Simple, honest string-contains search over paper metadata
 * (title, authors, venue, summary, abstract, topics, tags).
 * @param {string} q lowercase query
 * @param {number} limit
 * @returns {Promise<ReadonlyArray<import("./types.js").SearchResult>>}
 */
async function simpleJsonSearch(q, limit) {
  const papers = await ensureLoaded();
  const needle = q.toLowerCase();
  return papers
    .filter(p => {
      const haystack = [
        p.paper_title, p.blog_summary, p.paper_abstract,
        p.paper_venue, p.paper_authors.join(" "),
        p.topics.join(" "), p.tags.join(" "),
      ].join(" ").toLowerCase();
      return haystack.includes(needle);
    })
    .slice(0, limit)
    .map(p => ({
      score: 1.0,
      date: p.date,
      title: p.paper_title,
      authors: p.paper_authors,
      year: p.paper_year,
      venue: p.paper_venue,
      slug: p.slug,
    }));
}

/**
 * Enrich tantivy hits with paper metadata (article dates can carry
 * "-2" suffix variants; the JSON layer has the same keys).
 * @param {ReadonlyArray<{score: number, date: string, shardIndex: number}>} hits
 * @returns {ReadonlyArray<{score: number, date: string, shardIndex: number, title?: string, authors?: ReadonlyArray<string>, year?: number, venue?: string, slug?: string}>}
 */
function enrich(hits) {
  return hits.map(hit => {
    const paper = papersByDate.get(hit.date);
    if (!paper) return hit;
    return {
      ...hit,
      title: paper.paper_title,
      authors: paper.paper_authors,
      year: paper.paper_year,
      venue: paper.paper_venue,
      slug: paper.slug,
    };
  });
}

/**
 * Cache search results in sessionStorage for tag post-filtering.
 * @param {ReadonlyArray<import("./types.js").SearchResult>} results
 */
function cacheResults(results) {
  sessionStorage.setItem("searchResults", JSON.stringify(results));
}

/**
 * Get cached search results from sessionStorage.
 * @returns {ReadonlyArray<import("./types.js").SearchResult>}
 */
export function getCachedResults() {
  if (lastResults) return lastResults;
  const cached = sessionStorage.getItem("searchResults");
  if (cached) {
    lastResults = JSON.parse(cached);
    return lastResults ?? [];
  }
  return [];
}

/**
 * Filter cached search results by tags.
 * @param {ReadonlyArray<string>} selectedTags
 * @returns {Promise<ReadonlyArray<import("./types.js").PaperMeta>>}
 */
export async function filterByTags(selectedTags) {
  const results = getCachedResults();
  const papers = await ensureLoaded();
  const dates = new Set(results.map(r => r.date));

  if (selectedTags.length === 0) {
    return papers.filter(p => dates.has(p.date));
  }

  return papers.filter(p => {
    if (!dates.has(p.date)) return false;
    const paperTags = new Set([...p.tags, ...p.topics]);
    return selectedTags.every(tag => paperTags.has(tag));
  });
}

/**
 * Get all unique tags from the currently loaded papers.
 * @returns {Promise<ReadonlyArray<string>>}
 */
export async function getAllTags() {
  const papers = await ensureLoaded();
  const tags = new Set();
  for (const p of papers) {
    for (const t of p.tags) tags.add(t);
    for (const t of p.topics) tags.add(t);
  }
  return [...tags].toSorted((a, b) => a.localeCompare(b));
}

/**
 * Get all unique topics (ontology domains) from loaded papers.
 * @returns {Promise<ReadonlyArray<string>>}
 */
export async function getAllTopics() {
  const papers = await ensureLoaded();
  const topics = new Set();
  for (const p of papers) {
    for (const t of p.topics) topics.add(t);
  }
  return [...topics].toSorted((a, b) => a.localeCompare(b));
}

/**
 * Get tag counts for word cloud visualization.
 * @returns {Promise<ReadonlyArray<{name: string, value: number}>>}
 */
export async function getTagCounts() {
  const papers = await ensureLoaded();
  const counts = new Map();
  for (const p of papers) {
    for (const t of p.tags) {
      counts.set(t, (counts.get(t) ?? 0) + 1);
    }
    for (const t of p.topics) {
      counts.set(t, (counts.get(t) ?? 0) + 1);
    }
  }
  return [...counts.entries()]
    .map(([name, value]) => ({ name, value }))
    .toSorted((a, b) => b.value - a.value);
}
