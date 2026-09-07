/**
 * @typedef {object} Figure
 * @property {string} number
 * @property {string} title
 * @property {string | null} caption
 */

/**
 * @typedef {object} PaperMeta
 * @property {string} date - YYYYMMDD format
 * @property {string} file_name - filename without extension
 * @property {string} slug - blog slug
 * @property {string} blog_url
 * @property {string} blog_title
 * @property {string} blog_summary
 * @property {string} paper_title
 * @property {ReadonlyArray<string>} paper_authors
 * @property {number} paper_year
 * @property {string} paper_venue
 * @property {string} paper_url
 * @property {string} paper_abstract
 * @property {string} paper_file
 * @property {ReadonlyArray<string>} topics
 * @property {ReadonlyArray<string>} tags
 * @property {string} extraction_method
 * @property {boolean} ocr_used
 * @property {ReadonlyArray<Figure>} [figures]
 */

/**
 * @typedef {object} SearchResult
 * @property {number} score
 * @property {string} date
 * @property {string} title
 * @property {ReadonlyArray<string>} authors
 * @property {number} year
 * @property {string} venue
 * @property {string} slug
 */

export {};
