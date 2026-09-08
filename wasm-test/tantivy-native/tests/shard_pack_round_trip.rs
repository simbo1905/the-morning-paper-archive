use std::path::Path;

use tantivy::Index;

use tantivy_native::battery::{self, ORACLE_MD_COUNTS};
use tantivy_native::{blob, md_corpus, shards};
use tantivy_native::{CORPUS_SIZE, JSONL_PATH, PAGES_DIR};

/// Sharding must not change search results: pack a multi-shard index set into
/// the blob manifest format, load each shard bin into a Directory (lock-file
/// skip), query across shards, and the union of per-shard battery totals must
/// equal both the single-index results and the whoosh oracle bar.
#[test]
fn shard_pack_round_trip() {
    let corpus = md_corpus::load_md_corpus(Path::new(PAGES_DIR), Path::new(JSONL_PATH)).unwrap();
    assert_eq!(corpus.len(), CORPUS_SIZE, "reachable md corpus must be 995 docs");

    let chunks = shards::partition(&corpus, 4);
    assert_eq!(chunks.len(), 4);
    assert_eq!(chunks.iter().map(|c| c.len()).sum::<usize>(), CORPUS_SIZE);

    // Single in-memory index over the full corpus: the ground truth battery.
    let s = md_corpus::md_schema();
    let (full_files, full_docs) = shards::build_shard_files(&corpus).unwrap();
    assert_eq!(full_docs, CORPUS_SIZE as u64);
    let full_index = Index::open(blob::load_into_ram(&blob::pack_files(&full_files)).unwrap()).unwrap();
    let full_out = battery::run_battery(
        &full_index.reader().unwrap().searcher(),
        &[s.blog_title, s.body],
        Some(s.date),
    )
    .unwrap();

    let mut union: Vec<(String, usize)> = Vec::new();
    let mut num_docs = 0u64;
    for chunk in &chunks {
        let (files, docs) = shards::build_shard_files(chunk).unwrap();
        assert_eq!(docs as usize, chunk.len());
        let packed = blob::pack_files(&files);

        let ram = blob::load_into_ram(&packed).unwrap();
        let index = Index::open(ram).unwrap();
        let searcher = index.reader().unwrap().searcher();
        assert_eq!(searcher.num_docs(), docs);
        num_docs += searcher.num_docs();

        for o in battery::run_battery(&searcher, &[s.blog_title, s.body], Some(s.date)).unwrap() {
            match union.iter_mut().find(|(l, _)| *l == o.label) {
                Some((_, total)) => *total += o.total,
                None => union.push((o.label.clone(), o.total)),
            }
        }
    }
    assert_eq!(num_docs, CORPUS_SIZE as u64, "shards must cover every doc exactly once");

    for (label, oracle_total) in ORACLE_MD_COUNTS {
        let shard_total = union.iter().find(|(l, _)| l == label).map(|(_, t)| *t).unwrap_or(0);
        assert_eq!(shard_total as u64, *oracle_total, "shard union mismatch for '{label}'");
        let full_total = full_out.iter().find(|o| o.label == *label).unwrap().total;
        assert_eq!(full_total as u64, *oracle_total, "single-index mismatch for '{label}'");
    }
}
