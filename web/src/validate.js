/**
 * Recursively freeze an object and all nested objects/arrays.
 * @param {unknown} value
 * @returns {unknown}
 */
export function structuralFreeze(value) {
  if (value && typeof value === "object") {
    if (!Array.isArray(value)) {
      Object.freeze(value);
    }
    const obj = /** @type {Record<string, unknown>} */ (value);
    for (const key of Object.keys(obj)) {
      structuralFreeze(obj[key]);
    }
    if (Array.isArray(value)) {
      Object.freeze(value);
    }
  }
  return value;
}

/**
 * Validate a paper metadata object against the JTD schema.
 * Simple runtime validator — checks required fields and types.
 * @param {unknown} data
 * @returns {data is import("./types.js").PaperMeta}
 */
export function validatePaperMeta(data) {
  if (!data || typeof data !== "object") return false;
  const obj = /** @type {Record<string, unknown>} */ (data);
  const required = [
    "date", "file_name", "slug", "blog_url", "blog_title", "blog_summary",
    "paper_title", "paper_authors", "paper_year", "paper_venue", "paper_url",
    "paper_abstract", "paper_file", "topics", "tags", "extraction_method", "ocr_used"
  ];
  for (const key of required) {
    if (!(key in obj)) return false;
  }
  if (typeof obj.date !== "string") return false;
  if (typeof obj.slug !== "string") return false;
  if (typeof obj.paper_title !== "string") return false;
  if (!Array.isArray(obj.paper_authors)) return false;
  if (typeof obj.paper_year !== "number") return false;
  if (!Array.isArray(obj.topics)) return false;
  if (!Array.isArray(obj.tags)) return false;
  // ocr_used is nullable in the JTD schema (paper-meta.jdt.json).
  if (obj.ocr_used !== null && typeof obj.ocr_used !== "boolean") return false;
  return true;
}
