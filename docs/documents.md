# Your documents

Mightling indexes **your own files** on the machine that holds them, so the agent (in the terminal,
in Ask threads and in Work) can search them and cite what it uses. Nothing is sent anywhere: the
index works at `/airgapped on`, and `ling-admin audit egress --docs` proves it opens no connection.
The index is `ling-docs`, on Linux; a node gets its run-time files at install, or from
`ling-admin docs setup`.

## Collections

A collection is a folder you name. `~/Documents` and `~/Downloads` are collections by default, when
they exist; removing one is remembered.

```bash
ling docs add ~/Documents/contracts --name contracts
ling docs list
ling docs status                 # documents, chunks, failures, pages that need OCR, last run, disk
ling docs reindex contracts      # rebuild from the files
ling docs remove contracts       # deletes the index (which holds the text), never the files
```

The same is in **Settings › Your documents**, in the desktop app (with a folder chooser) and in
`ling web`: each folder with its documents, passages, what could not be read, when it was last
indexed and the disk its index takes, and buttons to add a folder, rebuild or remove one.

Hidden files, `~/.ssh`, `~/.gnupg`, `~/.config`, `~/.mightling`, package and build folders and files
named like secrets (`.env`, `*.pem`, `*.key`, `id_*`, `credentials*`) are never indexed, whatever
you add. A `.lingignore` file (gitignore syntax) in a folder can leave out more.

## What it reads

| Kind | Files | A passage is cited as |
|---|---|---|
| Text | `.txt` `.md` `.rst` `.org` `.tex` | `lines 120-180` |
| PDF (its text layer) | `.pdf` | `p.7` |
| Word and OpenDocument | `.docx` `.odt` | `para 12-30` (paragraphs, a table row being one) |
| Web pages | `.html` `.htm` `.xhtml` | `para 3-9` |
| E-mail | `.eml`, `.mbox` | `msg 3` (and `msg 3 · invoice.pdf p.2` for an attachment) |
| Tables | `.csv` `.tsv` | `rows 2-21` |
| Spreadsheets | `.xlsx` `.xlsm` `.xlsb` `.xls` `.ods` | `sheet Budget, rows 2-21` (the sheet's own rows) |
| Presentations | `.pptx` `.odp` | `slide 4` (its speaker notes included) |
| Scanned pages and pictures (OCR) | a PDF page without text, `.png` `.jpg` `.tif` `.webp` `.bmp` `.gif` | `p.7`, or `image` |

Headings give each passage its section (`Delivery > Rates`), a message its subject, a table or a
sheet its columns, a slide its title. A spreadsheet is read by its stored values (a formula's last
result). An e-mail's readable attachments (PDF, Word, OpenDocument, HTML, text, CSV) are indexed with
their message. Other files, such as installers, archives and videos, are left out without
being opened.

Scanned pages and pictures are read by OCR on this machine, after the rest of a folder is
searchable: the page's script is recognised first (Latin, Cyrillic, Chinese, Japanese, Korean,
Arabic), then its text read, about 3 seconds a page. The agent sees such a passage marked as a
scanned page, since OCR can misread words. `ling docs status` says how many pages were read, how
many with low confidence, and which pages still have no text and why. Set `docs_ocr = false` in
`dreamference.toml` to switch it off (pictures are then left out), or `docs_ocr_max_pages` (500) to
read fewer pages of a long scan.

## Searching

The agent has `docs_search` and `docs_read` when a collection exists, and `pdf_search` (read back
with `docs_read`) to search inside a long PDF from a URL or the workspace. You can search too:

```bash
ling docs search "notice period acme" [--collection contracts] [--type docx] [--after 2025-01-01]
ling docs read documents:42 --at "msg 3"      # or --page 7, --lines 120-180
```

Search combines keywords and meaning, in any language (search in the documents' own language first).

## Citations that open

The agent cites each passage as a link, such as **acme-msa.pdf, p.7**. Click it:

- in the **desktop app**, the file opens with your system's viewer;
- in **`ling web`**, a PDF opens in the browser (at its page) and a text file is shown; Word files,
  web pages and mail are downloaded, so nothing in them runs in Mightling's page.

Only a document of one of your collections can be opened, by its number in the index; a citation can
never name another file.

## Keeping current

While a `ling` session runs, the index follows your files: changes are noticed within seconds where
the system's file-watch limit allows, and a full scan every 10 minutes catches the rest. Indexing
runs in the background under a memory cap shared with the code index, never during a Night Shift or
benchmark run, and stops for a model load.
