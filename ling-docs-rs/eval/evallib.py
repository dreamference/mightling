"""ling-docs Phase 0: per-format extraction into located units, structure-first chunking, the snippet
hit test, and the two search backends (SQLite FTS5 BM25, dense vectors) plus their RRF fusion."""
import csv
import email
import email.policy
import io
import json
import mailbox
import os
import re
import sqlite3
import unicodedata

import numpy as np

STOP = set("""a an the of to in on for and or is are was were be been being by with as at from that this these
those it its what which who whom whose when where why how did does do done can could would should will shall may
might must about into than then there their they them he she his her we our you your i me my not no yes if but so
such any all some each per via vs also only more most less least very much many one two""".split())


def norm(s):
    """Letters and digits only (any script), lower-cased, NFKC: robust to hyphenation, ligatures and line
    breaks. Until the multilingual pass this kept Latin only, which made every Cyrillic, CJK or Arabic
    snippet normalise to the empty string (a trivial hit)."""
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[\W_]+", "", s)


CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]+")


def query_terms(q):
    """Word terms of a query in any script (stop words of English dropped), and its CJK runs apart:
    unicode61 has no word segmentation for Chinese or Japanese, so those go to the trigram table."""
    words = [t for t in re.findall(r"[^\W_]+", CJK_RUN.sub(" ", q.lower())) if t not in STOP and len(t) > 1]
    return words, CJK_RUN.findall(q)


# ---------- extraction: a document becomes units {loc, heading, text} ----------

def units_pdf(pages):
    return [{"loc": f"p.{i + 1}", "page": i + 1, "heading": "", "text": t} for i, t in enumerate(pages) if t.strip()]


def units_markdown(text):
    units, heading, start, buf = [], [], 1, []
    for n, line in enumerate(text.splitlines(), 1):
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            if "".join(buf).strip():
                units.append({"loc": f"lines {start}-{n - 1}", "heading": " > ".join(heading), "text": "\n".join(buf)})
            level = len(m.group(1))
            heading = heading[: level - 1] + [m.group(2).strip()]
            start, buf = n, [line]
        else:
            buf.append(line)
    if "".join(buf).strip():
        units.append({"loc": f"lines {start}-{n}", "heading": " > ".join(heading), "text": "\n".join(buf)})
    return units


def units_rst(text):
    lines = text.splitlines()
    units, heading, start, buf = [], "", 1, []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and lines[i].strip() and re.fullmatch(r"([=\-~^\"'`#*+])\1{2,}", lines[i + 1].strip()):
            if "".join(buf).strip():
                units.append({"loc": f"lines {start}-{i}", "heading": heading, "text": "\n".join(buf)})
            heading, start, buf = lines[i].strip(), i + 1, [lines[i]]
            i += 2
            continue
        buf.append(lines[i])
        i += 1
    if "".join(buf).strip():
        units.append({"loc": f"lines {start}-{len(lines)}", "heading": heading, "text": "\n".join(buf)})
    return units


def units_text(text, block=40):
    lines = text.splitlines()
    return [{"loc": f"lines {i + 1}-{min(i + block, len(lines))}", "heading": "",
             "text": "\n".join(lines[i:i + block])} for i in range(0, len(lines), block)
            if "".join(lines[i:i + block]).strip()]


