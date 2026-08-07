#!/usr/bin/env python3
"""
Squash all commits made today into a single commit.

Usage:
    python squash_todays_commits.py
"""

import subprocess
import sys


def run_git(cmd: list[str]) -> str:
    result = subprocess.run(
        ["git"] + cmd,
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()


def main():
    try:
        # Use Git's own "today" handling to avoid timezone boundary issues
        print("Looking for commits since today (using Git date parsing)...")
        log_output = run_git([
            "log",
            "--since=midnight",
            "--pretty=format:%H"
        ])

        if not log_output:
            print("No commits found for today.")
            return

        hashes = log_output.splitlines()
        print(f"Found {len(hashes)} commit(s) from today.")

        if len(hashes) < 2:
            print("Only one commit today — nothing to squash.")
            return

        # Oldest commit is the last in the list
        oldest_hash = hashes[-1]

        # Get parent of the oldest commit (fails for root commit)
        try:
            parent = run_git(["rev-parse", "--verify", f"{oldest_hash}^"])
            print(f"Oldest commit today: {oldest_hash[:8]}")
            print(f"Resetting to parent: {parent[:8]}")
            run_git(["reset", "--soft", parent])
        except subprocess.CalledProcessError:
            print("Oldest commit today is the root commit of the repository.")
            print(f"Resetting to root (keeping root commit, squashing all {len(hashes)-1} later commits into one).")
            run_git(["reset", "--soft", oldest_hash])

        # Commit everything as one commit
        # You can customize the message or make it interactive
        commit_msg = "Squashed work from today into single commit"
        run_git(["commit", "-m", commit_msg])

        print("✅ Successfully squashed today's commits into one commit.")

    except subprocess.CalledProcessError as e:
        print(f"Git error: {e.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
