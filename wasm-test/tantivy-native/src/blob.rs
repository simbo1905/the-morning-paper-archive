use std::fs;
use std::io;
use std::path::Path;

use tantivy::directory::RamDirectory;
use tantivy::{Directory, Index};

/// Packed-blob format (shared by the packers and the wasm crate consumers):
///
///   u32 LE  manifest_len
///   manifest: "filename:offset:len" lines, separated by '\n' (no trailing
///             newline); offsets are relative to the start of the byte region
///   bytes:    the concatenated file contents
///
/// i.e. file bytes live at blob[4 + manifest_len + offset .. + len].

pub struct BlobEntry {
    pub filename: String,
    pub offset: usize,
    pub len: usize,
}

pub fn parse_manifest(blob: &[u8]) -> io::Result<(u32, Vec<BlobEntry>)> {
    if blob.len() < 4 {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "blob too short for manifest length",
        ));
    }
    let manifest_len = u32::from_le_bytes([blob[0], blob[1], blob[2], blob[3]]);
    let end = 4 + manifest_len as usize;
    if blob.len() < end {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "blob shorter than declared manifest",
        ));
    }
    let manifest = std::str::from_utf8(&blob[4..end]).map_err(|e| {
        io::Error::new(io::ErrorKind::InvalidData, format!("bad manifest utf8: {e}"))
    })?;
    let mut entries = Vec::new();
    for line in manifest.split('\n') {
        let parts: Vec<&str> = line.split(':').collect();
        if parts.len() != 3 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("manifest line not filename:offset:len: {line}"),
            ));
        }
        entries.push(BlobEntry {
            filename: parts[0].to_string(),
            offset: parts[1].parse().map_err(|_| {
                io::Error::new(io::ErrorKind::InvalidData, format!("bad offset in {line}"))
            })?,
            len: parts[2].parse().map_err(|_| {
                io::Error::new(io::ErrorKind::InvalidData, format!("bad len in {line}"))
            })?,
        });
    }
    Ok((manifest_len, entries))
}

pub fn extract_files(blob: &[u8]) -> io::Result<Vec<(String, Vec<u8>)>> {
    let (manifest_len, entries) = parse_manifest(blob)?;
    let base = 4 + manifest_len as usize;
    let mut files = Vec::with_capacity(entries.len());
    for e in entries {
        let start = base + e.offset;
        let stop = start + e.len;
        if blob.len() < stop {
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                format!("blob shorter than file {} declares", e.filename),
            ));
        }
        files.push((e.filename, blob[start..stop].to_vec()));
    }
    Ok(files)
}

/// Pack a set of (filename, bytes) into the manifest blob format.
/// File order must be deterministic (sorted) for reproducible blobs.
pub fn pack_files(files: &[(String, Vec<u8>)]) -> Vec<u8> {
    let mut manifest = Vec::new();
    let mut bytes = Vec::new();
    for (filename, data) in files {
        if !manifest.is_empty() {
            manifest.push(b'\n');
        }
        manifest.extend_from_slice(format!("{}:{}:{}", filename, bytes.len(), data.len()).as_bytes());
        bytes.extend_from_slice(data);
    }
    let mut blob = Vec::with_capacity(4 + manifest.len() + bytes.len());
    blob.extend_from_slice(&(manifest.len() as u32).to_le_bytes());
    blob.extend_from_slice(&manifest);
    blob.extend_from_slice(&bytes);
    blob
}

/// Read every file of a directory (sorted by name), ready for pack_files.
pub fn read_dir_files(dir: &Path) -> io::Result<Vec<(String, Vec<u8>)>> {
    let mut names: Vec<String> = fs::read_dir(dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().to_string())
        .collect();
    names.sort();
    let mut files = Vec::with_capacity(names.len());
    for name in names {
        files.push((name.clone(), fs::read(dir.join(&name))?));
    }
    Ok(files)
}

/// Extract a manifest blob into a fresh RamDirectory (one atomic_write per file).
pub fn load_into_ram(blob: &[u8]) -> io::Result<RamDirectory> {
    let ram = RamDirectory::create();
    load_into_ram_dir(blob, &ram)?;
    Ok(ram)
}

/// Lock files must NOT be materialized: the reader acquires the meta lock via
/// open_write, which fails FileAlreadyExists if the packed 0-byte lock file
/// is present in the directory.
const LOCK_FILES: [&str; 2] = [".tantivy-meta.lock", ".tantivy-writer.lock"];

pub fn load_into_ram_dir(blob: &[u8], ram: &RamDirectory) -> io::Result<()> {
    for (filename, data) in extract_files(blob)? {
        if LOCK_FILES.contains(&filename.as_str()) {
            continue;
        }
        ram.atomic_write(Path::new(&filename), &data)?;
    }
    Ok(())
}

/// Unpack a manifest blob into a RamDirectory and open it as a tantivy Index.
pub fn open_index_from_blob(blob: &[u8]) -> tantivy::Result<Index> {
    let ram = load_into_ram(blob)?;
    Index::open(ram)
}
