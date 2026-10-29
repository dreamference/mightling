"""The forge's server-side anonymity check (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §3.2).

A `pre-receive.d` hook in each repository refuses a push whose new commits bring an identifier:
an entry of the owner's denylist (kept on the node, `~/.config/dreamference/anonymity-denylist.txt`,
never in a repository) or, for a repository checked in full, what `tests/test_anonymity.py`
finds without a list: a real-looking LAN or overlay address and a home path naming anyone but a
placeholder. It scans what the push adds (the added lines of every new commit, the paths they
touch, and the commits' authors, committers and messages), so a leak added in one commit and
removed in the next is refused too, and the upstream history a fork carries is not re-read.

The image has no Python, so the hook is bash and `git grep -P` over the patterns this module
writes; the GitHub mirror re-checks every ref it sends with the same patterns in Python, as the
second layer. The same hook also refuses every push while the forge is frozen for a `move`, and
a push bringing a root commit the repository does not have (a branch of an old, rewritten
history).
"""

import ipaddress
import os
import re
import subprocess
from pathlib import Path
from typing import Final

# The checks a repository can have: `full` (the denylist and the generic checks), `denylist` (a
# fork whose upstream is full of other people's addresses and paths), `off` (the product default:
# the anonymity rules are this project's).
SCOPE_FULL: Final[str] = "full"
SCOPE_DENYLIST: Final[str] = "denylist"
SCOPE_OFF: Final[str] = "off"
SCOPES: Final[tuple] = (SCOPE_FULL, SCOPE_DENYLIST, SCOPE_OFF)
# Mirrors tests/test_anonymity.py; tests/test_forge.py compares the two.
EXAMPLE_LAN_SUBNETS: Final[tuple] = ("192.168.1.0/24", "192.168.50.0/24", "192.168.100.0/24")
EXAMPLE_OVERLAY_ADDRESSES: Final[tuple] = (
    "100.64.0.0",
    "100.64.0.1",
    "100.100.100.100",
    "100.101.102.103",
)
PLACEHOLDER_USERS: Final[tuple] = (
    "user",
    "u",
    "x",
    "j",
    "o",
    "a",
    "me",
    "you",
    "jane",
    "ubuntu",
    "runner",
    "owner",
    "alice",
    "bob",
    "sessions",
    "shell_snapshots",
    "shared",
    "...",
)
DENYLIST: Final[str] = "~/.config/dreamference/anonymity-denylist.txt"
# Inside the container: the data folder's `mightling/` holds the patterns and the freeze flag.
CONTAINER_DIR: Final[str] = "/var/lib/gitea/mightling"
HOOK_NAME: Final[str] = "mightling-anonymity"
TRIGGER_HOOK_NAME: Final[str] = "mightling-mirror-trigger"
OCTET: Final[str] = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
END: Final[str] = r"(?![\d.]*\d)"
ZERO: Final[str] = "0" * 40
# The `+++` line of a file a commit deletes.
DEV_NULL: Final[str] = "/dev/null"


