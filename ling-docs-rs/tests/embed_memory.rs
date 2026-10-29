//! The index worker's memory while it embeds, measured with a counting allocator. Its own test
//! binary: the allocator counts every allocation of the process, so nothing else may run beside.

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};

use ling_docs::extract::{Event, Extracted, Unit};

struct Counting;

static LIVE: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let p = unsafe { System.alloc(layout) };
        if !p.is_null() {
            let live = LIVE.fetch_add(layout.size(), Ordering::Relaxed) + layout.size();
            PEAK.fetch_max(live, Ordering::Relaxed);
        }
        p
    }
    unsafe fn dealloc(&self, p: *mut u8, layout: Layout) {
        unsafe { System.dealloc(p, layout) };
        LIVE.fetch_sub(layout.size(), Ordering::Relaxed);
    }
}

#[global_allocator]
static ALLOC: Counting = Counting;

struct Ones;

impl ling_docs::embed::Embed for Ones {
    fn dim(&self) -> usize {
        4
    }
    fn name(&self) -> &str {
        "ones"
    }
    fn embed(&self, _text: &str) -> anyhow::Result<Vec<f32>> {
        Ok(vec![1.0; 4])
    }
}

#[test]
fn the_chunks_waiting_for_vectors_are_never_held_all_at_once() {
    let dir = tempfile::tempdir().unwrap();
    let db = dir.path().join("c.db");
    let events = dir.path().join("events.jsonl");
    // 64 documents of about 900 KB of words each: over 48 MB of chunk text.
    let docs = 64;
    let conn = ling_docs::store::open_rw(&db).unwrap();
    let mut lines = String::new();
    for id in 1..=docs {
        conn.execute(
            "INSERT INTO documents(id, path, kind, size, mtime_ns, status, title) VALUES (?1, ?2, 'text', 10, 1, 'pending', 'a')",
            rusqlite::params![id, format!("d{id}.txt")],
        )
        .unwrap();
        let done = Event::Done {
            id,
            result: Extracted {
                units: vec![Unit {
                    loc: "lines 1-1".into(),
                    page: None,
                    line_first: Some(1),
                    line_last: Some(1),
                    heading: String::new(),
                    group: None,
                    text: format!("word{id} ").repeat(1 << 17),
                }],
                ..Default::default()
            },
        };
        lines.push_str(&serde_json::to_string(&done).unwrap());
        lines.push('\n');
    }
    std::fs::write(&events, lines).unwrap();
    let mut log = Vec::new();
    ling_docs::index::index_worker(
        "docs",
        &db,
        &events,
        &Ones,
        &ling_docs::chunk::Words,
        &mut log,
    )
    .unwrap();
    // A run killed after writing the documents: every chunk still waits for its vector.
    conn.execute("UPDATE chunks SET vector = NULL", []).unwrap();
    let waiting: i64 = conn
        .query_row("SELECT sum(length(text)) FROM chunks", [], |r| r.get(0))
        .unwrap();
    assert!(waiting > 48 << 20, "{waiting}");
    std::fs::write(&events, b"").unwrap();
    let before = LIVE.load(Ordering::Relaxed);
    PEAK.store(before, Ordering::Relaxed);
    let (_, done) = ling_docs::index::index_worker(
        "docs",
        &db,
        &events,
        &Ones,
        &ling_docs::chunk::Words,
        &mut log,
    )
    .unwrap();
    let grew = PEAK.load(Ordering::Relaxed) - before;
    let chunks: i64 = conn
        .query_row(
            "SELECT count(*) FROM chunks WHERE vector IS NOT NULL",
            [],
            |r| r.get(0),
        )
        .unwrap();
    assert_eq!(done as i64, chunks);
    assert!(
        grew < 8 << 20,
        "embedding {waiting} bytes of chunks held {grew} bytes at once"
    );
}
