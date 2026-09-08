use std::fs;
use std::path::Path;
use std::time::Instant;

use tantivy_native::blob::{self, pack_files, read_dir_files};
use tantivy_native::{MD_INDEX_BLOB, MD_INDEX_DIR};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let index_dir = Path::new(MD_INDEX_DIR);
    let blob_path = Path::new(MD_INDEX_BLOB);

    let start = Instant::now();
    let files = read_dir_files(index_dir)?;
    let read_time = start.elapsed();
    let packed = pack_files(&files);
    let pack_time = start.elapsed() - read_time;

    fs::write(blob_path, &packed)?;
    println!(
        "Packed {} files ({} bytes on disk) -> {} ({} bytes) in {:.3?} (read {:.3?})",
        files.len(),
        files.iter().map(|(_, d)| d.len()).sum::<usize>(),
        MD_INDEX_BLOB,
        packed.len(),
        pack_time,
        read_time,
    );

    // Self-check: the packed blob must decompress to the same file set.
    let extracted = blob::extract_files(&packed)?;
    assert_eq!(extracted.len(), files.len());
    for ((name, data), (name2, data2)) in extracted.iter().zip(files.iter()) {
        assert_eq!(name, name2);
        assert_eq!(data, data2, "round-trip mismatch for {name}");
    }
    println!("Round-trip self-check OK");
    Ok(())
}
