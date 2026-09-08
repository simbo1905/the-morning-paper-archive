use std::time::Instant;

use tantivy::Index;

use tantivy_native::blob::load_into_ram;
use tantivy_native::battery::{self, ORACLE_MD_COUNTS};
use tantivy_native::md_corpus;
use tantivy_native::{MD_INDEX_BLOB, MD_INDEX_DIR};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mode = std::env::args().nth(1).unwrap_or_default();
    let start = Instant::now();

    let (index, unpack_ms, open_ms) = match mode.as_str() {
        "disk" => {
            let index = Index::open_in_dir(MD_INDEX_DIR)?;
            (index, 0.0, start.elapsed().as_secs_f64() * 1000.0)
        }
        "blob" => {
            let packed = std::fs::read(MD_INDEX_BLOB)?;
            let read_time = start.elapsed();
            let ram = load_into_ram(&packed)?;
            let unpack_ms = (start.elapsed() - read_time).as_secs_f64() * 1000.0;
            let index = Index::open(ram)?;
            let open_ms = (start.elapsed() - read_time).as_secs_f64() * 1000.0 - unpack_ms;
            println!(
                "Blob read {:.3?} ({} bytes), unpacked into RamDirectory in {:.3?}, index open {:.3?}",
                read_time,
                packed.len(),
                std::time::Duration::from_secs_f64(unpack_ms / 1000.0),
                std::time::Duration::from_secs_f64(open_ms / 1000.0),
            );
            (index, unpack_ms, open_ms)
        }
        other => {
            eprintln!("usage: query_md <disk|blob> (got '{other}')");
            std::process::exit(2);
        }
    };

    let s = md_corpus::md_schema();
    let searcher = index.reader()?.searcher();
    println!("num_docs: {}", searcher.num_docs());
    println!("unpack->RamDirectory: {unpack_ms:.2} ms, open: {open_ms:.2} ms");
    println!();
    println!("{:<26}{:>8}{:>8}{:>10}", "query", "tantivy", "oracle", "ms");

    let outcomes = battery::run_battery(&searcher, &[s.blog_title, s.body], Some(s.date))?;
    let oracle: std::collections::HashMap<&str, u64> = ORACLE_MD_COUNTS.iter().copied().collect();
    for o in &outcomes {
        let oracle_total = oracle.get(o.label.as_str()).copied().unwrap_or(0);
        println!("{:<26}{:>8}{:>8}{:>10.2}", o.label, o.total, oracle_total, o.ms());
    }
    let battery_ms: f64 = outcomes.iter().map(|o| o.ms()).sum();
    println!();
    println!("battery total: {:.2} ms ({} queries)", battery_ms, outcomes.len());
    Ok(())
}
