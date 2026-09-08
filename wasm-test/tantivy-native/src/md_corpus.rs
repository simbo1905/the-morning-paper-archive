use std::collections::HashSet;
use std::fs;
use std::path::Path;

use tantivy::schema::*;
use tantivy::Index;

pub struct MdDoc {
    pub date: String,
    pub slug: String,
    pub blog_title: String,
    pub body: String,
}

pub struct MdSchema {
    pub schema: Schema,
    pub date: Field,
    pub slug: Field,
    pub blog_title: Field,
    pub body: Field,
}

/// Schema of the corrected article index: full-text over the blog body,
/// metadata kept for display. body is indexed NOT stored — the site serves
/// the .md file for rendering.
pub fn md_schema() -> MdSchema {
    let mut builder = Schema::builder();
    let date = builder.add_text_field("date", STRING | STORED);
    let slug = builder.add_text_field("slug", STRING | STORED);
    let blog_title = builder.add_text_field("blog_title", TEXT | STORED);
    let body = builder.add_text_field("body", TEXT);
    MdSchema {
        schema: builder.build(),
        date,
        slug,
        blog_title,
        body,
    }
}

/// Load the reachable md corpus: pages/*.md whose stem is a date in the
/// shipped JSONL. Mirrors scripts/whoosh_oracle.py parse_md exactly
/// (front-matter `---` block with blog_url/blog_title, body = the rest).
pub fn load_md_corpus(pages_dir: &Path, jsonl_path: &Path) -> Result<Vec<MdDoc>, Box<dyn std::error::Error>> {
    let text = fs::read_to_string(jsonl_path)?;
    let mut dates = HashSet::new();
    for line in text.lines() {
        if line.trim().is_empty() {
            continue;
        }
        let doc: serde_json::Value = serde_json::from_str(line)?;
        if let Some(d) = doc.get("date").and_then(|v| v.as_str()) {
            if !d.is_empty() {
                dates.insert(d.to_string());
            }
        }
    }

    let mut md_files: Vec<_> = fs::read_dir(pages_dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map_or(false, |ext| ext == "md"))
        .collect();
    md_files.sort();

    let mut docs = Vec::new();
    for path in md_files {
        let stem = path.file_stem().unwrap().to_string_lossy().to_string();
        if !dates.contains(&stem) {
            continue;
        }
        let text = fs::read_to_string(&path)?;
        let lines: Vec<&str> = text.lines().collect();
        let mut meta = std::collections::HashMap::new();
        let body;
        if lines.first().map_or(false, |l| l.trim() == "---") {
            let end = lines[1..]
                .iter()
                .position(|l| l.trim() == "---")
                .map(|i| i + 1);
            match end {
                Some(end) => {
                    for line in lines[1..end].iter() {
                        let (key, value) = line.trim().split_once(':').unwrap_or(("", ""));
                        if !key.trim().is_empty() {
                            meta.insert(key.trim().to_string(), value.trim().to_string());
                        }
                    }
                    body = lines[end + 1..].join("\n");
                }
                None => body = text.clone(),
            }
        } else {
            body = text.clone();
        }
        let blog_url = meta.get("blog_url").cloned().unwrap_or_default();
        let slug = blog_url.trim_end_matches('/').rsplit('/').next().unwrap_or("").to_string();
        docs.push(MdDoc {
            date: stem,
            slug,
            blog_title: meta.get("blog_title").cloned().unwrap_or_default(),
            body,
        });
    }
    Ok(docs)
}

/// Build the article index into a real disk directory (one commit, one
/// segment-merge-friendly writer heap). Removes any pre-existing index there.
pub fn build_md_index(index_dir: &Path, corpus: &[MdDoc]) -> Result<Index, Box<dyn std::error::Error>> {
    let s = md_schema();
    if index_dir.exists() {
        fs::remove_dir_all(index_dir)?;
    }
    fs::create_dir_all(index_dir)?;
    let index = Index::create_in_dir(index_dir, s.schema.clone())?;
    let mut writer = index.writer(50_000_000)?;
    for doc_src in corpus {
        let mut doc = tantivy::TantivyDocument::new();
        doc.add_text(s.date, &doc_src.date);
        doc.add_text(s.slug, &doc_src.slug);
        doc.add_text(s.blog_title, &doc_src.blog_title);
        doc.add_text(s.body, &doc_src.body);
        writer.add_document(doc)?;
    }
    writer.commit()?;
    drop(writer);
    Ok(index)
}
