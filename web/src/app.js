/**
 * Main SPA application module.
 * Handles: data loading, tag autocomplete, search, infinite scroll, routing.
 */

import { hasData, storePapers, getAllPapers, clearData } from "./db.js";
import { dumbSearch, filterByTags, getAllTags, getCachedResults } from "./search.js";
import { structuralFreeze, validatePaperMeta } from "./validate.js";

/** @type {ReadonlyArray<string>} */
let selectedTags = [];

/** @type {ReadonlyArray<import("./types.js").PaperMeta>} */
let currentResults = [];

/** @type {number} */
let visibleCount = 0;

const PAGE_SIZE = 20;

/**
 * Initialize the SPA.
 */
export async function init() {
  const status = document.getElementById("status");
  if (status) status.textContent = "Loading data...";

  // Check if data is in IndexedDB
  const has = await hasData();
  if (!has) {
    if (status) status.textContent = "Fetching paper metadata...";
    await loadData();
  }

  if (status) status.textContent = "Ready";

  // Setup tag autocomplete
  setupTagAutocomplete();

  // Setup search
  setupSearch();

  // Setup routing
  setupRouting();

  // Initial search (empty = show all)
  await doSearch("");

  // Hide status
  if (status) status.style.display = "none";
}

/**
 * Load paper metadata from the JSONL file and store in IndexedDB.
 */
async function loadData() {
  const response = await fetch("../wasm-test/search_data.jsonl");
  if (!response.ok) throw new Error(`Failed to fetch data: ${response.status}`);
  const text = await response.text();
  const papers = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    try {
      const data = JSON.parse(line);
      if (validatePaperMeta(data)) {
        papers.push(structuralFreeze(data));
      }
    } catch {
      // skip invalid
    }
  }
  await storePapers(papers);
}

/**
 * Setup tag autocomplete with datalist.
 */
async function setupTagAutocomplete() {
  const tags = await getAllTags();
  const datalist = document.getElementById("tag-list");
  if (datalist) {
    datalist.innerHTML = tags.map(t => `<option value="${t}">`).join("");
  }

  const tagInput = document.getElementById("tag-input");
  if (tagInput) {
    tagInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === "Tab") {
        const value = tagInput.value.trim();
        if (value && !selectedTags.includes(value)) {
          addTag(value);
          tagInput.value = "";
          e.preventDefault();
        }
      }
    });
  }
}

/**
 * Add a tag to the selected tags.
 * @param {string} tag
 */
function addTag(tag) {
  selectedTags = [...selectedTags, tag];
  renderTagPills();
  reapplyFilters();
}

/**
 * Remove a tag from selected tags.
 * @param {string} tag
 */
function removeTag(tag) {
  selectedTags = selectedTags.filter(t => t !== tag);
  renderTagPills();
  reapplyFilters();
}

/**
 * Render tag pills for selected tags.
 */
function renderTagPills() {
  const container = document.getElementById("selected-tags");
  if (!container) return;
  container.innerHTML = "";
  for (const tag of selectedTags) {
    const pill = document.createElement("tag-pill");
    pill.tag = tag;
    pill.addEventListener("click", () => removeTag(tag));
    pill.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === "Delete") removeTag(tag);
    });
    container.appendChild(pill);
  }
}

/**
 * Setup search input.
 */
function setupSearch() {
  const input = document.getElementById("search-input");
  if (input) {
    let debounceTimer;
    input.addEventListener("input", () => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => doSearch(input.value), 300);
    });
  }
}

/**
 * Run search and display results.
 * @param {string} query
 */
async function doSearch(query) {
  const timing = document.getElementById("timing");
  const t0 = performance.now();
  await dumbSearch(query, 500);
  await reapplyFilters();
  const elapsed = performance.now() - t0;
  if (timing) timing.textContent = `${currentResults.length} results in ${elapsed.toFixed(1)}ms`;
}

/**
 * Reapply tag filters to cached search results.
 */
async function reapplyFilters() {
  currentResults = await filterByTags(selectedTags);
  visibleCount = 0;
  renderResults();
}

/**
 * Render results with infinite scroll.
 */
function renderResults() {
  const container = document.getElementById("results");
  if (!container) return;

  const toShow = currentResults.slice(0, visibleCount + PAGE_SIZE);
  visibleCount = toShow.length;

  container.innerHTML = "";
  for (const paper of toShow) {
    const card = document.createElement("paper-card");
    card.paper = paper;
    container.appendChild(card);
  }

  // Update count
  const count = document.getElementById("result-count");
  if (count) count.textContent = `${toShow.length} / ${currentResults.length}`;
}

/**
 * Setup infinite scroll via IntersectionObserver.
 */
export function setupInfiniteScroll() {
  const sentinel = document.getElementById("scroll-sentinel");
  if (!sentinel) return;

  const observer = new IntersectionObserver((entries) => {
    if (entries[0].isIntersecting && visibleCount < currentResults.length) {
      renderResults();
    }
  }, { rootMargin: "200px" });

  observer.observe(sentinel);
}

/**
 * Simple hash-based routing.
 */
function setupRouting() {
  window.addEventListener("hashchange", handleRoute);
  handleRoute();
}

function handleRoute() {
  const hash = window.location.hash;
  const blogMatch = hash.match(/^#\/blog\/(\d{8})/);
  if (blogMatch) {
    showBlogPost(blogMatch[1]);
  } else {
    showMainView();
  }
}

function showMainView() {
  const blogView = document.getElementById("blog-view");
  const mainView = document.getElementById("main-view");
  if (blogView) blogView.style.display = "none";
  if (mainView) mainView.style.display = "";
}

/**
 * Show a blog post.
 * @param {string} date
 */
function showBlogPost(date) {
  const mainView = document.getElementById("main-view");
  const blogView = document.getElementById("blog-view");
  if (mainView) mainView.style.display = "none";
  if (blogView) blogView.style.display = "";

  const post = document.getElementById("blog-post");
  if (post && "loadPost" in post) {
    /** @type {import("./components.js").BlogPost} */ (post).loadPost(date);
  }
}
