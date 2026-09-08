use std::time::{Duration, Instant};

use tantivy::collector::{Count, TopDocs};
use tantivy::query::{BooleanQuery, FuzzyTermQuery, Occur, Query, QueryParser};
use tantivy::schema::{Field, Value};
use tantivy::{Searcher, Term};

/// The QA battery, identical to scripts/whoosh_oracle.py BATTERY.
/// (label, query text, fuzzy edit distance for whoosh's `term~dist` form.)
pub const QUERIES: &[(&str, &str, Option<u8>)] = &[
    ("blockchai", "blockchai", None),
    ("quantum blockchain", "quantum blockchain", None),
    ("consensus", "consensus", None),
    ("consensuz~2", "consensuz", Some(2)),
    ("paxos consensus", "paxos consensus", None),
    ("distributed transactions", "distributed transactions", None),
    ("compiler optimization", "compiler optimization", None),
];

/// Whoosh oracle md-mode totals (no limit), the QA bar for the article index.
pub const ORACLE_MD_COUNTS: &[(&str, u64)] = &[
    ("blockchai", 0),
    ("quantum blockchain", 2),
    ("consensus", 109),
    ("consensuz~2", 111),
    ("paxos consensus", 37),
    ("distributed transactions", 126),
    ("compiler optimization", 13),
];

pub struct QueryOutcome {
    pub label: String,
    pub total: usize,
    /// Top-5 document identities: stored date when available, else seg:doc.
    pub top: Vec<String>,
    pub elapsed: Duration,
}

impl QueryOutcome {
    pub fn ms(&self) -> f64 {
        self.elapsed.as_secs_f64() * 1000.0
    }
}

/// Run the battery against one searcher over the given query fields.
/// Plain queries use the QueryParser (per-term union across fields, terms
/// in conjunction — matching whoosh's MultifieldParser); fuzzy queries are
/// a union of FuzzyTermQuery over every query field.
pub fn run_battery(
    searcher: &Searcher,
    fields: &[Field],
    display: Option<Field>,
) -> Result<Vec<QueryOutcome>, Box<dyn std::error::Error>> {
    let mut parser = QueryParser::for_index(searcher.index(), fields.to_vec());
    // whoosh's MultifieldParser joins terms with AND; tantivy defaults to OR.
    parser.set_conjunction_by_default();
    let mut outcomes: Vec<QueryOutcome> = Vec::new();
    for (label, text, fuzzy) in QUERIES {
        let query: Box<dyn Query> = match fuzzy {
            Some(dist) => Box::new(BooleanQuery::new(
                fields
                    .iter()
                    .map(|f| {
                        let term = Term::from_field_text(*f, text);
                        (Occur::Should, Box::new(FuzzyTermQuery::new(term, *dist, true)) as Box<dyn Query>)
                    })
                    .collect(),
            )),
            None => Box::new(parser.parse_query(text)?),
        };
        let start = Instant::now();
        let total = searcher.search(&query, &Count)?;
        let top_docs = searcher.search(&query, &TopDocs::with_limit(5).order_by_score())?;
        let elapsed = start.elapsed();
        let top = top_docs
            .into_iter()
            .map(|(_, addr)| match display {
                Some(field) => {
                    let doc: tantivy::TantivyDocument = searcher.doc(addr)?;
                    Ok(doc.get_first(field).and_then(|v| v.as_str()).unwrap_or("?").to_string())
                }
                None => Ok(format!("{}:{}", addr.segment_ord, addr.doc_id)),
            })
            .collect::<Result<Vec<_>, tantivy::TantivyError>>()?;
        outcomes.push(QueryOutcome {
            label: label.to_string(),
            total,
            top,
            elapsed,
        });
    }
    Ok(outcomes)
}
