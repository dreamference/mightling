//! The false-positive check on real model output: every text in a JSON-lines corpus
//! (`{"source", "kind", "text"}` per line, written by `scripts/loop_guard_corpus.py` from SWE-bench
//! transcripts and saved sessions) is streamed through the detector with the default thresholds,
//! and each detection is printed with its text's source. Ignored by default, since the corpus lives
//! outside the repository:
//!
//! ```text
//! LOOP_GUARD_CORPUS=/path/corpus.jsonl cargo test --release -p ling-loop-guard --test corpus -- --ignored --nocapture
//! ```
//!
//! `LOOP_GUARD_MAX_FLAGGED` (default 0.001, a fraction of the texts) fails the test above it, and
//! `LOOP_GUARD_THRESHOLDS` tries other thresholds, as `max_pattern_size,min_pattern_size,min_count,
//! max_run,max_char_run,compression_window,min_compression_ratio`.

use std::collections::BTreeMap;
use std::io::BufRead;

use ling_loop_guard::Detector;
use ling_loop_guard::Thresholds;
use ling_loop_guard::scan;

/// The defaults, or `LOOP_GUARD_THRESHOLDS`.
fn thresholds() -> Thresholds {
    match std::env::var("LOOP_GUARD_THRESHOLDS") {
        Ok(spec) => {
            let v: Vec<f64> = spec.split(',').map(|x| x.trim().parse().expect("a number")).collect();
            assert_eq!(v.len(), 7, "seven thresholds");
            Thresholds {
                max_pattern_size: v[0] as usize,
                min_pattern_size: v[1] as usize,
                min_count: v[2] as usize,
                max_run: v[3] as usize,
                max_char_run: v[4] as usize,
                compression_window: v[5] as usize,
                min_compression_ratio: v[6],
            }
        }
        Err(_) => Thresholds::default(),
    }
}

#[test]
#[ignore]
fn normal_outputs_are_not_taken_for_loops() {
    let path = std::env::var("LOOP_GUARD_CORPUS").expect("LOOP_GUARD_CORPUS names the corpus");
    let max: f64 = std::env::var("LOOP_GUARD_MAX_FLAGGED").ok().and_then(|v| v.parse().ok()).unwrap_or(0.001);
    let file = std::fs::File::open(&path).expect("the corpus opens");
    let thresholds = thresholds();
    println!("{thresholds:?}");
    let mut texts: BTreeMap<String, (usize, usize, usize)> = BTreeMap::new();
    let (mut total, mut flagged) = (0usize, 0usize);
    for line in std::io::BufReader::new(file).lines() {
        let line = line.expect("a line");
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
        let text = value["text"].as_str().unwrap_or_default();
        let kind = value["kind"].as_str().unwrap_or("?").to_string();
        let entry = texts.entry(kind.clone()).or_default();
        entry.0 += 1;
        entry.2 += text.len();
        total += 1;
        if let Some(found) = scan(text, &thresholds) {
            entry.1 += 1;
            flagged += 1;
            let at = found.cut.min(text.len());
            let mut end = (at + 160).min(text.len());
            while !text.is_char_boundary(end) {
                end -= 1;
            }
            println!(
                "FLAGGED {kind} {:?} len={} cut={} in {}\n    after cut: {:?}",
                found.kind,
                text.len(),
                found.cut,
                value["source"].as_str().unwrap_or_default(),
                &text[at..end]
            );
        }
    }
    for (kind, (n, f, bytes)) in &texts {
        println!("{kind}: {f} of {n} flagged ({:.4}%), {bytes} bytes", 100.0 * *f as f64 / *n as f64);
    }
    let rate = flagged as f64 / total.max(1) as f64;
    println!("all: {flagged} of {total} flagged ({:.4}%)", 100.0 * rate);
    assert!(rate <= max, "{flagged} of {total} normal texts were taken for loops");
}

/// The other half: the same texts made to loop. Each message or tool input of at least 400 bytes
/// gets its last `n` bytes (100, 400, 1,600 or 6,000) repeated after it, exactly or (every other
/// text) with a counter in each copy, as a near loop; the detector must stop it, and the bytes it
/// lets through past the text are printed as percentiles.
#[test]
#[ignore]
fn the_same_outputs_made_to_loop_are_stopped() {
    let path = std::env::var("LOOP_GUARD_CORPUS").expect("LOOP_GUARD_CORPUS names the corpus");
    let file = std::fs::File::open(&path).expect("the corpus opens");
    let thresholds = thresholds();
    let mut through: BTreeMap<(usize, bool), Vec<(usize, usize)>> = BTreeMap::new();
    let mut missed: BTreeMap<(usize, bool), usize> = BTreeMap::new();
    for (i, line) in std::io::BufReader::new(file).lines().enumerate() {
        let line = line.expect("a line");
        let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) else { continue };
        if !matches!(value["kind"].as_str(), Some("message" | "tool_input")) || i % 7 != 0 {
            continue;
        }
        let text = value["text"].as_str().unwrap_or_default();
        if text.len() < 400 {
            continue;
        }
        let n = [100, 400, 1600, 6000][i % 4];
        if text.len() < n {
            continue;
        }
        let mut from = text.len() - n;
        while !text.is_char_boundary(from) {
            from += 1;
        }
        let block = &text[from..];
        let near = (i / 4) % 2 == 1;
        let mut looped = text.to_string();
        // Enough copies for any check to have its say: 64 KB or 40 copies.
        let copies = (65536 / block.len().max(1)).max(40);
        for c in 0..copies {
            if near {
                looped.push_str(&format!("({c}) "));
            }
            looped.push_str(block);
        }
        let mut detector = Detector::new(thresholds.clone());
        detector.push(text);
        let pieces_before = detector.pieces();
        let mut stopped = detector.detection().is_some();
        let mut rest = &looped[text.len()..];
        while !stopped && !rest.is_empty() {
            let mut take = rest.len().min(7);
            while !rest.is_char_boundary(take) {
                take += 1;
            }
            stopped = detector.push(&rest[..take]).is_some();
            rest = &rest[take..];
        }
        if stopped {
            let past = detector.text().len() - text.len();
            through.entry((n, near)).or_default().push((past, detector.pieces() - pieces_before));
        } else {
            *missed.entry((n, near)).or_default() += 1;
        }
    }
    let mut all_missed = 0;
    for ((n, near), mut v) in through {
        v.sort_unstable();
        let miss = missed.get(&(n, near)).copied().unwrap_or(0);
        all_missed += miss;
        let p = |q: f64| v[((v.len() - 1) as f64 * q) as usize];
        println!(
            "block {n:>5} {}: {} stopped, {miss} missed; streamed before the stop, bytes/pieces: p50 {:?} p90 {:?} max {:?}",
            if near { "near " } else { "exact" },
            v.len(),
            p(0.5),
            p(0.9),
            v[v.len() - 1]
        );
    }
    assert_eq!(all_missed, 0, "loops went undetected");
}
