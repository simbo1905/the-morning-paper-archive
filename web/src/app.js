/**
 * Main SPA application module.
 * Handles: loading overlay, data loading, tag autocomplete, search, infinite scroll, routing.
 */

import { hasData, storePapers } from "./db.js";
import { search, fuzzySearch, filterByTags, getAllTags, getCachedResults, initArticleSearch, getTagCounts } from "./search.js";
import { fetchTextWithProgress, ProgressReporter } from "./progress.js";
import { structuralFreeze, validatePaperMeta } from "./validate.js";

/** @type {ReadonlyArray<string>} */
let selectedTags = [];

/** @type {ReadonlyArray<import("./types.js").PaperMeta>} */
let currentResults = [];

/** @type {number} */
let visibleCount = 0;

/** @type {boolean} */
let firstQueryLogged = false;

/** @type {number} */
let jsonlExpected = 0;

const PAGE_SIZE = 20;

/**
 * Initialize the SPA.
 */
export async function init() {
  const t0 = performance.now();
  const reporter = new ProgressReporter(setProgress);
  // Declare the full expected payload up front (shards + JSONL) so the bar
  // never jumps backwards when a later phase reports its size.
  try {
    const head = await fetch(new URL("./wasm-test/search_data.jsonl", document.baseURI).href, { method: "HEAD" });
    if (head.ok) {
      jsonlExpected = Number(head.headers.get("content-length")) || 0;
      if (jsonlExpected) reporter.expect(jsonlExpected);
    }
  } catch {
    // JSONL size declared lazily in loadData on the first progress event
  }
  try {
    // Phase: shards + WASM init. If tantivy fails we continue JSON-only,
    // loudly — no silent degradation, no fake results.
    setLoadingStatus("Loading Tantivy article index...");
    console.log("[phase] shards");
    /** @type {{numDocs: number, initMs: number, loadMs: number, shardCount: number} | null} */
    let tantivyInfo = null;
    try {
      tantivyInfo = await initArticleSearch({ reporter });
    } catch (err) {
      console.warn("[tantivy] article index failed to load — running JSON only:", err);
    }

    // Phase: JSONL metadata (first visit only; IndexedDB hit on warm visits).
    if (!(await hasData())) {
      setLoadingStatus("Fetching paper metadata...");
      console.log("[phase] JSONL");
      await loadData(reporter);
    } else {
      console.log("[phase] IndexedDB warm: JSONL fetch skipped");
    }
    console.log("[phase] IndexedDB ready");

    updateSearchMode(tantivyInfo);

    // Setup tag autocomplete
    setupTagAutocomplete();

    // Setup search
    setupSearch();

    // Setup fuzzy search toggle
    setupFuzzyToggle();

    // Setup routing
    setupRouting();

    // Initial search (empty = show all)
    await doSearch("");

    // Hide status
    const status = document.getElementById("status");
    if (status) status.style.display = "none";

    // Setup word cloud
    setupWordCloud();

    hideLoading();
    console.log(`[timing] load + init + first query in ${(performance.now() - t0).toFixed(0)} ms`);
  } catch (err) {
    showLoadingError(err);
  }
}

/**
 * Load paper metadata from the JSONL file and store in IndexedDB.
 * @param {import("./progress.js").ProgressReporter} reporter
 */
async function loadData(reporter) {
  const url = new URL("./wasm-test/search_data.jsonl", document.baseURI).href;
  let last = 0;
  const text = await fetchTextWithProgress(url, (received, total) => {
    if (total && !jsonlExpected) {
      jsonlExpected = total;
      reporter.expect(total);
    }
    reporter.add(received - last);
    last = received;
  });
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
  console.log(`[phase] IndexedDB: stored ${papers.length} papers`);
}

/**
 * Update the search mode indicator. It must never claim a mode that is not
 * running: both sources are real and always visible.
 * @param {{numDocs: number, initMs: number, loadMs: number, shardCount: number} | null} info
 */
function updateSearchMode(info) {
  const indicator = document.getElementById("search-mode");
  if (!indicator) return;
  if (info) {
    indicator.textContent = "JSON + Tantivy articles";
    indicator.className = "search-mode tantivy";
    indicator.title = `${info.numDocs} article docs in ${info.shardCount} shards, index open in ${info.initMs.toFixed(0)} ms`;
  } else {
    indicator.textContent = "JSON only";
    indicator.className = "search-mode json-only";
    indicator.title = "Tantivy article index unavailable — metadata search only";
  }
}

