use std::fs;
use std::path::Path;
use std::time::Instant;

use tantivy_native::{md_corpus, CORPUS_SIZE, JSONL_PATH, MD_INDEX_DIR, PAGES_DIR};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let start = Instant::now();
    let corpus = md_corpus::load_md_corpus(Path::new(PAGES_DIR), Path::new(JSONL_PATH))?;
    let load_time = start.elapsed();
    println!("Reachable md corpus: {} docs (jsonl∩pages scan {:.3?})", corpus.len(), load_time);
    assert_eq!(corpus.len(), CORPUS_SIZE, "reachable corpus must be exactly {CORPUS_SIZE}");

    let index_dir = Path::new(MD_INDEX_DIR);
    let index = md_corpus::build_md_index(index_dir, &corpus)?;
    let build_time = start.elapsed() - load_time;

    let num_docs = index.reader()?.searcher().num_docs();
    let dir_size: u64 = fs::read_dir(index_dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.metadata().map(|m| m.len()).unwrap_or(0))
        .sum();
    println!("Built article index: {} docs in {:.3?} ({} files, {} bytes, {:.2} MB)",
        num_docs,
        build_time,
        fs::read_dir(index_dir)?.count(),
        dir_size,
        dir_size as f64 / 1_000_000.0,
    );
    Ok(())
}
