//! Phase 0 probe: extract a PDF's text per page with a pure-Rust crate and print it as JSON.
//! Usage: rustpdf <pdf-extract|pdf_oxide> <file.pdf>
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let (engine, path) = (args[1].as_str(), args[2].as_str());
    let start = Instant::now();
    let pages: Result<Vec<String>, String> = match engine {
        "pdf-extract" => pdf_extract::extract_text_by_pages(path).map_err(|e| e.to_string()),
        "pdf_oxide" => (|| {
            let mut doc = pdf_oxide::PdfDocument::open(path).map_err(|e| e.to_string())?;
            let n = doc.page_count().map_err(|e| e.to_string())?;
            let mut out = Vec::with_capacity(n);
            for i in 0..n {
                out.push(doc.extract_text(i).unwrap_or_default());
            }
            Ok(out)
        })(),
        _ => Err(format!("unknown engine {engine}")),
    };
    let secs = start.elapsed().as_secs_f64();
    let value = match pages {
        Ok(p) => serde_json::json!({"ok": true, "pages": p, "seconds": secs}),
        Err(e) => serde_json::json!({"ok": false, "error": e, "seconds": secs}),
    };
    println!("{value}");
}
