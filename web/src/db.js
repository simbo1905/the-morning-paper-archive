/**
 * IndexedDB layer for storing and retrieving paper metadata.
 * Papers are keyed by date (YYYYMMDD) for fast date-based lookups.
 */

const DB_NAME = "morning-paper";
const DB_VERSION = 1;
const STORE_NAME = "papers";

/** @type {IDBDatabase | null} */
let dbPromise = null;

/**
 * @returns {Promise<IDBDatabase>}
 */
function openDB() {
  if (dbPromise) return Promise.resolve(dbPromise);
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME);
      }
    };
    req.onsuccess = () => {
      dbPromise = req.result;
      resolve(req.result);
    };
    req.onerror = () => reject(req.error);
  });
}

/**
 * Store papers in IndexedDB, keyed by date.
 * @param {ReadonlyArray<import("./types.js").PaperMeta>} papers
 * @returns {Promise<void>}
 */
export async function storePapers(papers) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    for (const paper of papers) {
      store.put(paper, paper.date);
    }
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

/**
 * Get all papers from IndexedDB.
 * @returns {Promise<ReadonlyArray<import("./types.js").PaperMeta>>}
 */
export async function getAllPapers() {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const req = store.getAll();
    req.onsuccess = () => {
      const results = /** @type {import("./types.js").PaperMeta[]} */ (req.result ?? []);
      resolve(results);
    };
    req.onerror = () => reject(req.error);
  });
}

/**
 * Get a single paper by date.
 * @param {string} date - YYYYMMDD
 * @returns {Promise<import("./types.js").PaperMeta | null>}
 */
export async function getPaper(date) {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const req = store.get(date);
    req.onsuccess = () => resolve(req.result ?? null);
    req.onerror = () => reject(req.error);
  });
}

/**
 * Get all unique tags/topics from stored papers.
 * @returns {Promise<ReadonlyArray<string>>}
 */
export async function getAllTags() {
  const papers = await getAllPapers();
  const tags = new Set();
  for (const p of papers) {
    for (const t of p.tags) tags.add(t);
    for (const t of p.topics) tags.add(t);
  }
  return [...tags].toSorted((a, b) => a.localeCompare(b));
}

/**
 * Check if IndexedDB has data.
 * @returns {Promise<boolean>}
 */
export async function hasData() {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readonly");
    const store = tx.objectStore(STORE_NAME);
    const req = store.count();
    req.onsuccess = () => resolve(req.result > 0);
    req.onerror = () => reject(req.error);
  });
}

/**
 * Clear all data from IndexedDB.
 * @returns {Promise<void>}
 */
export async function clearData() {
  const db = await openDB();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, "readwrite");
    const store = tx.objectStore(STORE_NAME);
    store.clear();
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}
