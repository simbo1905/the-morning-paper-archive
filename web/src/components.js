/**
 * Web Components for the morning paper SPA.
 * Light-DOM, native semantic HTML, no shadow DOM.
 */

import { getPaper } from "./db.js";

/**
 * Paper card component — shows summary, abstract toggle, links to PDF and original.
 * @extends {HTMLElement}
 */
class PaperCard extends HTMLElement {
  connectedCallback() {
    this.classList.add("paper-card");
  }

  /**
   * @param {import("./types.js").PaperMeta} paper
   */
  set paper(paper) {
    this._paper = paper;
    this.render();
  }

  render() {
    if (!this._paper) return;
    const p = this._paper;
    const dateFormatted = `${p.date.slice(0, 4)}-${p.date.slice(4, 6)}-${p.date.slice(6, 8)}`;
    const authorsStr = p.paper_authors.slice(0, 3).join(", ") +
      (p.paper_authors.length > 3 ? " et al." : "");

    const tagPills = [...p.topics, ...p.tags].slice(0, 8)
      .map(t => `<span class="tag-pill">${t}</span>`).join("");

    const abstractHtml = p.paper_abstract
      ? `<details class="abstract"><summary>Abstract</summary><p>${escapeHtml(p.paper_abstract)}</p></details>`
      : "";

    const pdfLink = p.paper_file
      ? `<a href="./papers/${p.date.slice(0, 4)}/${p.date.slice(4, 6)}/${p.date.slice(6, 8)}/${p.slug}.pdf" target="_blank" rel="noopener">PDF</a>`
      : "";

    const paperLink = p.paper_url
      ? `<a href="${escapeHtml(p.paper_url)}" target="_blank" rel="noopener">Original</a>`
      : "";

    const blogLink = `<a href="#/blog/${p.date}" class="blog-link">Read blog post</a>`;

    this.innerHTML = `
      <article>
        <header>
          <h3>${escapeHtml(p.paper_title)}</h3>
          <p class="meta">
            <time datetime="${dateFormatted}">${dateFormatted}</time>
            ${authorsStr ? ` — ${escapeHtml(authorsStr)}` : ""}
            ${p.paper_year ? ` (${p.paper_year})` : ""}
            ${p.paper_venue ? ` — ${escapeHtml(p.paper_venue)}` : ""}
          </p>
        </header>
        <p class="summary">${escapeHtml(p.blog_summary)}</p>
        ${abstractHtml}
        <div class="tags">${tagPills}</div>
        <nav class="links">
          ${blogLink}
          ${pdfLink}
          ${paperLink}
        </nav>
      </article>
    `;
  }
}

customElements.define("paper-card", PaperCard);

/**
 * Tag pill component — clickable, removable.
 * @extends {HTMLElement}
 */
class TagPill extends HTMLElement {
  connectedCallback() {
    this.classList.add("tag-pill", "removable");
    this.setAttribute("role", "button");
    this.setAttribute("tabindex", "0");
  }

  /**
   * @param {string} tag
   */
  set tag(tag) {
    this._tag = tag;
    this.textContent = tag;
    this.setAttribute("aria-label", `Remove tag ${tag}`);
  }

  get tag() {
    return this._tag ?? "";
  }
}

customElements.define("tag-pill", TagPill);

/**
 * Blog post viewer — fetches MD from gh-pages and renders it.
 * @extends {HTMLElement}
 */
class BlogPost extends HTMLElement {
  /**
   * @param {string} date
   */
  async loadPost(date) {
    this.innerHTML = `<p class="loading">Loading blog post...</p>`;
    try {
      const response = await fetch(`./pages/${date}.md`);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const md = await response.text();
      this.renderMarkdown(md);
    } catch (err) {
      this.innerHTML = `<p class="error">Failed to load: ${escapeHtml(String(err))}</p>`;
    }
  }

  /**
   * @param {string} md
   */
  renderMarkdown(md) {
    // Strip frontmatter
    const content = md.replace(/^---[\s\S]*?---\s*/, "");
    // Simple markdown to HTML
    let html = escapeHtml(content);
    // Headings
    html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
    html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
    // Bold
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    // Italic
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    // Blockquotes
    html = html.replace(/^> (.+)$/gm, "<blockquote>$1</blockquote>");
    // Links
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    // Lists
    html = html.replace(/^- (.+)$/gm, "<li>$1</li>");
    html = html.replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>");
    // Paragraphs
    html = html.split("\n\n").map(block => {
      if (block.startsWith("<")) return block;
      return `<p>${block.replace(/\n/g, "<br>")}</p>`;
    }).join("\n");
    this.innerHTML = `<article class="blog-post">${html}</article>`;
  }
}

customElements.define("blog-post", BlogPost);

/**
 * @param {string} str
 * @returns {string}
 */
function escapeHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
