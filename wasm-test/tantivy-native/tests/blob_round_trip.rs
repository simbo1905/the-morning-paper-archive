use std::fs;
use std::path::{Path, PathBuf};

use tantivy::collector::TopDocs;
use tantivy::query::QueryParser;
use tantivy::schema::*;
use tantivy::Index;

use tantivy_native::blob;

fn temp_dir(label: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "tantivy_native_{}_{}_{}",
        label,
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn build_tiny_index(dir: &Path) -> Index {
    let mut builder = Schema::builder();
    let date = builder.add_text_field("date", STRING | STORED);
    let body = builder.add_text_field("body", TEXT);
    let schema = builder.build();

    let index = Index::create_in_dir(dir, schema).unwrap();
    let mut writer = index.writer(50_000_000).unwrap();
    for (d, b) in [
        ("a1", "alpha beta gamma consensus"),
        ("a2", "alpha delta epsilon"),
        ("a3", "zeta eta consensus paxos"),
    ] {
        let mut doc = tantivy::TantivyDocument::new();
        doc.add_text(date, d);
        doc.add_text(body, b);
        writer.add_document(doc).unwrap();
    }
    writer.commit().unwrap();
    index
}

fn query_dates(index: &Index, q: &str) -> Vec<String> {
    let schema = index.schema();
    let date = schema.get_field("date").unwrap();
    let body = schema.get_field("body").unwrap();
    let reader = index.reader().unwrap();
    let searcher = reader.searcher();
    let parser = QueryParser::for_index(index, vec![body]);
    let query = parser.parse_query(q).unwrap();
    searcher
        .search(&query, &TopDocs::with_limit(5).order_by_score())
        .unwrap()
        .into_iter()
        .map(|(_, addr)| {
            let doc: tantivy::TantivyDocument = searcher.doc(addr).unwrap();
            doc.get_first(date).and_then(|v| v.as_str()).unwrap().to_string()
        })
        .collect()
}

#[test]
fn blob_round_trip() {
    let dir = temp_dir("roundtrip_src");
    let index = build_tiny_index(&dir);

    let files = blob::read_dir_files(&dir).unwrap();
    assert!(files.iter().any(|(name, _)| name == "meta.json"));
    let packed = blob::pack_files(&files);

    // Manifest format: u32 LE length | "filename:offset:len" \n lines | bytes.
    let manifest_len = u32::from_le_bytes([packed[0], packed[1], packed[2], packed[3]]) as usize;
    assert_eq!(packed.len(), 4 + manifest_len + files.iter().map(|(_, d)| d.len()).sum::<usize>());
    let manifest = std::str::from_utf8(&packed[4..4 + manifest_len]).unwrap();
    assert_eq!(manifest.split('\n').count(), files.len());
    for line in manifest.split('\n') {
        let parts: Vec<&str> = line.split(':').collect();
        assert_eq!(parts.len(), 3, "manifest line not filename:offset:len: {line}");
        assert!(parts[1].parse::<usize>().is_ok());
        assert!(parts[2].parse::<usize>().is_ok());
    }

    let blob_index = Index::open(blob::load_into_ram(&packed).unwrap()).unwrap();

    for q in ["alpha", "consensus", "paxos", "missing"] {
        let disk_hits = query_dates(&index, q);
        let blob_hits = query_dates(&blob_index, q);
        assert_eq!(disk_hits, blob_hits, "query '{q}' differs disk vs blob");
    }
    let consensus = query_dates(&blob_index, "consensus");
    assert_eq!(consensus.len(), 2);

    fs::remove_dir_all(&dir).ok();
}
