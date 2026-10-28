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
    # the SCIP tables, until ling-code migrates them (scip_store.rs)
    "puffin_names",
    "puffin_relationships",
    "puffin_meta",
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

SWE_UNIT_OLD = '                    "ragon-swe-*",\n'
COMPAT_EDITS = [
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
        'assert text.count("<service>") == 3 and',
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
        if new in text and old != new and text.count(new) >= 1 and text.count(old) == 0:
            continue
        if text.count(old) != count:
            sys.exit(
                f"compat edit anchor not found {count}x in {path}: {old.strip()[:70]!r} ({text.count(old)}x)"
            )
        if not dry:
            full.write_text(text.replace(old, new), encoding="utf-8")
        done += 1
    return done


def tidy(root, paths):
    """Formats the renamed Python files with ruff (a longer name can push a line over the limit).

    The ratchet tests treat a file whose formatting got worse as a regression. Uses the ruff of the
    tree's own virtualenv, else of the interpreter running this script; says so and goes on when
    neither has one.
    """
    files = sorted({new_path(p) for p in paths if p.endswith(".py")})
    files = [f for f in files if f.startswith(("dreamference/", "tests/")) and (pathlib.Path(root) / f).exists()]
    for python in (os.path.join(root, ".venv/bin/python"), sys.executable):
        if not pathlib.Path(python).exists():
            continue
        if subprocess.run([python, "-m", "ruff", "--version"], capture_output=True).returncode:
            continue
        for command in (["format"], ["check", "--fix", "--quiet"]):
            subprocess.run([python, "-m", "ruff", *command, *files], cwd=root, capture_output=True)
        return len(files)
    print("ruff not found: run `ruff format` and `ruff check --fix` on the renamed files by hand")
    return 0


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
    changed, renames = run(root, files, args.dry, args.report)
    for path in changed:
        print(f"  {'would change' if args.dry else 'changed'} {path}")
    for old, target in renames:
        print(f"  {old} -> {target}")
    overlays = [] if args.repo == "engine" else apply_overlay(root, args.dry)
    if args.repo == "main" and not args.paths and not args.dry:
        print(f"compat edits: {apply_compat(root, args.dry)}")
        print(f"ruff-formatted files: {tidy(root, set(files))}")
    print(
        f"contents changed: {len(changed)} files; renamed: {len(renames)} paths; overlays: {len(overlays)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