def units_html(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("style, script, sup.reference, .mw-ref, .navbox, .reflist, .mw-references-wrap"):
        tag.decompose()
    units, h2, h3, buf = [], "", "", []

    def flush():
        if "".join(buf).strip():
            units.append({"loc": f"section {h2 or 'lead'}{' > ' + h3 if h3 else ''}",
                          "heading": " > ".join(x for x in (h2, h3) if x), "text": "\n".join(buf)})

    for el in soup.find_all(["h2", "h3", "p", "li", "dd", "td", "th", "caption"]):
        text = el.get_text(" ", strip=True)
        if not text:
            continue
        if el.name == "h2":
            flush(); buf, h2, h3 = [], text, ""
        elif el.name == "h3":
            flush(); buf, h3 = [], text
        elif el.name in ("td", "th"):
            if el.find_parent("table") and el.find_parent("table").find_parent(["td", "th"]):
                continue
            buf.append(text)
        else:
            if el.find_parent(["li", "td", "th"]) and el.name != "li":
                continue
            buf.append(text)
    flush()
    return units


def units_docx(path):
    from docx import Document
    units, h1, h2, buf, start, idx = [], "", "", [], 0, 0
    doc = Document(path)

    def flush():
        if "".join(buf).strip():
            units.append({"loc": f"paragraphs {start}-{idx}", "heading": " > ".join(x for x in (h1, h2) if x),
                          "text": "\n".join(buf)})

    for idx, p in enumerate(doc.paragraphs):
        style = (p.style.name or "") if p.style is not None else ""
        if style.startswith("Heading 1") or style == "Title":
            flush(); buf, h1, h2, start = [], p.text, "", idx
        elif style.startswith("Heading 2"):
            flush(); buf, h2, start = [], p.text, idx
        elif p.text.strip():
            buf.append(p.text)
    flush()
    return units


def _message_unit(msg):
    body = msg.get_body(preferencelist=("plain", "html"))
    text = body.get_content() if body is not None else ""
    head = f"From: {msg['From']}\nTo: {msg['To']}\nDate: {msg['Date']}\nSubject: {msg['Subject']}\n\n"
    return {"loc": f"message {msg['Message-ID']}", "heading": str(msg["Subject"] or ""), "text": head + text}


def units_mbox(path):
    out = []
    for m in mailbox.mbox(path, factory=lambda f: email.message_from_binary_file(f, policy=email.policy.default)):
        out.append(_message_unit(m))
    return out


def units_eml(path):
    with open(path, "rb") as f:
        return [_message_unit(email.message_from_binary_file(f, policy=email.policy.default))]


def units_csv(path, rows=20):
    with open(path, newline="", encoding="utf-8") as f:
        data = list(csv.reader(f))
    header, body = data[0], data[1:]
    units = []
    for i in range(0, len(body), rows):
        part = body[i:i + rows]
        text = "\n".join(", ".join(f"{h}: {v}" for h, v in zip(header, r)) for r in part)
        units.append({"loc": f"rows {i + 2}-{i + 1 + len(part)}", "heading": ", ".join(header), "text": text})
    return units


def extract(root, rel, pdf_pages=None):
    path = os.path.join(root, rel)
    ext = rel.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        return units_pdf(pdf_pages[rel]) if pdf_pages and rel in pdf_pages else []
    if ext == "md":
        return units_markdown(open(path, encoding="utf-8").read())
    if ext == "rst":
        return units_rst(open(path, encoding="utf-8").read())
    if ext == "txt":
        return units_text(open(path, encoding="utf-8", errors="replace").read())
    if ext in ("html", "htm"):
        return units_html(open(path, encoding="utf-8").read())
    if ext == "docx":
        return units_docx(path)
    if ext == "mbox":
        return units_mbox(path)
    if ext == "eml":
        return units_eml(path)
    if ext == "csv":
        return units_csv(path)
    return []


# ---------- chunking ----------

class Counter:
    """Token counts in the embedding model's tokenizer family (§7.2). The default is the XLM-R
    SentencePiece vocabulary shared by multilingual-e5 and paraphrase-multilingual-MiniLM; the English
    runs used bge-small's WordPiece."""
    def __init__(self, name="intfloat/multilingual-e5-small"):
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_pretrained(name)

    def __call__(self, text):
        return len(self.tok.encode(text, add_special_tokens=False).ids)


def _pieces(text):
    """Split into sentence-ish pieces, keeping newlines as boundaries."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        for line in para.split("\n"):
            out.extend(p for p in re.split(r"(?<=[.!?])\s+", line) if p.strip())
        out.append("")
    return out


def chunk(doc, units, count, target=512, overlap=0.15):
    """Structure first (units never split across documents, messages or pages kept by locator), then
    packed to `target` tokens with `overlap` carried over. Small consecutive units of the same
    document are merged up to the target (CSV row groups, short sections)."""
    chunks, cur, cur_tokens, cur_locs, cur_heading, cur_pages = [], [], 0, [], "", []

    def emit():
        if cur and "".join(cur).strip():
            chunks.append({"doc": doc, "loc": cur_locs[0] if len(cur_locs) == 1 else f"{cur_locs[0]} .. {cur_locs[-1]}",
                           "pages": sorted(set(cur_pages)), "heading": cur_heading, "text": "\n".join(cur)})

    for u in units:
        is_message = u["loc"].startswith("message ")
        u_tokens = count(u["text"])
        if is_message or (cur_tokens + u_tokens > target and cur):
            emit()
            cur, cur_tokens, cur_locs, cur_pages = [], 0, [], []
        cur_heading = u["heading"] or cur_heading
        if u_tokens <= target:
            cur.append(u["text"]); cur_tokens += u_tokens; cur_locs.append(u["loc"])
            cur_pages += [u["page"]] if "page" in u else []
            if is_message:
                emit(); cur, cur_tokens, cur_locs, cur_pages = [], 0, [], []
            continue
        # A long unit: window over its pieces.
        pieces = _pieces(u["text"])
        win, win_tokens = [], 0
        for p in pieces:
            pt = count(p) if p else 0
            if win_tokens + pt > target and win:
                cur, cur_locs = win, [u["loc"]]
                cur_pages = [u["page"]] if "page" in u else []
                emit()
                keep, kt = [], 0
                for q in reversed(win):
                    qt = count(q) if q else 0
                    if kt + qt > target * overlap:
                        break
                    keep.insert(0, q); kt += qt
                win, win_tokens = keep, kt
            win.append(p); win_tokens += pt
        cur, cur_tokens, cur_locs = win, win_tokens, [u["loc"]]
        cur_pages = [u["page"]] if "page" in u else []
        emit()
        cur, cur_tokens, cur_locs, cur_pages = [], 0, [], []
    emit()
    return chunks


def embed_text(c, title):
    head = title + (" | " + c["heading"] if c["heading"] else "")
    return head + "\n" + c["text"]


def is_hit(chunk_text, snippet):
    return norm(snippet) in norm(chunk_text)


# ---------- search backends ----------

class BM25:
    """SQLite FTS5 BM25 over title, heading and text (porter + unicode61). With `trigram`, a second
    FTS5 table (the built-in trigram tokenizer) answers the CJK runs of a query, and the two rankings
    are fused by RRF: unicode61 makes a whole run of Chinese or Japanese one token, so a query matches
    only if it repeats a run between punctuation marks verbatim."""
    def __init__(self, chunks, trigram=False):
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE VIRTUAL TABLE c USING fts5(title, heading, text, tokenize='porter unicode61')")
        rows = [(i, c["title"], c["heading"], c["text"]) for i, c in enumerate(chunks)]
        self.db.executemany("INSERT INTO c(rowid, title, heading, text) VALUES (?,?,?,?)", rows)
        self.trigram = trigram
        if trigram:
            self.db.execute("CREATE VIRTUAL TABLE t USING fts5(title, heading, text, tokenize='trigram')")
            self.db.executemany("INSERT INTO t(rowid, title, heading, text) VALUES (?,?,?,?)",
                                [r for r in rows if CJK_RUN.search(r[1] + r[2] + r[3])])

    def search(self, q, k=100):
        words, runs = query_terms(q)
        ranked = []
        if not self.trigram:
            words = words + [r for r in runs if len(r) > 1]
        if words:
            query = " OR ".join(f'"{t}"' for t in words)
            ranked = [r[0] for r in self.db.execute(
                "SELECT rowid FROM c WHERE c MATCH ? ORDER BY bm25(c, 2.0, 1.0, 1.0) LIMIT ?", (query, k))]
        if self.trigram and runs:
            grams = sorted({r[i:i + 3] for r in runs for i in range(max(1, len(r) - 2))})
            grams = [g for g in grams if len(g) == 3]
            if grams:
                query = " OR ".join(f'"{g}"' for g in grams)
                tri = [r[0] for r in self.db.execute(
                    "SELECT rowid FROM t WHERE t MATCH ? ORDER BY bm25(t, 2.0, 1.0, 1.0) LIMIT ?", (query, k))]
                ranked = rrf(ranked, tri, top=k) if ranked else tri
        return ranked


class Dense:
    def __init__(self, vectors):
        v = np.asarray(vectors, dtype=np.float32)
        self.v = v / np.linalg.norm(v, axis=1, keepdims=True)

    def search(self, qv, k=100):
        q = np.asarray(qv, dtype=np.float32)
        q = q / np.linalg.norm(q)
        s = self.v @ q
        idx = np.argpartition(-s, min(k, len(s) - 1))[:k]
        return [int(i) for i in idx[np.argsort(-s[idx])]]


def rrf(*rankings, k=60, top=100):
    score = {}
    for r in rankings:
        for rank, i in enumerate(r):
            score[i] = score.get(i, 0.0) + 1.0 / (k + rank + 1)
    return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])][:top]


def metrics(results, questions, chunks):
    """results: list of ranked chunk-id lists aligned with questions."""
    out = {"n": len(questions)}
    hits5 = hits10 = docs10 = 0
    rr = 0.0
    by_kind, by_fmt, by_lang, by_group = {}, {}, {}, {}
    for q, ranked in zip(questions, results):
        first = next((r for r, i in enumerate(ranked[:10]) if chunks[i]["doc"] == q["doc"]
                      and is_hit(chunks[i]["text"], q["snippet"])), None)
        h10 = first is not None
        hits10 += h10
        hits5 += first is not None and first < 5
        rr += 1.0 / (first + 1) if h10 else 0.0
        docs10 += any(chunks[i]["doc"] == q["doc"] for i in ranked[:10])
        group = ("en" if q.get("lang", "en") == "en" else "other") + "-" + q["kind"]
        for key, bucket in ((q["kind"], by_kind), (q["doc"].split("/")[0], by_fmt), (q.get("lang", "en"), by_lang),
                            (group, by_group)):
            b = bucket.setdefault(key, [0, 0])
            b[0] += h10; b[1] += 1
    n = len(questions)
    out.update(recall5=hits5 / n, recall10=hits10 / n, mrr10=rr / n, doc_recall10=docs10 / n,
               by_kind={k: f"{a}/{b}" for k, (a, b) in by_kind.items()},
               by_format={k: f"{a}/{b}" for k, (a, b) in by_fmt.items()},
               by_lang={k: f"{a}/{b}" for k, (a, b) in by_lang.items()},
               by_group={k: f"{a}/{b}" for k, (a, b) in by_group.items()})
    return out


def load_json(path):
    return json.load(open(path))
