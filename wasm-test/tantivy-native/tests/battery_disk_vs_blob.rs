use std::fs;
use std::path::{Path, PathBuf};

use tantivy::Index;

use tantivy_native::{battery, blob, md_corpus};

const PAGES: &str = "../../pages";
const JSONL: &str = "../../wasm-test/search_data.jsonl";

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

#[test]
fn battery_disk_vs_blob_same_results() {
    let corpus = md_corpus::load_md_corpus(Path::new(PAGES), Path::new(JSONL)).unwrap();
    assert_eq!(corpus.len(), 995, "reachable md corpus must be 995 docs");

    let dir = temp_dir("md_index");
    let disk_index = md_corpus::build_md_index(&dir, &corpus).unwrap();
    assert_eq!(disk_index.reader().unwrap().searcher().num_docs(), 995);

    let files = blob::read_dir_files(&dir).unwrap();
    let packed = blob::pack_files(&files);
    let blob_index = Index::open(blob::load_into_ram(&packed).unwrap()).unwrap();

    let s = md_corpus::md_schema();
    let disk_out = battery::run_battery(
        &disk_index.reader().unwrap().searcher(),
        &[s.blog_title, s.body],
        Some(s.date),
    )
    .unwrap();
    let blob_out = battery::run_battery(
        &blob_index.reader().unwrap().searcher(),
        &[s.blog_title, s.body],
        Some(s.date),
    )
    .unwrap();

    assert_eq!(disk_out.len(), battery::QUERIES.len());
    for (d, b) in disk_out.iter().zip(blob_out.iter()) {
        assert_eq!(d.label, b.label);
        assert_eq!(d.total, b.total, "total mismatch for '{}'", d.label);
        assert_eq!(d.top, b.top, "top-5 mismatch for '{}'", d.label);
    }

    fs::remove_dir_all(&dir).ok();
}