/**
 * Set the loading overlay progress percentage.
 * @param {number} percent
 */
function setProgress(percent) {
  const bar = document.getElementById("loading-bar");
  const label = document.getElementById("loading-percent");
  if (bar) bar.style.width = `${percent}%`;
  if (label) label.textContent = `${percent}%`;
}

/**
 * @param {string} text
 */
function setLoadingStatus(text) {
  const status = document.getElementById("loading-status");
  if (status) status.textContent = text;
}

/**
 * Hide the overlay and reveal the main UI once everything is loaded.
 */
function hideLoading() {
  const overlay = document.getElementById("loading");
  const mainView = document.getElementById("main-view");
  if (overlay) overlay.style.display = "none";
  if (mainView) mainView.style.display = "";
}

/**
 * Show the error in the overlay — nothing half-loaded is ever shown.
 * @param {unknown} err
 */
function showLoadingError(err) {
  const overlay = document.getElementById("loading");
  const mainView = document.getElementById("main-view");
  if (mainView) mainView.style.display = "none";
  if (overlay) overlay.classList.add("loading-error");
  setLoadingStatus(`Error: ${err instanceof Error ? err.message : String(err)}`);
  console.error("[loading] failed:", err);
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

/** @type {boolean} */
let fuzzyMode = false;

/**
 * Setup fuzzy search toggle.
 */
function setupFuzzyToggle() {
  const toggle = document.getElementById("fuzzy-toggle");
  if (toggle) {
    toggle.addEventListener("change", () => {
      fuzzyMode = toggle.checked;
      const input = document.getElementById("search-input");
      if (input) doSearch(input.value);
    });
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
  if (fuzzyMode && query.trim()) {
    await fuzzySearch(query, 500, 2);
  } else {
    await search(query, 500);
  }
  await reapplyFilters();
  const elapsed = performance.now() - t0;
  if (timing) timing.textContent = `${currentResults.length} results in ${elapsed.toFixed(1)}ms`;
  if (!firstQueryLogged) {
    firstQueryLogged = true;
    console.log(`[timing] first query in ${elapsed.toFixed(1)} ms`);
  }
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
 * Setup ECharts word cloud for tags.
 */
async function setupWordCloud() {
  const chartDiv = document.getElementById("word-cloud");
  if (!chartDiv) return;

  const tagCounts = await getTagCounts();
  const topTags = tagCounts.slice(0, 100);

  // Load ECharts from CDN
  const script = document.createElement("script");
  script.src = "https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js";
  script.onload = () => {
    const wordcloudScript = document.createElement("script");
    wordcloudScript.src = "https://cdn.jsdelivr.net/npm/echarts-wordcloud@2/dist/echarts-wordcloud.min.js";
    wordcloudScript.onload = () => {
      renderWordCloud(chartDiv, topTags);
    };
    document.head.appendChild(wordcloudScript);
  };
  document.head.appendChild(script);
}

/**
 * Render the word cloud.
 * @param {HTMLElement} container
 * @param {ReadonlyArray<{name: string, value: number}>} data
 */
function renderWordCloud(container, data) {
  // @ts-ignore - ECharts loaded from CDN
  const chart = echarts.init(container);
  chart.setOption({
    tooltip: { show: true },
    series: [{
      type: "wordCloud",
      shape: "circle",
      left: "center",
      top: "center",
      width: "90%",
      height: "90%",
      sizeRange: [12, 40],
      rotationRange: [-30, 30],
      gridSize: 8,
      drawOutOfBound: false,
      textStyle: {
        fontFamily: "sans-serif",
        fontWeight: "bold",
        color: () => {
          const colors = ["#0066cc", "#1a73e8", "#0066cc", "#4285f4", "#1a73e8"];
          return colors[Math.floor(Math.random() * colors.length)];
        },
      },
      emphasis: {
        textStyle: {
          shadowBlur: 10,
          shadowColor: "rgba(0,0,0,0.3)",
        },
      },
      data: data.map(d => ({
        name: d.name,
        value: d.value,
      })),
    }],
  });

  // Click to add tag
  chart.on("click", (/** @type {any} */ params) => {
    if (params.name && !selectedTags.includes(params.name)) {
      addTag(params.name);
    }
  });
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
