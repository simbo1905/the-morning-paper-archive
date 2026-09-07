use std::collections::HashMap;
use std::fs;

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
    if m == 0 { return n; }
    if n == 0 { return m; }
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
    if max_dist == 0 { return vec![term.to_string()]; }
    let mut matches = vec![term.to_string()];
    for word in vocab.keys() {
        if word.len() > 2 && levenshtein(term, word) <= max_dist {
            matches.push(word.clone());
        }
    }
    matches
}

fn build_index(jsonl: &str) -> IndexData {
    let mut docs = Vec::new();
    for line in jsonl.lines() {
        if line.trim().is_empty() { continue; }
        if let Ok(doc) = serde_json::from_str::<Document>(line) {
            docs.push(doc);
        }
    }
    let num_docs = docs.len();
    let mut inverted: HashMap<String, Vec<Posting>> = HashMap::new();
    let mut doc_lengths = Vec::with_capacity(num_docs);
    for (doc_id, doc) in docs.iter().enumerate() {
        let combined = format!("{} {} {} {} {} {} {}",
            doc.paper_title, doc.paper_authors.join(" "), doc.paper_venue,
            doc.blog_summary, doc.paper_abstract, doc.topics.join(" "), doc.tags.join(" "));
        let tokens = tokenize(&combined);
        doc_lengths.push(tokens.len() as f32);
        let mut tf_map: HashMap<String, f32> = HashMap::new();
        for token in &tokens { *tf_map.entry(token.clone()).or_insert(0.0) += 1.0; }
        for (term, tf) in tf_map { inverted.entry(term).or_insert_with(Vec::new).push(Posting { doc_id, tf }); }
    }
    let avg_doc_length = if num_docs > 0 { doc_lengths.iter().sum::<f32>() / num_docs as f32 } else { 0.0 };
    IndexData { docs, inverted, doc_lengths, avg_doc_length, num_docs }
}

fn do_search(index: &IndexData, query: &str, limit: usize, max_distance: usize) {
    let query_terms = tokenize(query);
    if query_terms.is_empty() { println!("  No query terms"); return; }
    let k1: f32 = 1.5; let b: f32 = 0.75;
    let mut scores: HashMap<usize, f32> = HashMap::new();
    for term in &query_terms {
        let expanded = if max_distance > 0 { expand_fuzzy(term, &index.inverted, max_distance) } else { vec![term.clone()] };
        for variant in expanded {
            let postings = match index.inverted.get(&variant) { Some(p) => p, None => continue };
            let df = postings.len() as f32;
            if df == 0.0 { continue; }
            let idf = ((index.num_docs as f32 - df + 0.5) / (df + 0.5) + 1.0).ln();
            for posting in postings {
                let dl = index.doc_lengths[posting.doc_id];
                let tf_norm = posting.tf * (k1 + 1.0) / (posting.tf + k1 * (1.0 - b + b * dl / index.avg_doc_length));
                *scores.entry(posting.doc_id).or_insert(0.0) += idf * tf_norm;
            }
        }
    }
    let mut ranked: Vec<(usize, f32)> = scores.into_iter().collect();
    ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    ranked.truncate(limit);
    println!("  {} results", ranked.len());
    for (doc_id, score) in &ranked {
        let doc = &index.docs[*doc_id];
        println!("    [{:.3}] [{}] {} ({})", score, doc.date, doc.paper_title, doc.paper_year);
    }
}

fn main() {
    let pages_dir = std::path::Path::new("../../pages");
    let json_files: Vec<_> = fs::read_dir(pages_dir).unwrap()
        .filter_map(|e| e.ok()).map(|e| e.path())
        .filter(|p| p.extension().map_or(false, |ext| ext == "json"))
        .collect();

    println!("Loading {} JSON files...", json_files.len());
    let mut docs_raw = Vec::new();
    for jf in &json_files {
        let data = fs::read_to_string(jf).unwrap();
        if let Ok(doc) = serde_json::from_str::<Document>(&data) {
            docs_raw.push(doc);
        }
    }
    // Serialize as JSONL for the index builder
    let jsonl: String = docs_raw.iter()
        .map(|d| serde_json::to_string(d).unwrap())
        .collect::<Vec<_>>()
        .join("\n");

    println!("Building index...");
    let t0 = std::time::Instant::now();
    let index = build_index(&jsonl);
    let build_time = t0.elapsed();
    println!("  {} docs indexed in {:?}", index.num_docs, build_time);

    println!("\n=== TEST SEARCHES ===");
    for q in &["paxos consensus", "distributed transactions", "compiler optimization"] {
        println!("\nSearch: '{}'", q);
        let t0 = std::time::Instant::now();
        do_search(&index, q, 5, 0);
        println!("  ({:?})", t0.elapsed());
    }

    println!("\n=== FUZZY SEARCHES ===");
    for (q, dist) in &[("consensus", 2), ("paxos", 1), ("distributd", 2)] {
        println!("\nFuzzy: '{}'~{}", q, dist);
        let t0 = std::time::Instant::now();
        do_search(&index, q, 5, *dist);
        println!("  ({:?})", t0.elapsed());
    }
}
