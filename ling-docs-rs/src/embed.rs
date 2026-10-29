//! Embeddings (spec §7.3): `snowflake-arctic-embed-m-v2.0`, its int8 ONNX export, on the CPU
//! through ONNX Runtime, **one chunk at a time**.
//!
//! Phase 0 measured batch 1 as both the fastest and the smallest setting (12.8 chunks/s in
//! 0.9 GB on four cores, where batch 16 pads every chunk to the longest and takes 3.9 GB for less
//! speed), so there is no batching here at all. CLS pooling, L2-normalised; queries carry the
//! `query: ` prefix and documents none, as the model card prescribes. Inputs are cut at 512
//! tokens, as Phase 0's embedding runs were.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use anyhow::{anyhow, Context, Result};
use ort::session::Session;
use ort::value::Tensor;
use tokenizers::Tokenizer;

use crate::chunk::TokenCount;

/// The prefix of a query (§7.3).
pub const QUERY_PREFIX: &str = "query: ";

/// Tokens an input is cut to, special tokens included.
pub const MAX_TOKENS: usize = 512;

/// Something that turns text into a unit vector.
pub trait Embed {
    fn dim(&self) -> usize;
    fn embed(&self, text: &str) -> Result<Vec<f32>>;
    /// A name recorded with the vectors, so vectors of another model are never mixed in.
    fn name(&self) -> &str;
    /// The files the vectors depend on (the model and its tokenizer): their SHA-256 is recorded
    /// with the vectors too, so a new revision under the same name is embedded again (§7.5).
    fn model_files(&self) -> Vec<PathBuf> {
        Vec::new()
    }
}

/// The installed model's files, in a model directory.
pub const MODEL_FILES: [&str; 2] = ["model.onnx", "tokenizer.json"];

/// The model files of a model directory.
pub fn model_files_in(dir: &Path) -> Vec<PathBuf> {
    MODEL_FILES.iter().map(|name| dir.join(name)).collect()
}

/// The files' sizes and modification times: when unchanged, the recorded hash still holds and the
/// files are not read again (a reinstall of the same bytes changes this, not the hash).
pub fn files_stat(files: &[PathBuf]) -> String {
    use std::os::unix::fs::MetadataExt;
    files
        .iter()
        .map(|f| match std::fs::metadata(f) {
            Ok(m) => format!(
                "{}:{}:{}.{}",
                f.file_name().unwrap_or_default().to_string_lossy(),
                m.len(),
                m.mtime(),
                m.mtime_nsec()
            ),
            Err(_) => format!("{}:-", f.file_name().unwrap_or_default().to_string_lossy()),
        })
        .collect::<Vec<_>>()
        .join(" ")
}

/// A SHA-256 over the files' names and contents' SHA-256, in order.
pub fn files_sha256(files: &[PathBuf]) -> Result<String> {
    use sha2::Digest;
    let mut hasher = sha2::Sha256::new();
    for f in files {
        hasher.update(
            f.file_name()
                .unwrap_or_default()
                .to_string_lossy()
                .as_bytes(),
        );
        hasher.update([0u8]);
        hasher.update(
            crate::discover::hash(f)
                .with_context(|| format!("reading {}", f.display()))?
                .as_bytes(),
        );
    }
    Ok(hasher
        .finalize()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect())
}

/// The model's tokenizer, which also counts tokens for chunking.
pub struct ModelTokenizer {
    tokenizer: Tokenizer,
}

impl ModelTokenizer {
    pub fn load(model_dir: &Path) -> Result<ModelTokenizer> {
        let path = model_dir.join("tokenizer.json");
        let mut tokenizer = Tokenizer::from_file(&path)
            .map_err(|e| anyhow!("cannot load {}: {e}", path.display()))?;
        // The file carries the model's 512-token truncation: with it every long unit would count
        // as 512 tokens and never be windowed (found 2026-10-08: half Phase 0's chunk count, and
        // chunks whose tail the model never saw). Counting needs the true length; `ids` cuts.
        tokenizer
            .with_truncation(None)
            .map_err(|e| anyhow!("{e}"))?;
        tokenizer.with_padding(None);
        Ok(ModelTokenizer { tokenizer })
    }

    /// Token ids with the special tokens, cut to [`MAX_TOKENS`] (the closing token kept).
    pub fn ids(&self, text: &str) -> Result<Vec<i64>> {
        let encoding = self
            .tokenizer
            .encode(text, true)
            .map_err(|e| anyhow!("tokenizer: {e}"))?;
        let mut ids: Vec<i64> = encoding.get_ids().iter().map(|i| *i as i64).collect();
        if ids.len() > MAX_TOKENS {
            let last = *ids.last().unwrap_or(&2);
            ids.truncate(MAX_TOKENS - 1);
            ids.push(last);
        }
        Ok(ids)
    }
}

impl TokenCount for ModelTokenizer {
    fn count(&self, text: &str) -> usize {
        self.tokenizer
            .encode(text, false)
            .map(|e| e.get_ids().len())
            .unwrap_or_else(|_| text.split_whitespace().count())
    }
}

/// The ONNX model.
pub struct OnnxEmbedder {
    session: Mutex<Session>,
    pub tokenizer: ModelTokenizer,
    dim: usize,
    name: String,
    files: Vec<PathBuf>,
}

