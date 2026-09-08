use std::fs;
use std::path::Path;
use std::time::Instant;

use tantivy_native::blob;
use tantivy_native::md_corpus;
use tantivy_native::shards;
use tantivy_native::{JSONL_PATH, PAGES_DIR};

/// Shard the md article index into N chronological bins under web/tantivy/,
/// each packed in the blob manifest format, plus a manifest.json.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let n: usize = std::env::args()
        .nth(1)
        .and_then(|a| a.parse().ok())
        .unwrap_or(20);

    let corpus = md_corpus::load_md_corpus(Path::new(PAGES_DIR), Path::new(JSONL_PATH))?;
    assert_eq!(corpus.len(), tantivy_native::CORPUS_SIZE);
    let chunks = shards::partition(&corpus, n);

    let out_dir = Path::new("../../web/tantivy");
    fs::create_dir_all(out_dir)?;
    for entry in fs::read_dir(out_dir)? {
        let path = entry?.path();
        if path.extension().map_or(false, |ext| ext == "bin") {
            fs::remove_file(&path)?;
        }
    }

    let s = md_corpus::md_schema();
    let schema_json: Vec<serde_json::Value> = s
        .schema
        .fields()
        .map(|(_, entry)| {
            serde_json::json!({
                "name": entry.name(),
                "type": entry.field_type().value_type().name(),
                "indexed": entry.is_indexed(),
                "stored": entry.is_stored(),
            })
        })
        .collect();

    let start = Instant::now();
    let mut shards_json = Vec::with_capacity(chunks.len());
    let mut total_bytes = 0usize;
    let mut total_docs = 0usize;
    for (i, chunk) in chunks.iter().enumerate() {
        let (files, docs) = shards::build_shard_files(chunk)?;
        let packed = blob::pack_files(&files);
        let file = format!("shard-{i:02}.bin");
        fs::write(out_dir.join(&file), &packed)?;
        total_bytes += packed.len();
        total_docs += chunk.len();
        shards_json.push(serde_json::json!({
            "file": file,
            "docs": chunk.len(),
            "firstDate": chunk.first().unwrap().date,
            "lastDate": chunk.last().unwrap().date,
            "bytes": packed.len(),
        }));
        println!("{file}: {} bytes, {} docs, {} .. {}", packed.len(), docs, chunk.first().unwrap().date, chunk.last().unwrap().date);
    }

    let manifest = serde_json::json!({
        "totalDocs": total_docs,
        "numShards": chunks.len(),
        "schema": schema_json,
        "shards": shards_json,
    });
    fs::write(out_dir.join("manifest.json"), serde_json::to_string_pretty(&manifest)?)?;

    println!(
        "Packed {} shards, {} docs, {} bytes total -> {} in {:.3?}",
        chunks.len(),
        total_docs,
        total_bytes,
        out_dir.display(),
        start.elapsed(),
    );
    Ok(())
}
