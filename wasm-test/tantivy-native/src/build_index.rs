use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::Path;

use tantivy::collector::TopDocs;
use tantivy::query::QueryParser;
use tantivy::schema::*;
use tantivy::Index;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let pages_dir = Path::new("../../pages");
    let index_dir = Path::new("./tantivy_index");
    let blob_path = Path::new("./tantivy_index.blob");

    if index_dir.exists() {
        fs::remove_dir_all(index_dir)?;
    }
    fs::create_dir_all(index_dir)?;

    let mut schema_builder = Schema::builder();
    let date = schema_builder.add_text_field("date", STRING | STORED);
    let slug = schema_builder.add_text_field("slug", STRING | STORED);
    let blog_url = schema_builder.add_text_field("blog_url", STRING | STORED);
    let paper_title = schema_builder.add_text_field("paper_title", TEXT | STORED);
    let paper_authors = schema_builder.add_text_field("paper_authors", TEXT | STORED);
    let paper_year = schema_builder.add_i64_field("paper_year", INDEXED | STORED);
    let paper_venue = schema_builder.add_text_field("paper_venue", TEXT | STORED);
    let blog_summary = schema_builder.add_text_field("blog_summary", TEXT);
    let paper_abstract = schema_builder.add_text_field("paper_abstract", TEXT);
    let topics = schema_builder.add_text_field("topics", TEXT | STORED);
    let tags = schema_builder.add_text_field("tags", TEXT | STORED);
    let schema = schema_builder.build();

    let index = Index::create_in_dir(index_dir, schema.clone())?;
    let mut writer = index.writer(50_000_000)?;

    let json_files: Vec<_> = fs::read_dir(pages_dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map_or(false, |ext| ext == "json"))
        .collect();

    println!("Found {} JSON files", json_files.len());

    let mut count = 0;
    for jf in &json_files {
        let data: HashMap<String, serde_json::Value> =
            serde_json::from_str(&fs::read_to_string(jf)?)?;

        let get_str = |k: &str| -> String {
            data.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string()
        };
        let get_arr = |k: &str| -> String {
            data.get(k)
                .and_then(|v| v.as_array())
                .map(|arr| {
                    arr.iter()
                        .filter_map(|v| v.as_str())
                        .collect::<Vec<_>>()
                        .join(" ")
                })
                .unwrap_or_default()
        };
        let get_i64 = |k: &str| -> i64 {
            data.get(k).and_then(|v| v.as_i64()).unwrap_or(0)
        };

        let mut doc = tantivy::TantivyDocument::new();
        doc.add_text(date, get_str("date"));
        doc.add_text(slug, get_str("slug"));
        doc.add_text(blog_url, get_str("blog_url"));
        doc.add_text(paper_title, get_str("paper_title"));
        doc.add_text(paper_authors, get_arr("paper_authors"));
        doc.add_i64(paper_year, get_i64("paper_year"));
        doc.add_text(paper_venue, get_str("paper_venue"));
        doc.add_text(blog_summary, get_str("blog_summary"));
        doc.add_text(paper_abstract, get_str("paper_abstract"));
        doc.add_text(topics, get_arr("topics"));
        doc.add_text(tags, get_arr("tags"));
        writer.add_document(doc)?;
        count += 1;
    }

    writer.commit()?;
    println!("Indexed {} documents", count);

    // Test search
    let reader = index.reader()?;
    let searcher = reader.searcher();
    let query_parser = QueryParser::for_index(
        &index,
        vec![paper_title, paper_authors, blog_summary, paper_abstract, paper_venue],
    );

    for q in &["paxos consensus", "distributed transactions", "compiler optimization"] {
        let query = query_parser.parse_query(q)?;
        let top_docs = searcher.search(&query, &TopDocs::with_limit(5).order_by_score())?;
        println!("\nSearch: '{}' -> {} results", q, top_docs.len());
        for (score, doc_addr) in top_docs {
            let doc: tantivy::TantivyDocument = searcher.doc(doc_addr)?;
            let title = doc
                .get_first(paper_title)
                .and_then(|v| v.as_str())
                .unwrap_or("?");
            let date_val = doc.get_first(date).and_then(|v| v.as_str()).unwrap_or("?");
            println!("  [{}] {} (score: {:.3})", date_val, title, score);
        }
    }

    // Pack index into a single blob
    println!("\nPacking index to blob...");
    let mut blob = Vec::new();
    let mut manifest = Vec::new();

    for entry in fs::read_dir(index_dir)? {
        let entry = entry?;
        let path = entry.path();
        if path.is_file() {
            let filename = path.file_name().unwrap().to_string_lossy().to_string();
            let data = fs::read(&path)?;
            let offset = blob.len();
            blob.extend_from_slice(&data);
            manifest.push(format!("{}:{}:{}", filename, offset, data.len()));
        }
    }

    let manifest_bytes = manifest.join("\n");
    let manifest_len = manifest_bytes.len() as u32;

    let mut file = fs::File::create(blob_path)?;
    file.write_all(&manifest_len.to_le_bytes())?;
    file.write_all(manifest_bytes.as_bytes())?;
    file.write_all(&blob)?;

    let blob_size = fs::metadata(blob_path)?.len();
    println!(
        "Blob size: {} bytes ({:.2} MB)",
        blob_size,
        blob_size as f64 / 1_000_000.0
    );
    println!("Index dir size: {} bytes", total_dir_size(index_dir));

    Ok(())
}

fn total_dir_size(path: &Path) -> u64 {
    let mut total = 0;
    if let Ok(entries) = fs::read_dir(path) {
        for entry in entries.flatten() {
            let p = entry.path();
            if p.is_file() {
                total += p.metadata().map(|m| m.len()).unwrap_or(0);
            }
        }
    }
    total
}
