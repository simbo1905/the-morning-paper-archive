/**
 * Search module — starts with dumb string-contains, upgrades to WASM when available.
 * Search results are cached in sessionStorage for tag post-filtering.
 */

import { getAllPapers } from "./db.js";

/** @type {import("./types.js").PaperMeta[] | null} */
let allPapers = null;

/** @type {ReadonlyArray<import("./types.js").SearchResult> | null} */
let lastResults = null;

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
 * Dumb string-contains search over paper metadata.
 * @param {string} query
 * @param {number} limit
 * @returns {Promise<ReadonlyArray<import("./types.js").SearchResult>>}
 */
export async function dumbSearch(query, limit = 50) {
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
  if (selectedTags.length === 0) {
    const papers = await ensureLoaded();
    const dates = new Set(results.map(r => r.date));
    return papers.filter(p => dates.has(p.date));
  }

  const papers = await ensureLoaded();
  const dates = new Set(results.map(r => r.date));
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
