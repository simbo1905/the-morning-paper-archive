use std::time::Instant;

use tantivy::schema::{FieldType, IndexRecordOption};
use tantivy::Index;

use tantivy_native::blob::{self, load_into_ram};
use tantivy_native::battery;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let path = match std::env::args().nth(1) {
        Some(p) => p,
        None => {
            eprintln!("usage: inspect_blob <path>");
            std::process::exit(2);
        }
    };

    let start = Instant::now();
    let packed = std::fs::read(&path)?;
    let read_time = start.elapsed();
    let (manifest_len, entries) = blob::parse_manifest(&packed)?;
    println!("Blob: {path} ({} bytes, manifest_len {manifest_len}, {} files)", packed.len(), entries.len());
    for e in &entries {
        println!("  {}:{}", e.filename, e.len);
    }

    let ram = load_into_ram(&packed)?;
    let unpack_ms = (start.elapsed() - read_time).as_secs_f64() * 1000.0;
    println!("Unpacked into RamDirectory in {:.2} ms", unpack_ms);

    match Index::open(ram) {
        Ok(index) => {
            let open_ms = (start.elapsed() - read_time).as_secs_f64() * 1000.0 - unpack_ms;
            println!("Index::open SUCCEEDED in {:.2} ms — genuine tantivy index", open_ms);
            let schema = index.schema();
            let mut indexed_text: Vec<tantivy::schema::Field> = Vec::new();
            for (field, entry) in schema.fields() {
                if !entry.is_indexed() {
                    continue;
                }
                if let FieldType::Str(opts) = entry.field_type() {
                    if let Some(idx) = opts.get_indexing_options() {
                        if idx.tokenizer() != "raw" && idx.index_option() != IndexRecordOption::Basic {
                            indexed_text.push(field);
                        }
                    }
                }
            }
            let names: Vec<&str> = indexed_text
                .iter()
                .map(|f| schema.get_field_name(*f))
                .collect();
            println!("Tokenized indexed fields: {}", names.join(", "));

            let reader = index.reader()?;
            let searcher = reader.searcher();
            println!("num_docs: {}", searcher.num_docs());

            let display = schema.get_field("date").ok();
            let outcomes = battery::run_battery(&searcher, &indexed_text, display)?;
            println!();
            println!("{:<26}{:>8}{:>10}", "query", "hits", "ms");
            for o in &outcomes {
                println!("{:<26}{:>8}{:>10.2}", o.label, o.total, o.ms());
            }
        }
        Err(e) => {
            println!("Index::open FAILED: {e}");
            println!("Verdict: blob parses as the manifest format but is NOT a loadable tantivy index");
            return Ok(());
        }
    }
    Ok(())
}
