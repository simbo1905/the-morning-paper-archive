/**
 * Search module — WASM BM25 search with fallback to dumb string-contains.
 * Search results are cached in sessionStorage for tag post-filtering.
 */

import { getAllPapers } from "./db.js";

/** @type {import("./types.js").PaperMeta[] | null} */
let allPapers = null;

/** @type {ReadonlyArray<import("./types.js").SearchResult> | null} */
let lastResults = null;

/** @type {any} */
let wasmSearcher = null;

/** @type {boolean} */
let wasmReady = false;

/** @type {string} */
let searchMode = "dumb";

/**
 * Load all papers from IndexedDB into memory.
 * @returns {Promise<ReadonlyArray<import("./types.js").PaperMeta>>}
 */
async function ensureLoaded() {
  if (!allPapers) {
    allPapers = [...(await getAllPapers())].toSorted((a, b) => b.date.localeCompare(a.date));
  }
  return allPapers;
}

/**
 * Try to load the WASM search module.
 * @returns {Promise<void>}
 */
export async function initWasm() {
  try {
    // Use absolute path relative to document for GitHub Pages compatibility
    const basePath = new URL("./web/simple_search.js", document.baseURI).href;
    const wasmModule = await import(/* @vite-ignore */ basePath);
    await wasmModule.default();
    const response = await fetch("./wasm-test/search_data.jsonl");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.text();
    const t0 = performance.now();
    wasmSearcher = new wasmModule.WasmSearcher(data);
    const buildTime = performance.now() - t0;
    wasmReady = true;
    searchMode = "wasm";
    console.log(`WASM search ready: ${wasmSearcher.num_docs()} docs, built in ${buildTime.toFixed(1)}ms`);
    const indicator = document.getElementById("search-mode");
    if (indicator) indicator.title = `WASM BM25: ${wasmSearcher.num_docs()} docs, built in ${buildTime.toFixed(1)}ms`;
  } catch (err) {
    console.log("WASM search unavailable, using dumb search:", err.message);
    searchMode = "dumb";
    const indicator = document.getElementById("search-mode");
    if (indicator) indicator.title = `WASM failed: ${err.message}`;
  }
}

/**
 * Run search — uses WASM if available, falls back to dumb string-contains.
 * @param {string} query
 * @param {number} limit
 * @returns {Promise<ReadonlyArray<import("./types.js").SearchResult>>}
 */
export async function search(query, limit = 500) {
  if (!query.trim()) {
    return dumbSearch(query, limit);
  }
  if (wasmReady && wasmSearcher) {
    return wasmSearch(query, limit);
  }
  return dumbSearch(query, limit);
}

/**
 * WASM BM25 search.
 * @param {string} query
 * @param {number} limit
 * @returns {Promise<ReadonlyArray<import("./types.js").SearchResult>>}
 */
async function wasmSearch(query, limit) {
  const jsonStr = wasmSearcher.search(query, limit);
  const results = JSON.parse(jsonStr);
  lastResults = results;
  cacheResults(results);
  return results;
}

/**
 * Dumb string-contains search over paper metadata.
 * @param {string} query
 * @param {number} limit
 * @returns {Promise<ReadonlyArray<import("./types.js").SearchResult>>}
 */
export async function dumbSearch(query, limit = 500) {
  const papers = await ensureLoaded();
  const q = query.toLowerCase().trim();
  if (!q) {
    const results = papers.slice(0, limit).map(p => ({
      score: 1.0,
      date: p.date,
      title: p.paper_title,
      authors: p.paper_authors,
      year: p.paper_year,
      venue: p.paper_venue,
      slug: p.slug,
    }));
    lastResults = results;
    cacheResults(results);
    return results;
  }

  const results = papers
    .filter(p => {
      const haystack = [
        p.paper_title, p.blog_summary, p.paper_abstract,
        p.paper_venue, p.paper_authors.join(" "),
        p.topics.join(" "), p.tags.join(" "),
      ].join(" ").toLowerCase();
      return haystack.includes(q);
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

  lastResults = results;
  cacheResults(results);
  return results;
}

/**
 * Fuzzy search via WASM.
 * @param {string} query
 * @param {number} limit
 * @param {number} editDistance
 * @returns {Promise<ReadonlyArray<import("./types.js").SearchResult>>}
 */
export async function fuzzySearch(query, limit = 500, editDistance = 2) {
  if (wasmReady && wasmSearcher) {
    const jsonStr = wasmSearcher.search_fuzzy(query, limit, editDistance);
    const results = JSON.parse(jsonStr);
    lastResults = results;
    cacheResults(results);
    return results;
  }
  return dumbSearch(query, limit);
}

/**
 * Get the current search mode.
 * @returns {string}
 */
export function getSearchMode() {
  return searchMode;
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
