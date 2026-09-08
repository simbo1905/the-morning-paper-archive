use tantivy::collector::TopDocs;
use tantivy::query::{BooleanQuery, FuzzyTermQuery, Occur, Query, QueryParser};
use tantivy::schema::{Field, Value};
use tantivy::{Index, IndexReader, ReloadPolicy, Searcher, Term};

use crate::blob;

pub struct Hit {
    pub date: String,
    pub score: f32,
    pub shard_index: usize,
}

struct ShardEntry {
    index: Index,
    reader: IndexReader,
    date: Field,
    blog_title: Field,
    body: Field,
}

impl ShardEntry {
    fn open(blob_bytes: &[u8]) -> Result<ShardEntry, Box<dyn std::error::Error>> {
        let index = blob::open_index_from_blob(blob_bytes)?;
        let schema = index.schema();
        let date = schema.get_field("date")?;
        let blog_title = schema.get_field("blog_title")?;
        let body = schema.get_field("body")?;
        let reader = index
            .reader_builder()
            .reload_policy(ReloadPolicy::Manual)
            .try_into()?;
        Ok(ShardEntry {
            index,
            reader,
            date,
            blog_title,
            body,
        })
    }

    fn searcher(&self) -> Searcher {
        self.reader.searcher()
    }
}

/// Core searcher over N shard indexes, one in-memory Directory each.
/// Shared by the wasm-bindgen wrapper and the native mirror test.
pub struct SearcherCore {
    shards: Vec<ShardEntry>,
}

impl Default for SearcherCore {
    fn default() -> Self {
        Self::new()
    }
}

impl SearcherCore {
    pub fn new() -> SearcherCore {
        SearcherCore { shards: Vec::new() }
    }

    pub fn add_shard(&mut self, blob_bytes: &[u8]) -> Result<(), Box<dyn std::error::Error>> {
        self.shards.push(ShardEntry::open(blob_bytes)?);
        Ok(())
    }

    pub fn num_docs(&self) -> u64 {
        self.shards.iter().map(|s| s.searcher().num_docs()).sum()
    }

    /// Plain query (per-term union across fields, terms in conjunction),
    /// merged across shards, sorted by score desc, truncated to limit.
    pub fn search(&self, query: &str, limit: usize) -> Result<Vec<Hit>, Box<dyn std::error::Error>> {
        self.run_query(query, limit, None)
    }

    /// Fuzzy query: union of FuzzyTermQuery over every query field per shard
    /// (Levenshtein expansion comparable to the whoosh FuzzyTermPlugin ~dist).
    pub fn search_fuzzy(
        &self,
        query: &str,
        limit: usize,
        max_dist: u8,
    ) -> Result<Vec<Hit>, Box<dyn std::error::Error>> {
        self.run_query(query, limit, Some(max_dist))
    }

    fn run_query(
        &self,
        text: &str,
        limit: usize,
        fuzzy: Option<u8>,
    ) -> Result<Vec<Hit>, Box<dyn std::error::Error>> {
        let mut hits: Vec<Hit> = Vec::new();
        for (shard_index, shard) in self.shards.iter().enumerate() {
            let searcher = shard.searcher();
            let query: Box<dyn Query> = match fuzzy {
                Some(dist) => Box::new(BooleanQuery::new(
                    [shard.blog_title, shard.body]
                        .into_iter()
                        .map(|field| {
                            (
                                Occur::Should,
                                Box::new(FuzzyTermQuery::new(Term::from_field_text(field, text), dist, true))
                                    as Box<dyn Query>,
                            )
                        })
                        .collect(),
                )),
                None => {
                    let mut parser = QueryParser::for_index(&shard.index, vec![shard.blog_title, shard.body]);
                    parser.set_conjunction_by_default();
                    Box::new(parser.parse_query(text)?)
                }
            };
            for (score, addr) in searcher.search(&query, &TopDocs::with_limit(limit).order_by_score())? {
                let doc: tantivy::TantivyDocument = searcher.doc(addr)?;
                let date = doc
                    .get_first(shard.date)
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                hits.push(Hit { date, score, shard_index });
            }
        }
        // Stable sort keeps shard order then rank order on score ties.
        hits.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap_or(std::cmp::Ordering::Equal));
        hits.truncate(limit);
        Ok(hits)
    }
}

/// JSON wire format: [{date, score, shardIndex}].
pub fn hits_to_json(hits: &[Hit]) -> String {
    let values: Vec<serde_json::Value> = hits
        .iter()
        .map(|h| serde_json::json!({ "date": h.date, "score": h.score, "shardIndex": h.shard_index }))
        .collect();
    serde_json::to_string(&values).unwrap_or_else(|_| "[]".to_string())
}
