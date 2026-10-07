"""Mechanical Puffin -> Mightling rename (specs/DREAMFERENCE_RENAME_MIGHTLING.md §2).

The same `transform` is applied to file contents and to path names, so a reference to a file and the
file's new name always agree. The tree was renamed with it on 2026-10-07. It is kept for one job:
branches started before the rename still say Puffin, and after merging one, running this over the
files the merge brought in renames them the same way:

    .venv/bin/python scripts/rename_mightling.py --paths $(git diff --name-only HEAD~1 HEAD)

With no `--paths` it walks every tracked file. `--dry` changes nothing and prints what it would do.
Names that must keep the old spelling (the transition's assets, the migration's legacy constants,
internal names persisted on users' machines) are protected or their files excluded, so a second run
over an already renamed file changes nothing that matters; check `git diff` all the same.
"""
import os
import re
import subprocess
import sys

DRY = "--dry" in sys.argv

# Files left exactly as they are: history, the rename's own record, and code whose job is to know
# the old names (the migration, the transition assets).
EXCLUDE_FILES = {
    "specs/DREAMFERENCE_RELEASE_1.4.0.md",
    "specs/DREAMFERENCE_RELEASE_1.4.1.md",
    "specs/DREAMFERENCE_RELEASE_1.5.0.md",
    "specs/DREAMFERENCE_RENAME_MIGHTLING.md",
    "scripts/rename_mightling.py",
    "mling-rs/src/rename.rs",
    "dreamference/cli/legacy_name_migration.py",
    "tests/test_legacy_name_migration.py",
    "install.sh",
    ".github/workflows/release.yml",
}
EXCLUDE_PREFIXES = ("codex/", "desktop/ui/src/protocol/", "tests/fixtures/code_index/")

# Internal names that are persisted and never shown as a product name (§2, "Kept on purpose"), and
# old names that code keeps on purpose.
PROTECT = [
    "dreamference/puffin-codex",
    "dreamference/puffin-web\"",
    "dreamference/puffin-web'",
    "puffin-code-build",
    "puffin-image",          # the web chat's /puffin-images/ route and its nginx markers
    "puffin-bwrap",          # the AppArmor profile root installed
    "puffin_names",          # the SCIP stores' own tables
    "puffin_relationships",
    "puffin_meta",
    "puffin\\\\_%",
    ".puffin-origin.toml",
    "puffin-ledger",         # a hook path recorded in a test
    "LEGACY_ASSISTANT_NAME: Final[str] = \"Puffin\"",
    "\"puffin-desktop\", \"puffin-ui\", \"puffin-app\"",
    "`puffin update` there once",   # the 1.4.x updater, named in messages about the transition
    "`puffin` one last time",
    "`puffin_*`",
    "puffin-desktop, then puffin-ui, then puffin-app",
    "a_puffin_install",
    "\"name\": \"Puffin\"",
    "puffin_version",        # SWE-bench manifests
    "puffin_code_calls",     # SWE-bench stats
    "puffin_code_users",
]
SWE_ONLY = {"puffin_version", "puffin_code_calls", "puffin_code_users"}

CRATES = r"(launcher|airgapped|masking|apps|skills|tools|node[-_]locator|cave|web|code|app|search|fetch|rs|desktop[-_]bridge)"

RULES = [
    (r"DREAMFERENCE_PUFFIN_", "DREAMFERENCE_MIGHTLING_"),
    (r"PUFFIN_", "MIGHTLING_"),
    (r"_PUFFIN\b", "_MIGHTLING"),
    (r"\bPUFFIN\b", "MIGHTLING"),
    (r"puffin-admin", "mling-admin"),
    (r"puffin-node-locator", "mling-node-locator"),
    (r"puffin-node", "mightling-node"),
    # crates, commands and folders: the short `mling` prefix
    (r"puffin-" + CRATES + r"(?![a-z])", r"mling-\1"),
    (r"puffin_" + CRATES + r"(?=::|\s+as\b|\b(?=\s*[;,}\]]))", r"mling_\1"),
    (r'name = "puffin_', 'name = "mling_'),
    (r"\bpuffin_code\b", "mling_code"),  # the MCP server's name
    # the rest of the hyphenated names: systemd units, DOM ids, container and file prefixes
    (r"puffin-", "mightling-"),
    # homes and the install dir
    (r"\.puffin\b", ".mightling"),
    (r"dreamference/puffin\b", "dreamference/mightling"),
    # configuration keys and identifiers
    (r"puffin_", "mightling_"),
    (r"_puffin\b", "_mightling"),
    (r"Puffin", "Mightling"),
    # what is left is the command
    (r"puffin", "mling"),
]
COMPILED = [(re.compile(a), b) for a, b in RULES]

# A line that talks about the rename itself, or names an old name on purpose, is left as it is.
LEGACY_LINE = re.compile(
    r"renamed|rename\b|was called|is now Mightling|became Mightling|before the product|Puffin 1\.4|"
    r"puffin bird|\"puffin\"\]|it was `puffin|old name|earlier name|LEGACY|legacy|"
    r"Puffin install|installed Puffin|still running Puffin|sold under the name Puffin")


def transform_text(text, swe=False):
    """Transforms a file's contents line by line, leaving the lines about the rename alone."""
    return "".join(line if LEGACY_LINE.search(line) else transform(line, swe)
                   for line in text.splitlines(keepends=True))


def transform(text, swe=False):
    saved = {}
    for i, word in enumerate(PROTECT):
        if word in SWE_ONLY and not swe:
            continue
        token = f"\x00P{i}\x00"
        if word in text:
            saved[token] = word
            text = text.replace(word, token)
    for pattern, repl in COMPILED:
        text = pattern.sub(repl, text)
    for token, word in saved.items():
        text = text.replace(token, word)
    return text


def tracked():
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def main():
    if "--paths" in sys.argv:
        files = [p for p in sys.argv[sys.argv.index("--paths") + 1:] if not p.startswith("--")]
    else:
        files = tracked()
    changed = 0
    renames = []
    for path in files:
        if path in EXCLUDE_FILES or path.startswith(EXCLUDE_PREFIXES):
            continue
        if not os.path.isfile(path) or os.path.islink(path):
            continue
        swe = "swe_bench" in path
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            text = None
        if text is not None:
            new = transform_text(text, swe)
            if new != text:
                changed += 1
                if DRY:
                    print(f"  would change {path}")
                else:
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(new)
        new_path = transform(path, swe)
        if new_path != path:
            renames.append((path, new_path))
    for old, new in renames:
        if DRY:
            continue
        os.makedirs(os.path.dirname(new) or ".", exist_ok=True)
        subprocess.run(["git", "mv", old, new], check=True)
    if not DRY:
        for old, _ in renames:
            folder = os.path.dirname(old)
            while folder and os.path.isdir(folder) and not os.listdir(folder):
                os.rmdir(folder)
                folder = os.path.dirname(folder)
    print(f"contents changed: {changed} files; renamed: {len(renames)} paths")
    for old, new in renames:
        print(f"  {old} -> {new}")


if __name__ == "__main__":
    main()
