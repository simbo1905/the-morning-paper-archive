//! wasm-bindgen Tantivy searcher: loads shard bins (blob manifest format),
//! queries all shards, merges results. Browser integration via
//! web/tantivy_search.js + web/tantivy-test.html.

mod blob;
mod searcher;

use wasm_bindgen::prelude::*;

use searcher::hits_to_json;

#[wasm_bindgen]
pub struct TantivySearcher {
    core: searcher::SearcherCore,
}

#[wasm_bindgen]
impl TantivySearcher {
    #[wasm_bindgen(constructor)]
    pub fn new() -> Result<TantivySearcher, JsValue> {
        Ok(TantivySearcher {
            core: searcher::SearcherCore::new(),
        })
    }

    /// Add one shard from its packed .bin bytes (blob manifest format).
    pub fn add_shard(&mut self, bytes: &[u8]) -> Result<(), JsValue> {
        self.core
            .add_shard(bytes)
            .map_err(|e| JsValue::from_str(&e.to_string()))
    }

    /// Merged search across all shards: JSON [{date, score, shardIndex}].
    pub fn search(&self, query: &str, limit: usize) -> Result<String, JsValue> {
        self.core
            .search(query, limit)
            .map(|hits| hits_to_json(&hits))
            .map_err(|e| JsValue::from_str(&e.to_string()))
    }

    /// Fuzzy search with Levenshtein max distance per term (whoosh ~dist).
    pub fn search_fuzzy(&self, query: &str, limit: usize, max_dist: usize) -> Result<String, JsValue> {
        self.core
            .search_fuzzy(query, limit, max_dist as u8)
            .map(|hits| hits_to_json(&hits))
            .map_err(|e| JsValue::from_str(&e.to_string()))
    }

    /// Total docs across all shards.
    pub fn num_docs(&self) -> usize {
        self.core.num_docs() as usize
    }
}

#[cfg(test)]
mod tests {
    use tantivy_native::{battery, blob, md_corpus, shards};
    use tantivy_native::{JSONL_PATH, PAGES_DIR};

    use super::*;

    /// Native mirror test: proves the public API shape — init(bytes per
    /// shard), search(q, limit), search_fuzzy(q, limit, dist), num_docs —
    /// against the real corpus and the whoosh oracle bar.
    #[test]
    fn searcher_core_public_api_matches_oracle() {
        let corpus = md_corpus::load_md_corpus(std::path::Path::new(PAGES_DIR), std::path::Path::new(JSONL_PATH)).unwrap();
        let chunks = shards::partition(&corpus, 4);
        let mut bins: Vec<Vec<u8>> = Vec::new();
        for chunk in &chunks {
            let (files, docs) = shards::build_shard_files(chunk).unwrap();
            assert_eq!(docs as usize, chunk.len());
            bins.push(blob::pack_files(&files));
        }

        let mut searcher = TantivySearcher::new().unwrap();
        for b in &bins {
            searcher.add_shard(b).unwrap();
        }
        assert_eq!(searcher.num_docs(), 995);

        let oracle: std::collections::HashMap<&str, u64> = battery::ORACLE_MD_COUNTS.iter().copied().collect();
        for (label, text, fuzzy) in battery::QUERIES {
            let started = std::time::Instant::now();
            let json = match fuzzy {
                Some(dist) => searcher.search_fuzzy(text, 100_000, *dist as usize).unwrap(),
                None => searcher.search(text, 100_000).unwrap(),
            };
            let hits: Vec<serde_json::Value> = serde_json::from_str(&json).unwrap();
            let expected = *oracle.get(*label).unwrap();
            assert_eq!(hits.len() as u64, expected, "count mismatch for '{label}'");
            // Merged results must be score-descending and tagged per shard.
            for pair in hits.windows(2) {
                assert!(pair[0]["score"].as_f64().unwrap() >= pair[1]["score"].as_f64().unwrap());
            }
            for h in &hits {
                let i = h["shardIndex"].as_u64().unwrap();
                assert!(i < bins.len() as u64);
                assert!(h["date"].as_str().unwrap().len() == 8);
            }
            println!("{label}: {} hits in {:.2} ms", hits.len(), started.elapsed().as_secs_f64() * 1000.0);
        }
    }
}
