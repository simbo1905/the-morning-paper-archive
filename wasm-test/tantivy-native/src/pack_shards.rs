use std::fs;
use std::io::Write;
use std::path::Path;
use std::time::Instant;

use flate2::write::GzEncoder;
use flate2::Compression;

use tantivy_native::blob;
use tantivy_native::md_corpus;
use tantivy_native::shards;
use tantivy_native::{JSONL_PATH, PAGES_DIR};

/// Gzip-compress bytes at level 9 with the pure-Rust backend (no shell-outs).
fn gz_bytes(data: &[u8]) -> Result<Vec<u8>, std::io::Error> {
    let mut enc = GzEncoder::new(Vec::new(), Compression::best());
    enc.write_all(data)?;
    enc.finish()
}

/// Shard the md article index into N chronological bins under web/tantivy/,
/// each packed in the blob manifest format. Writes BOTH rungs to disk:
/// shard-NN.bin (raw, the oracle) and shard-NN.bin.gz (wire, gzip -9), plus
/// manifest.json selecting the gzip rung and manifest.raw.json (raw-rung
/// test fixture, no encoding fields). Also emits the gzip'd JSONL next to
/// the source JSONL and records its wire/raw sizes in both manifests.
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
        let ext = path.extension().and_then(|e| e.to_str());
        if matches!(ext, Some("bin") | Some("gz")) {
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
    let mut raw_shards_json = Vec::with_capacity(chunks.len());
    let mut total_raw = 0usize;
    let mut total_wire = 0usize;
    let mut total_docs = 0usize;
    println!("| shard | docs | raw B | gzip B | ratio |");
    println!("|---|---|---|---|---|");
    for (i, chunk) in chunks.iter().enumerate() {
        let (files, docs) = shards::build_shard_files(chunk)?;
        let packed = blob::pack_files(&files);
        let raw_len = packed.len();
        let packed_gz = gz_bytes(&packed)?;
        let gz_len = packed_gz.len();
        let bin = format!("shard-{i:02}.bin");
        let gz = format!("shard-{i:02}.bin.gz");
        fs::write(out_dir.join(&bin), &packed)?;
        fs::write(out_dir.join(&gz), &packed_gz)?;
        total_raw += raw_len;
        total_wire += gz_len;
        total_docs += chunk.len();
        shards_json.push(serde_json::json!({
            "file": gz,
            "docs": chunk.len(),
            "firstDate": chunk.first().unwrap().date,
            "lastDate": chunk.last().unwrap().date,
            "bytes": gz_len,
            "rawBytes": raw_len,
            "encoding": "gzip",
        }));
        raw_shards_json.push(serde_json::json!({
            "file": bin,
            "docs": chunk.len(),
            "firstDate": chunk.first().unwrap().date,
            "lastDate": chunk.last().unwrap().date,
            "bytes": raw_len,
        }));
        println!(
            "| {bin} | {docs} | {raw_len} | {gz_len} | {:.3} |",
            gz_len as f64 / raw_len as f64
        );
    }

    // JSONL wire rung: gzip -9 next to the source JSONL.
    let jsonl_path = Path::new(JSONL_PATH).to_path_buf();
    let jsonl_raw = fs::read(&jsonl_path)?;
    let jsonl_gz = gz_bytes(&jsonl_raw)?;
    let jsonl_gz_path = jsonl_path
        .parent()
        .expect("jsonl has a parent")
        .join("search_data.jsonl.gz");
    fs::write(&jsonl_gz_path, &jsonl_gz)?;
    println!(
        "| {} | {} | {} | {:.3} |",
        jsonl_gz_path.file_name().and_then(|f| f.to_str()).unwrap_or("jsonl.gz"),
        jsonl_raw.len(),
        jsonl_gz.len(),
        jsonl_gz.len() as f64 / jsonl_raw.len() as f64
    );
    println!(
        "| TOTAL | {} | {} | {:.3} |",
        total_raw + jsonl_raw.len(),
        total_wire + jsonl_gz.len(),
        (total_wire + jsonl_gz.len()) as f64 / (total_raw + jsonl_raw.len()) as f64
    );

    let manifest = serde_json::json!({
        "totalDocs": total_docs,
        "numShards": chunks.len(),
        "totalBytes": total_wire,
        "totalRawBytes": total_raw,
        "jsonl": {
            "file": "search_data.jsonl.gz",
            "bytes": jsonl_gz.len(),
            "rawBytes": jsonl_raw.len(),
            "encoding": "gzip",
        },
        "schema": schema_json,
        "shards": shards_json,
    });
    fs::write(out_dir.join("manifest.json"), serde_json::to_string_pretty(&manifest)?)?;

    // Raw-rung fixture: no encoding fields, raw .bin files, no jsonl key —
    // the client then uses raw bytes directly (regression ladder rung).
    let raw_manifest = serde_json::json!({
        "totalDocs": total_docs,
        "numShards": chunks.len(),
        "schema": schema_json,
        "shards": raw_shards_json,
    });
    fs::write(out_dir.join("manifest.raw.json"), serde_json::to_string_pretty(&raw_manifest)?)?;

    println!(
        "Packed {} shards, {} docs, {} B raw -> {} B gzip -> {} in {:.3?}",
        chunks.len(),
        total_docs,
        total_raw,
        total_wire,
        out_dir.display(),
        start.elapsed(),
    );
    Ok(())
}
