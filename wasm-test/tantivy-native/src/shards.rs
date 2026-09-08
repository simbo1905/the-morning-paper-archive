use std::collections::BTreeSet;
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use tantivy::directory::error::{DeleteError, OpenReadError, OpenWriteError};
use tantivy::directory::{Directory, FileHandle, RamDirectory, WatchCallback, WatchHandle};
use tantivy::directory::WritePtr;
use tantivy::Index;
use tantivy::IndexSettings;

use crate::md_corpus::{self, MdDoc};

/// A RamDirectory wrapper that records every file written (and un-records on
/// delete). tantivy 0.26 has no way to list a RamDirectory's files, so the
/// in-memory shard builder needs this to collect the files for packing.
#[derive(Clone)]
pub struct RecordingDirectory {
    inner: RamDirectory,
    files: Arc<Mutex<BTreeSet<PathBuf>>>,
}

impl RecordingDirectory {
    pub fn new(inner: RamDirectory) -> RecordingDirectory {
        RecordingDirectory {
            inner,
            files: Arc::new(Mutex::new(BTreeSet::new())),
        }
    }

    /// Snapshot of the file names currently in the directory, sorted.
    pub fn recorded_files(&self) -> Vec<PathBuf> {
        self.files.lock().unwrap().iter().cloned().collect()
    }

    pub fn atomic_read_file(&self, path: &Path) -> std::io::Result<Vec<u8>> {
        Directory::atomic_read(&self.inner, path)
            .map_err(|e| std::io::Error::other(format!("atomic_read {path:?}: {e}")))
    }
}

impl fmt::Debug for RecordingDirectory {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "RecordingDirectory")
    }
}

impl Directory for RecordingDirectory {
    fn get_file_handle(&self, path: &Path) -> Result<Arc<dyn FileHandle>, OpenReadError> {
        Directory::get_file_handle(&self.inner, path)
    }

    fn delete(&self, path: &Path) -> Result<(), DeleteError> {
        self.files.lock().unwrap().remove(path);
        Directory::delete(&self.inner, path)
    }

    fn exists(&self, path: &Path) -> Result<bool, OpenReadError> {
        Directory::exists(&self.inner, path)
    }

    fn open_write(&self, path: &Path) -> Result<WritePtr, OpenWriteError> {
        self.files.lock().unwrap().insert(path.to_path_buf());
        Directory::open_write(&self.inner, path)
    }

    fn atomic_read(&self, path: &Path) -> Result<Vec<u8>, OpenReadError> {
        Directory::atomic_read(&self.inner, path)
    }

    fn atomic_write(&self, path: &Path, data: &[u8]) -> std::io::Result<()> {
        self.files.lock().unwrap().insert(path.to_path_buf());
        Directory::atomic_write(&self.inner, path, data)
    }

    fn sync_directory(&self) -> std::io::Result<()> {
        Directory::sync_directory(&self.inner)
    }

    fn watch(&self, watch_callback: WatchCallback) -> tantivy::Result<WatchHandle> {
        Directory::watch(&self.inner, watch_callback)
    }
}

/// Partition docs into at most n contiguous chronological chunks.
/// The corpus arrives sorted by date (md filename stems are ISO dates).
pub fn partition(corpus: &[MdDoc], n: usize) -> Vec<&[MdDoc]> {
    let n = n.max(1);
    let chunk_len = corpus.len().div_ceil(n);
    corpus.chunks(chunk_len.max(1)).collect()
}

/// Build one shard index fully in memory (RamDirectory) and return its files
/// in the packable (filename, bytes) form, sorted, ready for blob::pack_files.
/// Lock files created by the writer are included; blob loading skips them.
pub fn build_shard_files(docs: &[MdDoc]) -> Result<(Vec<(String, Vec<u8>)>, u64), Box<dyn std::error::Error>> {
    let s = md_corpus::md_schema();
    let dir = RecordingDirectory::new(RamDirectory::create());
    let index = Index::create(dir.clone(), s.schema.clone(), IndexSettings::default())?;
    let mut writer = index.writer(50_000_000)?;
    for doc_src in docs {
        let mut doc = tantivy::TantivyDocument::new();
        doc.add_text(s.date, &doc_src.date);
        doc.add_text(s.slug, &doc_src.slug);
        doc.add_text(s.blog_title, &doc_src.blog_title);
        doc.add_text(s.body, &doc_src.body);
        writer.add_document(doc)?;
    }
    writer.commit()?;
    drop(writer);

    let mut files = Vec::new();
    for path in dir.recorded_files() {
        let data = dir.atomic_read_file(&path)?;
        files.push((path.to_string_lossy().to_string(), data));
    }
    Ok((files, docs.len() as u64))
}
