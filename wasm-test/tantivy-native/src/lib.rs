pub mod battery;
pub mod blob;
pub mod md_corpus;
pub mod shards;

pub const PAGES_DIR: &str = "../../pages";
pub const JSONL_PATH: &str = "../../wasm-test/search_data.jsonl";
pub const MD_INDEX_DIR: &str = "./tantivy_md_index";
pub const MD_INDEX_BLOB: &str = "./tantivy_md_index.blob";
pub const CORPUS_SIZE: usize = 995;