/// Loads ONNX Runtime from the lib directory, once per process.
pub fn init_runtime(lib_dir: &Path) -> Result<()> {
    static DONE: std::sync::OnceLock<std::result::Result<(), String>> = std::sync::OnceLock::new();
    DONE.get_or_init(|| {
        let path = lib_dir.join("libonnxruntime.so");
        if !path.is_file() {
            return Err(format!(
                "ONNX Runtime is not installed ({} is missing)",
                path.display()
            ));
        }
        ort::init_from(&path).map_err(|e| e.to_string())?.commit();
        Ok(())
    })
    .clone()
    .map_err(|e| anyhow!(e))
}

impl OnnxEmbedder {
    pub fn load(model_dir: &Path, lib_dir: &Path, threads: usize) -> Result<OnnxEmbedder> {
        init_runtime(lib_dir)?;
        let tokenizer = ModelTokenizer::load(model_dir)?;
        let model = model_dir.join("model.onnx");
        let session = Session::builder()
            .map_err(|e| anyhow!("{e}"))?
            .with_intra_threads(threads)
            .map_err(|e| anyhow!("{e}"))?
            .with_inter_threads(1)
            .map_err(|e| anyhow!("{e}"))?
            .commit_from_file(&model)
            .with_context(|| format!("loading {}", model.display()))?;
        let dim = 768;
        Ok(OnnxEmbedder {
            session: Mutex::new(session),
            tokenizer,
            dim,
            name: crate::config::MODEL_NAME.to_string(),
            files: model_files_in(model_dir),
        })
    }
}

impl Embed for OnnxEmbedder {
    fn dim(&self) -> usize {
        self.dim
    }

    fn name(&self) -> &str {
        &self.name
    }

    fn model_files(&self) -> Vec<PathBuf> {
        self.files.clone()
    }

    fn embed(&self, text: &str) -> Result<Vec<f32>> {
        let ids = self.tokenizer.ids(text)?;
        let n = ids.len();
        let mask = vec![1i64; n];
        let input_ids = Tensor::from_array(([1usize, n], ids)).map_err(|e| anyhow!("{e}"))?;
        let attention = Tensor::from_array(([1usize, n], mask)).map_err(|e| anyhow!("{e}"))?;
        let mut session = self
            .session
            .lock()
            .map_err(|_| anyhow!("the model session is poisoned"))?;
        let outputs = session
            .run(ort::inputs!["input_ids" => input_ids, "attention_mask" => attention])
            .map_err(|e| anyhow!("{e}"))?;
        // CLS pooling: the first token of the last hidden state.
        let (shape, data) = outputs["token_embeddings"]
            .try_extract_tensor::<f32>()
            .map_err(|e| anyhow!("{e}"))?;
        let dim = *shape.last().ok_or_else(|| anyhow!("no output"))? as usize;
        let mut vector = data[..dim].to_vec();
        normalise(&mut vector);
        Ok(vector)
    }
}

/// Scales a vector to unit length.
pub fn normalise(v: &mut [f32]) {
    let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm > 0.0 {
        for x in v.iter_mut() {
            *x /= norm;
        }
    }
}

/// A deterministic stand-in for tests: hashed words into a small vector. Texts sharing words are
/// close, which is all a test of the plumbing needs.
pub struct HashEmbedder {
    pub dim: usize,
}

impl Embed for HashEmbedder {
    fn dim(&self) -> usize {
        self.dim
    }

    fn name(&self) -> &str {
        "hash-test"
    }

    fn embed(&self, text: &str) -> Result<Vec<f32>> {
        let mut v = vec![0f32; self.dim];
        let text = text.strip_prefix(QUERY_PREFIX).unwrap_or(text);
        for word in text
            .split(|c: char| !c.is_alphanumeric())
            .filter(|w| w.len() > 2)
        {
            let word = word.to_lowercase();
            let h = word.bytes().fold(1469598103934665603u64, |h, b| {
                (h ^ b as u64).wrapping_mul(1099511628211)
            });
            v[(h % self.dim as u64) as usize] += 1.0;
        }
        normalise(&mut v);
        Ok(v)
    }
}

/// Packs a vector as little-endian float32 bytes.
pub fn to_blob(v: &[f32]) -> Vec<u8> {
    v.iter().flat_map(|x| x.to_le_bytes()).collect()
}

/// Reads a float32 blob.
pub fn from_blob(b: &[u8]) -> Vec<f32> {
    b.chunks_exact(4)
        .map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]]))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_tokenizer_counts_past_the_models_window() {
        // Only where the model is installed (`MIGHTLING_DOCS_MODEL_DIR`).
        let Some(dir) = std::env::var_os("MIGHTLING_DOCS_MODEL_DIR")
            .map(std::path::PathBuf::from)
            .filter(|d| d.join("tokenizer.json").is_file())
        else {
            return;
        };
        let tokenizer = ModelTokenizer::load(&dir).unwrap();
        let long = "The quick brown fox jumps over the lazy dog. ".repeat(200);
        assert!(
            tokenizer.count(&long) > 1500,
            "counting must not stop at 512"
        );
        assert_eq!(tokenizer.ids(&long).unwrap().len(), MAX_TOKENS);
    }

    #[test]
    fn blobs_round_trip_and_vectors_are_unit_length() {
        let e = HashEmbedder { dim: 32 };
        let v = e.embed("alpha beta gamma").unwrap();
        let norm: f32 = v.iter().map(|x| x * x).sum();
        assert!((norm - 1.0).abs() < 1e-5);
        assert_eq!(from_blob(&to_blob(&v)), v);
        let q = e.embed("query: alpha beta").unwrap();
        let other = e.embed("unrelated words entirely").unwrap();
        let dot = |a: &[f32], b: &[f32]| a.iter().zip(b).map(|(x, y)| x * y).sum::<f32>();
        assert!(dot(&q, &v) > dot(&q, &other));
    }
}