class ForgeAnonymity:
    """Writes the patterns and the hook, and checks text the same way."""

    @classmethod
    def denylist_patterns(cls, text: str) -> list[str]:
        """Turn the denylist's entries into patterns.

        Args:
            text: The denylist's contents.

        Returns:
            list[str]: One case-insensitive whole-word pattern per entry, as
            `tests/test_anonymity.py` compiles them (an entry that does not compile is literal).
        """
        found = []
        for line in text.splitlines():
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            try:
                re.compile(entry)
                body = entry
            except re.error:
                body = re.escape(entry)
            found.append(rf"(?i)(?<![A-Za-z0-9])(?:{body})(?![A-Za-z0-9])")
        return found

    @classmethod
    def generic_patterns(cls) -> list[str]:
        """Return the checks that need no list, as patterns both PCRE and Python read.

        Returns:
            list[str]: The checks that need no list, as patterns both PCRE and Python read.
        """
        lan_examples = "|".join(re.escape(subnet.split(".")[2]) for subnet in EXAMPLE_LAN_SUBNETS)
        overlay_examples = "|".join(
            re.escape(address) + END for address in EXAMPLE_OVERLAY_ADDRESSES
        )
        placeholders = "|".join(re.escape(user) for user in PLACEHOLDER_USERS)
        return [
            rf"(?<![\d.])192\.168\.(?!(?:{lan_examples})\.){OCTET}\.{OCTET}{END}",
            rf"(?<![\d.])(?!{overlay_examples})100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.{OCTET}\.{OCTET}{END}",
            r"(?:(?:(?<![\w.~/-])|(?<=file://))/(?:home|Users)/|[A-Za-z]:\\{1,2}Users\\{1,2})"
            rf"(?!(?i:{placeholders})(?![A-Za-z0-9_.-]))[A-Za-z0-9_.-]+",
        ]

    @classmethod
    def patterns(cls, scope: str, denylist_text: str | None) -> list[str]:
        """Return the patterns a scope checks.

        Args:
            scope: `full`, `denylist` or `off`.
            denylist_text: The denylist's contents, or None when the node has none.

        Returns:
            list[str]: The patterns for that scope.
        """
        if scope == SCOPE_OFF:
            return []
        found = cls.denylist_patterns(denylist_text or "")
        return found + (cls.generic_patterns() if scope == SCOPE_FULL else [])

    @classmethod
    def read_denylist(cls) -> str | None:
        """Read the node's denylist.

        Returns:
            str | None: The node's denylist, or None when it has none.
        """
        try:
            return Path(DENYLIST).expanduser().read_text(encoding="utf-8")
        except OSError:
            return None

    @classmethod
    def pattern_file(cls, data_dir: Path, scope: str) -> Path:
        """Return where that scope's patterns are written on the host.

        Args:
            data_dir: The forge's data folder.
            scope: The scope.

        Returns:
            Path: Where that scope's patterns are written on the host.
        """
        return data_dir / "mightling" / f"anonymity-{scope}.pcre"

    @classmethod
    def write_patterns(cls, data_dir: Path) -> None:
        """Write each scope's pattern file (0600: the container runs as the node's own user).

        A scope whose list is empty gets a pattern that never matches, so the hook's grep has
        something to read.

        Args:
            data_dir: The forge's data folder.
        """
        denylist = cls.read_denylist()
        for scope in (SCOPE_FULL, SCOPE_DENYLIST):
            path = cls.pattern_file(data_dir, scope)
            path.parent.mkdir(parents=True, exist_ok=True)
            lines = cls.patterns(scope, denylist) or [r"(?!)"]
            staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w") as handle:
                handle.write("\n".join(lines) + "\n")
            Path(staging).replace(path)

    @classmethod
    def findings(cls, text: str, patterns: list[str]) -> list[str]:
        """Check text against the patterns, line by line.

        Args:
            text: The text to check, line by line.
            patterns: The patterns (`patterns`).

        Returns:
            list[str]: `<line number>: pattern #<n>` for each hit; the identifier itself is never
            repeated, so a report can go anywhere.
        """
        compiled = [re.compile(pattern) for pattern in patterns]
        hits = []
        for number, line in enumerate(text.splitlines(), 1):
            hits += [
                f"{number}: pattern #{index}"
                for index, pattern in enumerate(compiled, 1)
                if pattern.search(line)
            ]
        return hits

    @classmethod
    def awk_program(cls) -> str:
        """Return the awk program that picks the added lines out of a patch.

        Returns:
            str: The awk that turns `git log -p` into one line per added line, prefixed with its
            file, and one line per touched path; files named in `exempt` are left out.
        """
        return (
            'BEGIN { n = split(exempt, names, " "); for (i = 1; i <= n; i++) skip_name[names[i]] = 1 }\n'
            '/^diff --git / { file = ""; skipping = 0; next }\n'
            '/^\\+\\+\\+ / { path = substr($0, 5); if (path == "/dev/null") { file = ""; next }\n'
            '  sub(/^b\\//, "", path); file = path; skipping = (file in skip_name); if (!skipping) print file; next }\n'
            '/^\\+/ { if (!skipping && file != "") print file ": " substr($0, 2) }\n'
        )

    @classmethod
    def hook_script(
        cls, scope: str, exempt: list[str], check_roots: bool = True, directory: str = CONTAINER_DIR
    ) -> str:
        """Return the `pre-receive.d` hook that checks a push.

        Args:
            scope: `full`, `denylist` or `off` (`off` keeps the freeze).
            exempt: Paths whose content is not scanned (`tests/test_anonymity.py`, whose fixtures
                are the identifiers it looks for).
            check_roots: Refuse a push bringing a root commit the repository does not have.
            directory: Where the patterns and the freeze flag are, as the hook sees it.

        Returns:
            str: The `pre-receive.d` script.
        """
        safe = [path for path in exempt if re.fullmatch(r"[A-Za-z0-9_./-]+", path)]
        lines = [
            "#!/usr/bin/env bash",
            "# Written by `ling-admin forge`; rewritten at every start, edits are lost.",
            "# The forge's anonymity check (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §3.2).",
            "set -u",
            f"dir={directory}",
            'if [ -e "$dir/frozen" ]; then',
            '  echo "The forge is frozen while its role moves to another node: push again in a few minutes."',
            "  exit 1",
            "fi",
        ]
        if scope == SCOPE_OFF and not check_roots:
            return "\n".join([*lines, "exit 0", ""])
        lines += [
            f"zero={ZERO}",
            "work=$(mktemp -d) || exit 1",
            "trap 'rm -rf \"$work\"' EXIT",
            ': > "$work/added"; : > "$work/meta"',
            "has_refs=$(git for-each-ref --count=1 --format=x refs/heads refs/tags)",
            "while read -r old new ref; do",
            '  [ "$new" = "$zero" ] && continue',
        ]
        if check_roots:
            lines += [
                '  if [ -n "$has_refs" ] && '
                '[ -n "$(git rev-list --max-parents=0 "$new" --not --all | head -n 1)" ]; then',
                '    echo "Refused by the forge: $ref brings a root commit this repository does not have,"',
                '    echo "an unrelated or pre-rewrite history. Rebase the work onto the forge\'s branches."',
                "    exit 1",
                "  fi",
            ]
        if scope != SCOPE_OFF:
            lines += [
                '  if [ "$(git cat-file -t "$new")" = tag ]; then git cat-file -p "$new" >> "$work/meta"; fi',
                '  git log --format="commit %H%n%an <%ae>%n%cn <%ce>%n%B" "$new" --not --all >> "$work/meta" || exit 1',
                '  git log -p --no-color --no-ext-diff --no-renames --format= "$new" --not --all \\',
                f"    | awk -v exempt={' '.join(safe)!r} '{cls.awk_program()}' >> \"$work/added\" || exit 1",
            ]
        lines.append("done")
        if scope != SCOPE_OFF:
            lines += [
                'cd "$work" || exit 1',
                f'git grep --no-index -q -P -f "$dir/anonymity-{scope}.pcre" -- added meta',
                "status=$?",
                'if [ "$status" -eq 0 ]; then',
                '  echo "Refused by the forge\'s anonymity check: the push adds an identifier (a denylist entry,"',
                '  echo "a real-looking address or a home path). Replace it with a placeholder and push again:"',
                f'  git grep --no-index -n -P -f "$dir/anonymity-{scope}.pcre" -- added meta'
                " | cut -c1-160 | head -n 20",
                "  exit 1",
                "fi",
                'if [ "$status" -ne 1 ]; then '
                'echo "The forge\'s anonymity check could not run; push refused."; exit 1; fi',
            ]
        return "\n".join([*lines, "exit 0", ""])

    @classmethod
    def trigger_script(cls) -> str:
        """Return the `post-receive.d` hook that tells the host a push arrived.

        Returns:
            str: The `post-receive.d` script that tells the host a push arrived, so the GitHub
            mirror runs within seconds (a systemd path unit watches the file).
        """
        return "\n".join(
            [
                "#!/usr/bin/env bash",
                "# Written by `ling-admin forge`.",
                f"date +%s > {CONTAINER_DIR}/pushed",
                "exit 0",
                "",
            ]
        )

    @classmethod
    def install(
        cls, repository: Path, scope: str, exempt: list[str], check_roots: bool, trigger: bool
    ) -> None:
        """Put the hooks into one bare repository on the host side of the data folder.

        Args:
            repository: The bare repository's folder.
            scope: `full`, `denylist` or `off`.
            exempt: Paths whose content is not scanned.
            check_roots: Refuse foreign root commits.
            trigger: Also install the GitHub mirror's trigger.
        """
        scripts = {
            repository / "hooks" / "pre-receive.d" / HOOK_NAME: cls.hook_script(
                scope, exempt, check_roots
            )
        }
        trigger_path = repository / "hooks" / "post-receive.d" / TRIGGER_HOOK_NAME
        if trigger:
            scripts[trigger_path] = cls.trigger_script()
        elif trigger_path.exists():
            trigger_path.unlink()
        for path, text in scripts.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            Path(path).chmod(0o755)

    @classmethod
    def added_text(
        cls, repository: Path, tips: list[str], known: list[str], exempt: list[str]
    ) -> str:
        """Compute on the host what the hook would scan for these tips.

        This is the GitHub mirror's second layer.

        Args:
            repository: A bare repository holding the tips.
            tips: The commits about to be sent.
            known: Commits the destination already has (their history is not re-read).
            exempt: Paths whose content is not scanned.

        Returns:
            str: The commits' metadata and their added lines, one per line.

        Raises:
            RuntimeError: git failed; the caller sends nothing.
        """
        if not tips:
            return ""
        revisions = [*tips, "--not", *known]
        meta = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "log",
                "--stdin",
                "--format=commit %H%n%an <%ae>%n%cn <%ce>%n%B",
            ],
            input="\n".join(revisions) + "\n",
            capture_output=True,
            text=True,
            check=False,
            # A commit naming an encoding git cannot convert is printed raw.
            errors="replace",
        )
        diff = subprocess.run(
            [
                "git",
                "-C",
                str(repository),
                "log",
                "--stdin",
                "-p",
                "--no-color",
                "--no-ext-diff",
                "--no-renames",
                "--format=",
            ],
            input="\n".join(revisions) + "\n",
            capture_output=True,
            text=True,
            check=False,
            errors="replace",
        )
        if meta.returncode != 0 or diff.returncode != 0:
            raise RuntimeError((meta.stderr or diff.stderr).strip() or "git log failed")
        return meta.stdout + cls.added_lines(diff.stdout, exempt)

    @classmethod
    def added_lines(cls, patch: str, exempt: list[str]) -> str:
        """Pick the touched paths and added lines out of a patch, as the awk program does.

        Args:
            patch: `git log -p` output.
            exempt: Paths whose content is not scanned.

        Returns:
            str: The touched paths and the added lines, each prefixed with its file.
        """
        out: list[str] = []
        current, skipping = "", False
        for line in patch.splitlines():
            if line.startswith("diff --git "):
                current, skipping = "", False
            elif line.startswith("+++ "):
                path = line[4:]
                if path == DEV_NULL:
                    current = ""
                    continue
                current = path[2:] if path.startswith("b/") else path
                skipping = current in exempt
                if not skipping:
                    out.append(current)
            elif line.startswith("+") and current and not skipping:
                out.append(f"{current}: {line[1:]}")
        return "\n".join(out) + ("\n" if out else "")

    @classmethod
    def is_example_lan(cls, address: str) -> bool:
        """Tell whether an address lies in one of the example subnets.

        Args:
            address: An IPv4 address.

        Returns:
            bool: Whether it lies in one of the example subnets (a helper for the tests' parity
            check with tests/test_anonymity.py).
        """
        ip = ipaddress.IPv4Address(address)
        return any(ip in ipaddress.IPv4Network(subnet) for subnet in EXAMPLE_LAN_SUBNETS)
