"""Mechanical Mightling -> Ragon rename (specs/DREAMFERENCE_RENAME_RAGON.md §9).

The same `transform` is applied to file contents and to path names, so a reference to a file and the
file's new name always agree. Run it on a branch that is thrown away after rehearsal, or once on the
release branch:

    .venv/bin/python scripts/rename_ragon.py --dry                 # list what would change, touch nothing
    .venv/bin/python scripts/rename_ragon.py --report out.txt      # rename, write the report
    .venv/bin/python scripts/rename_ragon.py --check               # leftover check only (exit 1 on a hit)
    .venv/bin/python scripts/rename_ragon.py --paths a b           # only these files (merged old branches)
    .venv/bin/python scripts/rename_ragon.py --verify              # the checks of §9.3, in order

`--repo engine` selects the engine repository's profile (no overlays, no compatibility edits; the
rules are the same). `--root DIR` runs somewhere other than the current directory.

Names that must keep the old spelling (the release signing namespaces, lock files, the overlay peer
names, a model called Ling-3.0, ...) are protected; the migration modules come from
`scripts/ragon_overlay/` in their final names. A second run changes nothing.
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

# Files whose contents are left exactly as they are: history, the rename's own record, and code whose
# job is to know the old names. Their PATHS still rename (except PATH_KEEP).
EXCLUDE_FILES = {
    "specs/DREAMFERENCE_RENAME_MIGHTLING.md",
    "specs/DREAMFERENCE_RENAME_RAGON.md",
    "scripts/rename_mightling.py",
    "scripts/rename_ragon.py",
    "tests/test_rename_ragon.py",
    "desktop/electron/predecessor.json",  # names Mightling on purpose (the desktop identity)
    "desktop/electron/windows/predecessor.nsh",
    # the migration modules: the overlay brings them in their final names
    "ling-rs/src/rename.rs",
    "ragon-rs/src/rename.rs",
    "dreamference/cli/legacy_name_migration.py",
    "tests/test_legacy_name_migration.py",
}
EXCLUDE_PREFIXES = (
    "codex/",
    "docs/dev/ragon-release/",  # the release notes for the rename name Mightling on purpose
    "desktop/ui/src/protocol/",
    "tests/fixtures/code_index/",
    "ling-docs-rs/eval/",  # content only: the corpus and its checksums stay as published
    "ragon-docs-rs/eval/",  # the same folder after the rename (the leftover check runs on both)
    "specs/DREAMFERENCE_RELEASE_1.",  # release notes 1.4 to 1.8 (history)
    "paper/puffin.",
    "scripts/ragon_overlay/",
)
# The transition assets are renamed like everything else, then given the older names by COMPAT_EDITS;
# the leftover check lets those through because the files must know every old name (spec §3).
LEFTOVER_EXEMPT = {"install.sh", "install.ps1", ".github/workflows/release.yml"}
# Paths that keep their name (their content is excluded too).
PATH_KEEP = {
    "specs/DREAMFERENCE_RENAME_MIGHTLING.md",
    "specs/DREAMFERENCE_RENAME_RAGON.md",
    "scripts/rename_mightling.py",
    "scripts/rename_ragon.py",
    "tests/test_rename_ragon.py",
}
PATH_KEEP_PREFIXES = ("paper/puffin.", "scripts/ragon_overlay/", "specs/DREAMFERENCE_RELEASE_1.")

# Names kept on purpose (spec §3). Literal strings, replaced by a placeholder around the rules.
PROTECT = [
    "mightling-release-key",
    "mightling-release",  # the SSHSIG namespace of SHA256SUMS
    "mightling-adapter",  # the SSHSIG namespace of adapter manifests
    "mightling-relay",
    # the enrolment wire protocol: a 1.8 node and a 1.9 client (or the reverse) must still agree
    "mightling-enrol-client|",
    "mightling-enrol-server|",
    "/mightling/enrol",
    "mightling-box-ready",
    "mightling-box-removed",
    ".ling-gpu",
    ".ling-build",
    "Ling-3.0",  # a model's name
    # NetBird peers of existing nodes: `mightling-<node id>`
    "mightling-<id>",
    "mightling-<node id>",
    "mightling-<its id>",
    "not mightling-",  # the message that says a peer name is not the overlay one
    'NODE_PEER_PREFIX: Final[str] = "mightling-"',
    'OVERLAY_PREFIX: &str = "mightling-"',
    # SWE-bench stored keys
    "puffin_version",
    "puffin_code_calls",
    "puffin_code_users",
    "PUFFIN_GMAIL_SECRET",  # the env name an old container hands the service (a test keeps it)
    "/puffin-images/",
    # the SCIP tables are renamed (RULES); the schema hash's filter keeps the old pattern beside the new one
    "puffin\\\\_%",
]

# The same, as regular expressions: a peer name is `mightling-` and a node id, whatever the example.
PROTECT_RE = [re.compile(r"mightling-(?=[0-9a-f]{8}-|\{(?:NODE_ID|node_id|self\.node_id)\}|(?:x|ab)\.netbird)")]

RULES = [
    (r"DREAMFERENCE_MIGHTLING_LING_", "DREAMFERENCE_RAGON_"),
    (r"DREAMFERENCE_LING_ENGINE", "DREAMFERENCE_RAGON_ENGINE"),
    (r"MIGHTLING", "RAGON"),
    (r"Mightling", "Ragon"),
    (r"mightling", "ragon"),
    # Puffin leftovers that are not product names (build caches, the AppArmor profile, ...)
    (r"puffin-(codex|web|code-build|bwrap)", r"ragon-\1"),
    (r"PUFFIN_", "RAGON_"),
    (r"puffin_(names|relationships|meta)", r"ragon_\1"),  # the SCIP stores' own tables
    (r"\.lingignore", ".ragonignore"),
    (r"(?<![A-Za-z0-9])LING(?![A-Za-z])", "RAGON"),
    (r"(?:(?<![A-Za-z0-9])|(?<=\\[bnrt]))ling(?![A-Za-z])", "ragon"),  # \bling-code in a regex, \nling
    (r"(?<![A-Za-z])ling(?=[A-Z])", "ragon"),
    (r"(?<![A-Za-z])Ling(?![a-z])", "Ragon"),
]
COMPILED = [(re.compile(a), b) for a, b in RULES]

LINK_RULE = re.compile(r"DREAMFERENCE_(MIGHTLING_LING|MIGHTLING|LING_ENGINE)_([A-Z0-9_]*)(?=\.md)")
LINK_REPL = {"MIGHTLING_LING": "RAGON", "MIGHTLING": "RAGON", "LING_ENGINE": "RAGON_ENGINE"}

# Puffin is the name before Mightling: the bird, the 1.4.x transition assets and the legacy names stay
# (the rename of the build caches and the AppArmor profile is in RULES), so the check does not look for it.
# What the leftover check looks for, after the protected words are masked.
LEFTOVER = re.compile(
    r"mightling|(?:(?<![A-Za-z0-9])|(?<=\\[bnrt]))ling(?![A-Za-z])|\.lingignore", re.I
)

# A prose line that talks about the rename itself or names an old name on purpose stays as it is.
LEGACY_LINE = re.compile(
    r"renamed|rename\b|was called|is now Ragon|became Ragon|before the product|Puffin 1\.|Mightling 1\.|"
    r"puffin bird|old name|earlier name|legacy name|sold under the name|still runs Mightling|"
    r"Mightling is now Ragon|called Mightling|called Puffin|named Mightling|named Puffin",
    re.I,
)
DATE = re.compile(r"20\d\d-\d\d-\d\d")
PROSE = (".md", ".txt", ".tex")
RENAME_NOTE = "Mightling was renamed Ragon in 1.9.0."
OVERLAY_DIR = "scripts/ragon_overlay"


def is_history(path, line):
    """Tells whether `line` of the prose file `path` is history that keeps the old name."""
    if not path.endswith(PROSE):
        return False
    if LEGACY_LINE.search(line):
        return True
    return path.startswith("specs/") and bool(DATE.search(line))


def transform(text):
    """Applies the rules to `text` (a line, a file's contents or a path), protected words kept."""
    saved = {}
    for i, word in enumerate(PROTECT):
        if word in text:
            token = f"\x00P{i}\x00"
            saved[token] = word
            text = text.replace(word, token)
    for n, pattern in enumerate(PROTECT_RE):

        def keep(match, n=n):
            token = f"\x00R{n}_{len(saved)}\x00"
            saved[token] = match.group(0)
            return token

        text = pattern.sub(keep, text)
    for pattern, repl in COMPILED:
        text = pattern.sub(repl, text)
    for token, word in saved.items():
        text = text.replace(token, word)
    return text


def transform_links(text):
    """Renames spec file names in links, for files whose other contents stay as they are."""
    return LINK_RULE.sub(lambda m: "DREAMFERENCE_" + LINK_REPL[m.group(1)] + "_" + m.group(2), text)


def transform_text(path, text):
    """Transforms a file's contents line by line, history lines of prose files left alone."""
    out = []
    for line in text.splitlines(keepends=True):
        out.append(transform_links(line if is_history(path, line) else transform(line)))
    new = "".join(out)
    if (
        path.startswith("specs/")
        and path.endswith(".md")
        and new != text
        and RENAME_NOTE not in new
    ):
        head, sep, rest = new.partition("\n")
        new = head + sep + "\n" + RENAME_NOTE + "\n" + rest
    return new


def new_path(path):
    """The path after the rename."""
    if path in PATH_KEEP or path.startswith(PATH_KEEP_PREFIXES):
        return path
    return transform(path)


def content_excluded(path):
    return path in EXCLUDE_FILES or path.startswith(EXCLUDE_PREFIXES)


def masked(line):
    """`line` with the protected words blanked, for the leftover check."""
    for word in PROTECT + COMPAT_KEEP:
        line = line.replace(word, " ")
    for pattern in PROTECT_RE:
        line = pattern.sub(" ", line)
    return line


def read_text(root, path):
    try:
        with pathlib.Path(os.path.join(root, path)).open(encoding="utf-8") as handle:
            return handle.read()
    except (UnicodeDecodeError, OSError):
        return None


def tracked(root):
    out = subprocess.run(
        ["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True
    ).stdout
    return [p for p in out.decode().split("\0") if p and os.path.lexists(os.path.join(root, p))]


def leftovers(root, files=None):
    """Lists `path:line: text` for every old name outside the allowlist."""
    hits = []
    for path in files if files is not None else tracked(root):
        if (
            content_excluded(path)
            or path in LEFTOVER_EXEMPT
            or pathlib.Path(os.path.join(root, path)).is_symlink()
        ):
            continue
        text = read_text(root, path)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if LEFTOVER.search(masked(line)) and not is_history(path, line):
                hits.append(f"{path}:{number}: {line.strip()[:160]}")
    for path in files if files is not None else tracked(root):
        if transform(path) != path and new_path(path) != path:
            hits.append(f"{path}: path still has an old name")
    return hits


# Old names the compatibility edits write on purpose: the leftover check lets them through.
COMPAT_KEEP = [
    "MIGHTLING_LEGACY_MIGRATION",
    "_mightling-node._tcp",
    "ai.dreamference.ling-engine.commit",
    "_mightling-forge._tcp",
    "/scripts/rename_mightling.py",
    "**Search your own documents.** Mightling now keeps",
    "mightling.slice/mightling-index.slice",
    "ling-swe-*",
    "still runs Mightling: `ling update` there",
]

# Compatibility edits applied to the renamed tree (spec §4 and §5): the older name stays readable for
# one release. Each is (path, old text, new text, expected occurrences); a missing anchor aborts.
OLD_INSTALL_SH_ANCHOR = 'for old in puffin puffin-search puffin-fetch puffin-code puffin-app puffin-admin; do\n'
OLD_INSTALL_SH = (
    "# Mightling (1.5 to 1.8) became Ragon in 1.9.0 (specs/DREAMFERENCE_RENAME_RAGON.md §4.2, L1): the\n"
    "# same move, for the same reasons. `ragon` moves the agent's home and the rest the first time it runs.\n"
    'MIGHTLING_DIR="$(dirname "$INSTALL_DIR")/mightling"\n'
    'if [ -d "$MIGHTLING_DIR" ] && [ ! -e "$INSTALL_DIR" ]; then\n'
    '    mv "$MIGHTLING_DIR" "$INSTALL_DIR"\n'
    "    for pair in ling:ragon ling-search:ragon-search ling-fetch:ragon-fetch ling-code:ragon-code ling-docs:ragon-docs ling-app:ragon-app; do\n"
    '        old="${pair%%:*}"; new="${pair#*:}"\n'
    '        [ -e "$INSTALL_DIR/bin/$old" ] && [ ! -e "$INSTALL_DIR/bin/$new" ] && mv "$INSTALL_DIR/bin/$old" "$INSTALL_DIR/bin/$new"\n'
    "    done\n"
    '    say "Mightling is now Ragon: moved $MIGHTLING_DIR to $INSTALL_DIR"\n'
    "fi\n"
    "for old in ling ling-search ling-fetch ling-code ling-docs ling-app ling-admin; do\n"
    '    [ -L "$LINK_DIR/$old" ] && rm -f "$LINK_DIR/$old"\n'
    "done\n"
)
SIGN_STEP = "      - name: Write and sign SHA256SUMS\n"
OLD_ASSETS_STEP = r"""      # 1.9.0 publishes the binaries three times, under ragon-*, ling-* (for a 1.8.x `ling update`) and
      # puffin-* (for 1.4.x), one SHA256SUMS over all of them (spec §4.1). The bytes are the same, so
      # the older sums files are the Ragon ones with the names changed. From 1.9.1 on only ragon-*.
      - name: Add the older asset names
        if: ${{ inputs.version == '1.9.0' }}
        run: |
          while IFS= read -r file; do
            dir="$(dirname "$file")"; base="$(basename "$file")"
            case "$base" in
              ragon-*.gz) cp "$file" "$dir/ling${base#ragon}" ;;
              ragon-*.sha256sums) sed 's/ragon/ling/' "$file" > "$dir/ling${base#ragon}" ;;
            esac
            case "$base" in
              ragon-aarch64-*.gz|ragon-x86_64-*.gz|ragon-search-*.gz|ragon-fetch-*.gz|ragon-code-*.gz)
                cp "$file" "$dir/puffin${base#ragon}" ;;
              ragon-aarch64-*.sha256sums|ragon-x86_64-*.sha256sums)
                grep -v -E 'ragon-(docs|app)' "$file" | sed 's/ragon/puffin/' > "$dir/puffin${base#ragon}" ;;
            esac
          done < <(find artifacts -type f -name 'ragon-*' | sort)
"""

SCIP_FILTER_OLD = "WHERE name NOT LIKE 'puffin\\\\_%' ESCAPE '\\\\' AND name NOT LIKE 'sqlite\\\\_%' ESCAPE '\\\\'"
SCIP_FILTER_NEW = (
    "WHERE name NOT LIKE 'ragon\\\\_%' ESCAPE '\\\\' AND name NOT LIKE 'puffin\\\\_%' ESCAPE '\\\\'\n"
    "           AND name NOT LIKE 'sqlite\\\\_%' ESCAPE '\\\\'"
)
SCIP_HAS_OLD = "SELECT count(*) FROM sqlite_master WHERE name = 'ragon_names'"
SCIP_HAS_NEW = "SELECT count(*) FROM sqlite_master WHERE name IN ('ragon_names', 'puffin_names')"
SCIP_OPEN_ANCHOR = '        let docs = {\n            let mut stmt = conn.prepare("SELECT relative_path FROM documents")?;\n'
SCIP_OPEN_NEW = (
    "        expose_legacy_tables(&conn)?;\n" + SCIP_OPEN_ANCHOR
)
SCIP_HELPER_ANCHOR = "/// SHA-256 of the ordered `sql` of `sqlite_master`"
SCIP_HELPER = """/// A store written before the rename (1.8 and earlier) calls its own tables `puffin_*`. A store is opened
/// read-only, so they are shown under their new names by temporary views (the connection's own, gone
/// with it); the next index run writes a store with the new names (spec DREAMFERENCE_RENAME_RAGON.md §3).
fn expose_legacy_tables(conn: &Connection) -> Result<()> {
    let present = |name: &str| -> rusqlite::Result<bool> {
        conn.query_row(
            "SELECT count(*) FROM (SELECT name FROM sqlite_master UNION ALL SELECT name FROM sqlite_temp_master)
             WHERE name = ?1",
            [name],
            |r| r.get::<_, i64>(0),
        )
            .map(|n| n > 0)
    };
    for table in ["names", "relationships", "meta"] {
        let (old, new) = (format!("puffin_{table}"), format!("ragon_{table}"));
        if present(&old)? && !present(&new)? {
            conn.execute_batch(&format!("CREATE TEMP VIEW {new} AS SELECT * FROM {old}"))?;
        }
    }
    Ok(())
}

"""
SCIP_TEST_ANCHOR = "#[cfg(test)]\nmod tests {\n"
SCIP_TEST = """#[cfg(test)]
mod tests {
    #[test]
    fn a_store_with_the_older_table_names_is_read_through_views() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("old.db");
        {
            let conn = super::Connection::open(&path).unwrap();
            conn.execute_batch(
                "CREATE TABLE puffin_names (symbol_id INTEGER NOT NULL, name TEXT NOT NULL);
                 INSERT INTO puffin_names VALUES (1, 'main');",
            )
            .unwrap();
        }
        let conn = super::Connection::open_with_flags(&path, super::OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
        super::expose_legacy_tables(&conn).unwrap();
        let name: String = conn.query_row("SELECT name FROM ragon_names", [], |r| r.get(0)).unwrap();
        assert_eq!(name, "main");
        super::expose_legacy_tables(&conn).unwrap(); // a second call finds the view and does nothing
    }

"""

LOC_ANCHOR = 'pub const SERVICE_TYPE: &str = "_ragon-node._tcp.local.";\n'
LOC_NEW = (
    LOC_ANCHOR
    + "\n/// The type 1.5 to 1.8 nodes announce; a 1.9 client browses it beside the new one for one release.\n"
    + 'pub const LEGACY_SERVICE_TYPE: &str = "_mightling-node._tcp.local.";\n'
)
SLICE_ANCHOR = "        let Ok(entries) = std::fs::read_dir(slice_cgroup()) else { return Vec::new() };\n        entries\n            .flatten()\n"
SLICE_NEW = (
    "        // Runs started by a 1.8 binary are in the older slice; they count against the same budget.\n"
    "        let entries: Vec<std::fs::DirEntry> = [slice_cgroup(), legacy_slice_cgroup()]\n"
    "            .iter()\n"
    "            .filter_map(|dir| std::fs::read_dir(dir).ok())\n"
    "            .flat_map(|dir| dir.flatten().collect::<Vec<_>>())\n"
    "            .collect();\n"
    "        entries\n"
    "            .into_iter()\n"
)
SLICE_FN_ANCHOR = "fn read_u64(path: &Path) -> Option<u64> {\n"
SLICE_FN = (
    "fn legacy_slice_cgroup() -> PathBuf {\n"
    '    PathBuf::from(slice_cgroup().to_string_lossy().replace("ragon.slice/ragon-index.slice", "mightling.slice/mightling-index.slice"))\n'
    "}\n\n"
)
NODE_DEADLINE = "        let deadline = Instant::now() + browse_timeout(wanted);\n"
NODE_RECV_OLD = (
    "            match events.recv_timeout(limit - now) {\n"
    "                Ok(mdns_sd::ServiceEvent::ServiceResolved(service)) => {\n"
)
NODE_RECV_NEW = (
    "            let slice = (limit - now).min(Duration::from_millis(50));\n"
    "            let waited = Instant::now();\n"
    "            let mut event = events.recv_timeout(slice).ok();\n"
    "            if event.is_none() {\n"
    "                event = legacy_events.as_ref().and_then(|legacy| legacy.try_recv().ok());\n"
    "            }\n"
    "            let Some(event) = event else {\n"
    "                std::thread::sleep(slice.saturating_sub(waited.elapsed()));\n"
    "                continue;\n"
    "            };\n"
    "            match event {\n"
    "                mdns_sd::ServiceEvent::ServiceResolved(service) => {\n"
)
NODE_TAIL_OLD = "                Ok(_) => {}\n                Err(_) => break,\n"
NODE_STOP = "        let _ = daemon.stop_browse(locator::SERVICE_TYPE);\n"
NODE_STRIP = "        .strip_suffix(locator::SERVICE_TYPE)\n"

SWE_UNIT_OLD = '                    "ragon-swe-*",\n'
COMPAT_EDITS = [
    ("ragon-rs/node-locator/src/lib.rs", LOC_ANCHOR, LOC_NEW, 1),
    ("ragon-web-rs/src/node_locator.rs", LOC_ANCHOR, LOC_NEW, 1),
    ("ragon-code-rs/src/index/host.rs", SLICE_ANCHOR, SLICE_NEW, 1),
    ("ragon-docs-rs/src/host.rs", SLICE_ANCHOR, SLICE_NEW, 1),
    ("ragon-code-rs/src/index/host.rs", SLICE_FN_ANCHOR, SLICE_FN + SLICE_FN_ANCHOR, 1),
    ("ragon-docs-rs/src/host.rs", SLICE_FN_ANCHOR, SLICE_FN + SLICE_FN_ANCHOR, 1),
    (
        "ragon-rs/src/node.rs",
        NODE_DEADLINE,
        "        let legacy_events = daemon.browse(locator::LEGACY_SERVICE_TYPE).ok();\n" + NODE_DEADLINE,
        1,
    ),
    ("ragon-rs/src/node.rs", NODE_RECV_OLD, NODE_RECV_NEW, 1),
    ("ragon-rs/src/node.rs", NODE_TAIL_OLD, "                _ => {}\n", 1),
    (
        "ragon-rs/src/node.rs",
        NODE_STOP,
        NODE_STOP + "        let _ = daemon.stop_browse(locator::LEGACY_SERVICE_TYPE);\n",
        1,
    ),
    (
        "ragon-rs/src/node.rs",
        NODE_STRIP,
        NODE_STRIP + "        .or_else(|| fullname.strip_suffix(locator::LEGACY_SERVICE_TYPE))\n",
        1,
    ),
    (
        "ragon-rs/src/node.rs",
        "    pub main: bool,\n}\n",
        "    pub main: bool,\n    /// Found only through the type the older releases announce: the node has not updated.\n    pub legacy: bool,\n}\n",
        1,
    ),
    (
        "ragon-rs/src/node.rs",
        '        main: record("main").is_some_and(|value| value != "0"),\n    })\n',
        '        main: record("main").is_some_and(|value| value != "0"),\n'
        "        legacy: fullname.ends_with(locator::LEGACY_SERVICE_TYPE),\n    })\n",
        1,
    ),
    (
        "ragon-rs/src/node.rs",
        "                node: Node { address, ..advert.node },\n                ..advert\n",
        "                node: Node { address, ..advert.node },\n                legacy: existing.legacy && advert.legacy,\n                ..advert\n",
        1,
    ),
    (
        "ragon-rs/src/node.rs",
        '            state: "ready".to_string(),\n            main: true,\n        }\n',
        '            state: "ready".to_string(),\n            main: true,\n            legacy: false,\n        }\n',
        1,
    ),
    (
        "ragon-rs/src/remote_join.rs",
        '            state: "ready".into(),\n            main,\n        }\n',
        '            state: "ready".into(),\n            main,\n            legacy: false,\n        }\n',
        1,
    ),
    (
        "ragon-rs/src/node.rs",
        '            lines.push(format!("      off the LAN: {} (remote access)", node.remote));\n        }\n',
        '            lines.push(format!("      off the LAN: {} (remote access)", node.remote));\n        }\n'
        "        if advert.legacy {\n"
        '            lines.push("      still runs Mightling: `ling update` there".to_string());\n'
        "        }\n",
        1,
    ),
    (
        "dreamference/forge/forge_settings.py",
        'SERVICE_TYPE: Final[str] = "_ragon-forge._tcp"\n',
        'SERVICE_TYPE: Final[str] = "_ragon-forge._tcp"\n'
        "# The type 1.5 to 1.8 nodes announce the forge under: published and browsed for one release.\n"
        'LEGACY_SERVICE_TYPE: Final[str] = "_mightling-forge._tcp"\n',
        1,
    ),
    (
        "dreamference/forge/forge_service.py",
        "    MEMORY_CAP_GIB,\n    ROLE_MIRROR,\n",
        "    LEGACY_SERVICE_TYPE,\n    MEMORY_CAP_GIB,\n    ROLE_MIRROR,\n",
        1,
    ),
    (
        "dreamference/forge/forge_service.py",
        "        for entry in NodeBrowser.browse_service(SERVICE_TYPE):\n",
        "        for entry in NodeBrowser.browse_service(SERVICE_TYPE) + NodeBrowser.browse_service(LEGACY_SERVICE_TYPE):\n",
        1,
    ),
    (
        "dreamference/forge/forge_service.py",
        "        for entry in NodeBrowser.browse_service(SERVICE_TYPE) + NodeBrowser.browse():\n",
        "        legacy = NodeBrowser.browse_service(LEGACY_SERVICE_TYPE)\n"
        "        for entry in NodeBrowser.browse_service(SERVICE_TYPE) + legacy + NodeBrowser.browse():\n",
        1,
    ),
    (
        "dreamference/node/node_service_file.py",
        'FORGE_SERVICE_TYPE: Final[str] = "_ragon-forge._tcp"\n',
        'FORGE_SERVICE_TYPE: Final[str] = "_ragon-forge._tcp"\n'
        'LEGACY_FORGE_SERVICE_TYPE: Final[str] = "_mightling-forge._tcp"\n',
        1,
    ),
    (
        "dreamference/node/node_service_file.py",
        '            lines += ["  <service>", f"    <type>{FORGE_SERVICE_TYPE}</type>", f"    <port>{int(forge_port)}</port>",\n'
        "                      f\"    <txt-record>{escape(f'node={node_id}')}</txt-record>\",\n"
        "                      f\"    <txt-record>{escape(f'role={role}')}</txt-record>\", \"  </service>\"]\n",
        "            for forge_type in (FORGE_SERVICE_TYPE, LEGACY_FORGE_SERVICE_TYPE):\n"
        "                lines += [\n"
        '                    "  <service>",\n'
        '                    f"    <type>{forge_type}</type>",\n'
        '                    f"    <port>{int(forge_port)}</port>",\n'
        "                    f\"    <txt-record>{escape(f'node={node_id}')}</txt-record>\",\n"
        "                    f\"    <txt-record>{escape(f'role={role}')}</txt-record>\",\n"
        '                    "  </service>",\n'
        "                ]\n",
        1,
    ),
    (
        "dreamference/vllm_server/ragon_serve_launch_builder.py",
        'RAGON_ENGINE_COMMIT_LABEL: Final[str] = "ai.dreamference.ragon-engine.commit"\n',
        'RAGON_ENGINE_COMMIT_LABEL: Final[str] = "ai.dreamference.ragon-engine.commit"\n'
        "# The label of an image built before the rename (a 1.8 pin): still read for one release.\n"
        'LEGACY_ENGINE_COMMIT_LABEL: Final[str] = "ai.dreamference.ling-engine.commit"\n',
        1,
    ),
    (
        "dreamference/vllm_server/ragon_serve_launch_builder.py",
        '        commit = str(labels.get(RAGON_ENGINE_COMMIT_LABEL) or "unknown")\n',
        "        commit = str(\n"
        "            labels.get(RAGON_ENGINE_COMMIT_LABEL) or labels.get(LEGACY_ENGINE_COMMIT_LABEL) or \"unknown\"\n"
        "        )\n",
        1,
    ),
    ("ragon-code-rs/src/scip_store.rs", SCIP_FILTER_OLD, SCIP_FILTER_NEW, 1),
    ("ragon-code-rs/src/scip_store.rs", SCIP_HAS_OLD, SCIP_HAS_NEW, 1),
    ("ragon-code-rs/src/scip_store.rs", SCIP_OPEN_ANCHOR, SCIP_OPEN_NEW, 1),
    ("ragon-code-rs/src/scip_store.rs", SCIP_HELPER_ANCHOR, SCIP_HELPER + SCIP_HELPER_ANCHOR, 1),
    ("ragon-code-rs/src/scip_store.rs", SCIP_TEST_ANCHOR, SCIP_TEST, 1),
    (
        "install.sh",
        OLD_INSTALL_SH_ANCHOR,
        OLD_INSTALL_SH + OLD_INSTALL_SH_ANCHOR,
        1,
    ),
    (".github/workflows/release.yml", SIGN_STEP, OLD_ASSETS_STEP + SIGN_STEP, 1),
    (
        "install.ps1",
        '$_.Name -like "Ragon-mDNS-*" }',
        '$_.Name -like "Ragon-mDNS-*" -or $_.Name -like "Mightling-mDNS-*" }',
        1,
    ),
    (
        "install.ps1",
        '    Get-NetFirewallRule | Where-Object { $_.Name -like "Ragon-mDNS-*" -or',
        '    # Rules of the Mightling install (1.5 to 1.8) go too: their programs are in its folder.\n'
        '    $Mine = @($Mine) + ((Join-Path $env:LOCALAPPDATA "Programs\\Mightling").TrimEnd("\\") + "\\")\n'
        '    Get-NetFirewallRule | Where-Object { $_.Name -like "Ragon-mDNS-*" -or',
        1,
    ),
    (
        "tests/test_install_ps1.py",
        '"Ragon-mDNS-ed821dee"',
        '"Ragon-mDNS-e3dfdf36"',
        1,
    ),
    (
        "tests/test_install_ps1.py",
        '"Ragon-mDNS-557757bc"',
        '"Ragon-mDNS-f3f04289"',
        1,
    ),
    (
        "tests/test_ragon_prompt.py",
        "assert len(v1) == 4_232,",
        "assert len(v1) == 4_228,",
        1,
    ),
    (
        "tests/test_agents_md.py",
        "LIMIT_BYTES: Final[int] = 24_000",
        "LIMIT_BYTES: Final[int] = 24_100",
        1,
    ),
    (
        "tests/test_forge.py",
        'assert text.count("<service>") == 2 and',
        'assert text.count("<service>") == 4 and',
        1,
    ),
    (
        "tests/test_node.py",
        "    <txt-record>main=1</txt-record>\n  </service>\n</service-group>\n\"\"\"",
        "    <txt-record>main=1</txt-record>\n  </service>\n  <service>\n    <type>_mightling-node._tcp</type>\n"
        "    <port>8000</port>\n    <txt-record>proto=1</txt-record>\n"
        "    <txt-record>node=7c1e0c7a-58a4-4b0c-9a7e-0d7a54f6b001</txt-record>\n"
        "    <txt-record>version=1.3.0</txt-record>\n    <txt-record>web=3000</txt-record>\n"
        "    <txt-record>search=8888</txt-record>\n    <txt-record>state=ready</txt-record>\n"
        "    <txt-record>main=1</txt-record>\n  </service>\n</service-group>\n\"\"\"",
        1,
    ),
    (".github/apps/CODEOWNERS", "/scripts/rename_ragon.py ", "/scripts/rename_mightling.py                @dreamference/release\n/scripts/ragon_overlay/                       @dreamference/release\n/tests/test_rename_ragon.py                  @dreamference/release\n/scripts/rename_ragon.py ", 1),
    (
        "dreamference/audit/sync_egress_audit.py",
        '"❌ --prompt has no effect with --sync: it traces one sync agent cycle, with no ragon session to prompt."',
        '"❌ --prompt has no effect with --sync: it traces one sync agent cycle, "\n                "with no ragon session to prompt."',
        1,
    ),
    (  # the 1.6.0 notes are history and keep the old name; the test that renders them says so
        "tests/test_release_notes.py",
        "**Search your own documents.** Ragon now keeps",
        "**Search your own documents.** Mightling now keeps",
        1,
    ),
    ("ragon-rs/src/uninstall.rs", '"Ragon-mDNS-ed821dee"', '"Ragon-mDNS-e3dfdf36"', 1),
    ("ragon-rs/src/uninstall.rs", '"Ragon-mDNS-557757bc"', '"Ragon-mDNS-f3f04289"', 2),
    (
        "dreamference/forge/forge_storage.py",
        SWE_UNIT_OLD,
        SWE_UNIT_OLD + '                    "ling-swe-*",\n',
        1,
    ),
    (
        "dreamference/night_shift/night_shift_autofill.py",
        SWE_UNIT_OLD,
        SWE_UNIT_OLD + '                    "ling-swe-*",\n',
        1,
    ),
    (
        "dreamference/node/node_service_file.py",
        'SERVICE_TYPE: Final[str] = "_ragon-node._tcp"\n',
        'SERVICE_TYPE: Final[str] = "_ragon-node._tcp"\n'
        "# The type 1.5 to 1.8 clients browse for: published beside the new one for one release.\n"
        'LEGACY_SERVICE_TYPE: Final[str] = "_mightling-node._tcp"\n',
        1,
    ),
    (
        "dreamference/node/node_service_file.py",
        '        lines.append("  </service>")\n        if forge is not None:',
        '        lines.append("  </service>")\n'
        '        lines += ["  <service>", f"    <type>{LEGACY_SERVICE_TYPE}</type>", f"    <port>{int(port)}</port>"]\n'
        '        lines += [f"    <txt-record>{escape(record)}</txt-record>" for record in records]\n'
        '        lines.append("  </service>")\n        if forge is not None:',
        1,
    ),
    (
        "dreamference/node/node_browser.py",
        "from dreamference.node.node_service_file import SERVICE_TYPE\n",
        "from dreamference.node.node_service_file import LEGACY_SERVICE_TYPE, SERVICE_TYPE\n",
        1,
    ),
    (
        "dreamference/node/node_browser.py",
        "        return cls.parse(result.stdout)\n",
        "        entries = cls.parse(result.stdout)\n"
        '        seen = {entry.get("node") for entry in entries}\n'
        "        for entry in cls.browse_service(LEGACY_SERVICE_TYPE, timeout=timeout):\n"
        '            if entry.get("node") not in seen:\n'
        '                entry["legacy"] = "1"  # still runs Mightling: `ling update` there\n'
        "                entries.append(entry)\n"
        "        return entries\n",
        1,
    ),
    (
        "tests/conftest.py",
        'monkeypatch.delenv("RAGON_LEGACY_MIGRATION", raising=False)\n',
        'monkeypatch.delenv("RAGON_LEGACY_MIGRATION", raising=False)\n'
        '    monkeypatch.delenv("MIGHTLING_LEGACY_MIGRATION", raising=False)\n',
        1,
    ),
]


def apply_compat(root, dry):
    """Applies COMPAT_EDITS under `root`; aborts when an anchor is missing or ambiguous."""
    done = 0
    for path, old, new, count in COMPAT_EDITS:
        full = pathlib.Path(root) / path
        if not full.exists():
            continue  # a partial tree (a rehearsal fixture); the real tree has every file
        text = full.read_text(encoding="utf-8")
        if old != new and new in text:
            continue  # applied already: a second run, or an edit whose new text holds the old
        if text.count(old) != count:
            sys.exit(
                f"compat edit anchor not found {count}x in {path}: {old.strip()[:70]!r} ({text.count(old)}x)"
            )
        if not dry:
            full.write_text(text.replace(old, new), encoding="utf-8")
        done += 1
    return done


def ruff_python(root):
    """An interpreter that has ruff: RAGON_PYTHON, the tree's own virtualenv, else this one."""
    for python in (os.environ.get("RAGON_PYTHON", ""), os.path.join(root, ".venv/bin/python"), sys.executable):
        if python and pathlib.Path(python).exists():
            if not subprocess.run([python, "-m", "ruff", "--version"], capture_output=True).returncode:
                return python
    return None


def python_sources(root, paths):
    """The Python files of `paths` the ratchet tests watch (the package and the tests)."""
    return sorted(
        p for p in paths if p.endswith(".py") and p.startswith(("dreamference/", "tests/"))
        and (pathlib.Path(root) / p).exists()
    )


def format_clean(root, paths):
    """The files of `paths` that `ruff format` leaves alone now (run before the rename)."""
    python = ruff_python(root)
    files = python_sources(root, paths)
    if python is None or not files:
        return set()
    out = subprocess.run(
        [python, "-m", "ruff", "format", "--check", *files], cwd=root, capture_output=True, text=True
    ).stdout
    dirty = {line.split("Would reformat: ", 1)[1].strip() for line in out.splitlines() if "Would reformat: " in line}
    return set(files) - dirty


def tidy(root, clean):
    """Formats the renamed files that were format-clean before: a longer name can push a line over.

    The ratchet tests treat a file whose formatting got worse as a regression; a file that was not
    clean to begin with is left as it was, so no unrelated reformatting enters the rename.
    """
    python = ruff_python(root)
    files = python_sources(root, [new_path(p) for p in clean])
    if python is None:
        print("ruff not found: run `ruff format` on the renamed files by hand")
        return 0
    subprocess.run([python, "-m", "ruff", "format", *files], cwd=root, capture_output=True)
    move_noqa(root, python, files)
    return len(files)


def move_noqa(root, python, files):
    """Puts `# noqa: PLR0913` back on the `def` line when the formatter wrapped a signature.

    A `noqa` sits on the line the finding is reported at; wrapping the arguments moves the report
    to the `def` line and leaves the comment on an argument, where it is unused.
    """
    base = [python, "-m", "ruff", "check", "--select", "PLR0913,RUF100", "--output-format", "json"]
    out = subprocess.run([*base, *files], cwd=root, capture_output=True, text=True).stdout
    moved = {}
    for hit in json.loads(out or "[]"):
        moved.setdefault(hit["filename"], []).append(hit)
    for name, hits in moved.items():
        if not any(h["code"] == "RUF100" for h in hits):
            continue
        path = pathlib.Path(name)
        lines = path.read_text(encoding="utf-8").split("\n")
        for hit in hits:
            row = hit["location"]["row"] - 1
            if hit["code"] == "RUF100":
                lines[row] = re.sub(r"\s*# noqa: PLR0913.*$", "", lines[row])
        for hit in hits:
            row = hit["location"]["row"] - 1
            if hit["code"] == "PLR0913" and "noqa" not in lines[row]:
                lines[row] = lines[row] + "  # noqa: PLR0913"
        path.write_text("\n".join(lines), encoding="utf-8")


def build_manifest(root):
    """Writes scripts/ragon_overlay/manifest.json from the files beside it and the tracked tree (main)."""
    overlay = pathlib.Path(root) / OVERLAY_DIR
    by_final = {new_path(path): path for path in tracked(root) if not path.startswith(OVERLAY_DIR)}
    manifest = {}
    for file in sorted(overlay.rglob("*")):
        final = file.relative_to(overlay).as_posix()
        if not file.is_file() or final == "manifest.json":
            continue
        old = by_final.get(final)
        if old is None:
            sys.exit(f"overlay file {final} replaces nothing on this tree")
        digest = hashlib.sha256((pathlib.Path(root) / old).read_bytes()).hexdigest()
        manifest[final] = {"replaces": old, "sha256": digest}
    (overlay / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def apply_overlay(root, dry):
    """Copies scripts/ragon_overlay/ over the renamed tree, refusing when main changed a file it replaces."""
    manifest_path = os.path.join(root, OVERLAY_DIR, "manifest.json")
    if not pathlib.Path(manifest_path).exists():
        return []
    with pathlib.Path(manifest_path).open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    done = []
    for final, entry in sorted(manifest.items()):
        old = entry["replaces"]
        source = os.path.join(root, OVERLAY_DIR, final)
        present = os.path.join(root, old)
        if pathlib.Path(present).exists():
            digest = hashlib.sha256(pathlib.Path(present).open("rb").read()).hexdigest()
            if digest != entry["sha256"]:
                sys.exit(
                    f"overlay stale: main changed {old} (sha256 {digest[:12]}, overlay was built on "
                    f"{entry['sha256'][:12]}); rebuild scripts/ragon_overlay/{final} from it"
                )
        elif not pathlib.Path(os.path.join(root, final)).exists():
            sys.exit(f"overlay stale: neither {old} nor {final} exists")
        done.append(final)
        if dry:
            continue
        dest = os.path.join(root, final)
        pathlib.Path(os.path.dirname(dest)).mkdir(exist_ok=True, parents=True)
        with pathlib.Path(source).open("rb") as src, pathlib.Path(dest).open("wb") as out:
            out.write(src.read())
        if pathlib.Path(present).exists() and old != final:
            subprocess.run(["git", "-C", root, "rm", "-q", "-f", old], check=True)
        subprocess.run(["git", "-C", root, "add", final], check=True)
    return done


def run(root, files, dry, report):
    """Renames contents and paths under `root`; returns (changed files, renamed paths)."""
    changed, renames, notes = [], [], []
    for path in files:
        full = os.path.join(root, path)
        if pathlib.Path(full).is_symlink():
            pass
        else:
            text = read_text(root, path)
            if text is not None:
                new = (
                    transform_links(text) if content_excluded(path) else transform_text(path, text)
                )
                if new != text:
                    changed.append(path)
                    if not dry:
                        with pathlib.Path(full).open("w", encoding="utf-8") as handle:
                            handle.write(new)
                if not content_excluded(path):
                    for line in text.splitlines():
                        if (
                            is_history(path, line)
                            and LEFTOVER.search(masked(line))
                            and DATE.search(line)
                            and not LEGACY_LINE.search(line)
                        ):
                            notes.append(f"history by date, left: {path}: {line.strip()[:120]}")
        if os.path.basename(path) == ".lingignore":
            notes.append(
                f"{path}: renamed to .ragonignore; a user's own .lingignore is not migrated"
            )
        target = new_path(path)
        if target != path:
            renames.append((path, target))
    seen = {}
    for old, target in renames:
        if target in seen or (
            os.path.lexists(os.path.join(root, target)) and target not in {o for o, _ in renames}
        ):
            sys.exit(f"collision: {old} and {seen.get(target, target)} both become {target}")
        seen[target] = old
    if not dry:
        subprocess.run(
            ["git", "-C", root, "add", "-A"], check=True
        )  # git mv of a submodule wants .gitmodules staged
        for old, target in renames:
            pathlib.Path(os.path.dirname(os.path.join(root, target)) or root).mkdir(
                exist_ok=True, parents=True
            )
            subprocess.run(["git", "-C", root, "mv", old, target], check=True)
        for old, _ in renames:
            folder = os.path.dirname(os.path.join(root, old))
            while (
                folder != os.path.abspath(root)
                and pathlib.Path(folder).is_dir()
                and not os.listdir(folder)
            ):
                pathlib.Path(folder).rmdir()
                folder = os.path.dirname(folder)
    if report:
        with pathlib.Path(report).open("w", encoding="utf-8") as handle:
            handle.write(f"contents changed: {len(changed)}\nrenamed: {len(renames)}\n")
            handle.writelines(f"rename {a} -> {b}\n" for a, b in renames)
            handle.writelines(n + "\n" for n in notes)
    return changed, renames


def verify(root):
    """The checks of §9.3, in order; stops at the first failure."""
    repo_python = os.path.join(root, ".venv/bin/python")
    python = repo_python if pathlib.Path(repo_python).exists() else sys.executable
    steps = [
        ("leftover check", None),
        ("pytest", [python, "-m", "pytest", "tests/", "-q"]),
        ("ruff format", [python, "-m", "ruff", "format", "--check", "dreamference", "tests"]),
        ("ruff check", [python, "-m", "ruff", "check", "dreamference", "tests"]),
    ]
    for crate in ("ragon-web-rs", "ragon-code-rs", "ragon-docs-rs"):
        steps.append(
            (
                f"cargo test {crate}",
                ["cargo", "test", "--locked", "--manifest-path", f"{crate}/Cargo.toml"],
            )
        )
    steps.append(("desktop npm test", ["npm", "--prefix", "desktop/electron", "test"]))
    steps.append(("codex build", [python, "-m", "dreamference.cli", "codex", "build"]))
    steps.append(("egress audit", [python, "-m", "dreamference.cli", "audit", "egress"]))
    for name, command in steps:
        print(f"== {name}")
        if command is None:
            hits = leftovers(root)
            print("\n".join(hits[:50]))
            if hits:
                return 1
            continue
        if subprocess.run(command, cwd=root).returncode != 0:
            print(f"FAILED: {name}")
            return 1
    print(OWNER_STEPS)
    return 0


OWNER_STEPS = """Owner-only steps at release time (spec §9.4): rename both GitHub repositories
(mightling -> ragon, ling-engine -> ragon-engine); descriptions, topics, org README, release titles;
publish ragon-serve to GHCR; the DNS record and redirect for the new site host, Pages custom domain;
tag 1.9.0 with three asset sets (ragon-*, ling-*, puffin-*) under SHA256SUMS; one sudo line per node;
reinstall the Claude plugin; re-run install.sh on nodes; no night on install day."""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--manifest", action="store_true", help="rebuild the overlay's manifest from this tree"
    )
    parser.add_argument("--report")
    parser.add_argument("--root", default=".")
    parser.add_argument("--repo", choices=("main", "engine"), default="main")
    parser.add_argument("--paths", nargs="+")
    args = parser.parse_args(argv)
    root = os.path.abspath(args.root)
    if args.manifest:
        print(json.dumps(build_manifest(root), indent=2))
        return 0
    if args.verify:
        return verify(root)
    if args.check:
        hits = leftovers(root, args.paths)
        print("\n".join(hits))
        print(f"{len(hits)} leftover(s)")
        return 1 if hits else 0
    files = args.paths if args.paths else tracked(root)
    clean = format_clean(root, files) if args.repo == "main" and not args.paths and not args.dry else set()
    changed, renames = run(root, files, args.dry, args.report)
    for path in changed:
        print(f"  {'would change' if args.dry else 'changed'} {path}")
    for old, target in renames:
        print(f"  {old} -> {target}")
    overlays = [] if args.repo == "engine" else apply_overlay(root, args.dry)
    if args.repo == "main" and not args.paths and not args.dry:
        print(f"compat edits: {apply_compat(root, args.dry)}")
        print(f"ruff-formatted files: {tidy(root, clean)}")
    print(
        f"contents changed: {len(changed)} files; renamed: {len(renames)} paths; overlays: {len(overlays)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
