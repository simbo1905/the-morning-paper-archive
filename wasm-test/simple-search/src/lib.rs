use std::collections::HashMap;
use wasm_bindgen::prelude::*;

/// A simple inverted index for full-text search in WASM.
/// Supports: BM25 scoring, fuzzy search (Levenshtein distance), field filtering.
/// No external dependencies beyond serde and wasm-bindgen.

#[derive(serde::Deserialize, serde::Serialize, Clone)]
struct Document {
    date: String,
    slug: String,
    paper_title: String,
    paper_authors: Vec<String>,
    paper_year: i64,
    paper_venue: String,
    blog_summary: String,
    paper_abstract: String,
    topics: Vec<String>,
    tags: Vec<String>,
}

struct Posting {
    doc_id: usize,
    tf: f32,
}

struct IndexData {
    docs: Vec<Document>,
    inverted: HashMap<String, Vec<Posting>>,
    doc_lengths: Vec<f32>,
    avg_doc_length: f32,
    num_docs: usize,
}

fn tokenize(text: &str) -> Vec<String> {
    text.to_lowercase()
        .split(|c: char| !c.is_alphanumeric())
        .filter(|s| !s.is_empty() && s.len() > 1)
        .map(|s| s.to_string())
        .collect()
}

fn levenshtein(a: &str, b: &str) -> usize {
    let a_chars: Vec<char> = a.chars().collect();
    let b_chars: Vec<char> = b.chars().collect();
    let (m, n) = (a_chars.len(), b_chars.len());
    if m == 0 {
        return n;
    }
    if n == 0 {
        return m;
    }
    let mut prev: Vec<usize> = (0..=n).collect();
    let mut curr = vec![0usize; n + 1];
    for i in 1..=m {
        curr[0] = i;
        for j in 1..=n {
            let cost = if a_chars[i - 1] == b_chars[j - 1] { 0 } else { 1 };
            curr[j] = (prev[j] + 1).min(curr[j - 1] + 1).min(prev[j - 1] + cost);
        }
        std::mem::swap(&mut prev, &mut curr);
    }
    prev[n]
}

fn expand_fuzzy(term: &str, vocab: &HashMap<String, Vec<Posting>>, max_dist: usize) -> Vec<String> {
    if max_dist == 0 {
        return vec![term.to_string()];
    }
    let mut matches = vec![term.to_string()];
    for word in vocab.keys() {
        if word.len() > 2 && levenshtein(term, word) <= max_dist {
            matches.push(word.clone());
        }
    }
    matches
}

#[wasm_bindgen]
pub struct WasmSearcher {
    index: IndexData,
}

#[wasm_bindgen]
impl WasmSearcher {
    #[wasm_bindgen(constructor)]
    pub fn new(jsonl_data: &str) -> Result<WasmSearcher, JsValue> {
        let mut docs = Vec::new();
        for line in jsonl_data.lines() {
            if line.trim().is_empty() {
                continue;
            }
            match serde_json::from_str::<Document>(line) {
                Ok(doc) => docs.push(doc),
                Err(_) => continue,
            }
        }

        let num_docs = docs.len();
        let mut inverted: HashMap<String, Vec<Posting>> = HashMap::new();
        let mut doc_lengths = Vec::with_capacity(num_docs);

        for (doc_id, doc) in docs.iter().enumerate() {
            let combined = format!(
                "{} {} {} {} {} {} {}",
                doc.paper_title,
                doc.paper_authors.join(" "),
                doc.paper_venue,
                doc.blog_summary,
                doc.paper_abstract,
                doc.topics.join(" "),
                doc.tags.join(" ")
            );
            let tokens = tokenize(&combined);
            doc_lengths.push(tokens.len() as f32);

            let mut tf_map: HashMap<String, f32> = HashMap::new();
            for token in &tokens {
                *tf_map.entry(token.clone()).or_insert(0.0) += 1.0;
            }
            for (term, tf) in tf_map {
                inverted
                    .entry(term)
                    .or_insert_with(Vec::new)
                    .push(Posting { doc_id, tf });
            }
        }

        let avg_doc_length = if num_docs > 0 {
            doc_lengths.iter().sum::<f32>() / num_docs as f32
        } else {
            0.0
        };

        Ok(WasmSearcher {
            index: IndexData {
                docs,
                inverted,
                doc_lengths,
                avg_doc_length,
                num_docs,
            },
        })
    }

    #[wasm_bindgen]
    pub fn search(&self, query: &str, limit: usize) -> String {
        self.do_search(query, limit, 0)
    }

    #[wasm_bindgen]
    pub fn search_fuzzy(&self, query: &str, limit: usize, max_distance: usize) -> String {
        self.do_search(query, limit, max_distance)
    }

    fn do_search(&self, query: &str, limit: usize, max_distance: usize) -> String {
        let query_terms = tokenize(query);
        if query_terms.is_empty() {
            return "[]".to_string();
        }

        let k1: f32 = 1.5;
        let b: f32 = 0.75;
        let mut scores: HashMap<usize, f32> = HashMap::new();

        for term in &query_terms {
            let expanded = if max_distance > 0 {
                expand_fuzzy(term, &self.index.inverted, max_distance)
            } else {
                vec![term.clone()]
            };

            for variant in expanded {
                let postings = match self.index.inverted.get(&variant) {
                    Some(p) => p,
                    None => continue,
                };
                let df = postings.len() as f32;
                if df == 0.0 {
                    continue;
                }
                let idf = ((self.index.num_docs as f32 - df + 0.5) / (df + 0.5) + 1.0).ln();

                for posting in postings {
                    let dl = self.index.doc_lengths[posting.doc_id];
                    let tf_norm = posting.tf
                        * (k1 + 1.0)
                        / (posting.tf + k1 * (1.0 - b + b * dl / self.index.avg_doc_length));
                    let score = idf * tf_norm;
                    *scores.entry(posting.doc_id).or_insert(0.0) += score;
                }
            }
        }

        let mut ranked: Vec<(usize, f32)> = scores.into_iter().collect();
        ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        ranked.truncate(limit);

        let results: Vec<serde_json::Value> = ranked
            .iter()
            .map(|(doc_id, score)| {
                let doc = &self.index.docs[*doc_id];
                serde_json::json!({
                    "score": score,
                    "date": doc.date,
                    "title": doc.paper_title,
                    "authors": doc.paper_authors,
                    "year": doc.paper_year,
                    "venue": doc.paper_venue,
                    "slug": doc.slug,
                })
            })
            .collect();

        serde_json::to_string(&results).unwrap_or_else(|_| "[]".to_string())
    }

    #[wasm_bindgen]
    pub fn num_docs(&self) -> usize {
        self.index.num_docs
    }
}
