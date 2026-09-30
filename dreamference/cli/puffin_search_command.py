"""
`puffin-search`: web search through the local SearXNG instance, as a command of its own.

It was `puffin-admin search` until 2026-09-30. Searching is something the `puffin` agent does in
the middle of a task, while `puffin-admin` administers the machine -- servers, models, the web UI
-- so the agent's most frequent command no longer lives among the administrative ones. It is a
shell command rather than an MCP tool for the reason `puffin-rs/src/lib.rs` gives next to the
prompt that names it: Codex exposes MCP tools only inside Code Mode's JavaScript runtime, and the
local model does not reliably call them there, while it runs shell commands correctly.
"""

import argparse
import json
from typing import List, Optional

PROGRAM_NAME = "puffin-search"


class PuffinSearchCommand:
    """
    Parses `puffin-search` arguments and prints SearXNG results as text or JSON.
    """

    @classmethod
    def build_parser(cls) -> argparse.ArgumentParser:
        """
        Builds the argument parser for `puffin-search`.

        Returns:
            argparse.ArgumentParser: Parser taking the query words, `-n` and `--json`.
        """
        parser = argparse.ArgumentParser(
            prog=PROGRAM_NAME,
            description="Search the web through the SearXNG instance on this machine.",
        )
        parser.add_argument("query", nargs="+", help="Search terms")
        parser.add_argument("-n", "--max-results", type=int, default=5, help="Results to return")
        parser.add_argument("--json", action="store_true", help="Emit raw JSON")
        return parser

    @classmethod
    def main(cls, argv: Optional[List[str]] = None) -> int:
        """
        Runs one search and prints the results.

        Args:
            argv (Optional[List[str]]): Arguments without the program name; `sys.argv[1:]` if None.

        Returns:
            int: 0 when the search answered, 1 when SearXNG reported an error.
        """
        from dreamference.mcp_server.web_tools import WebTools

        args = cls.build_parser().parse_args(argv)
        payload = WebTools.search(" ".join(args.query), max_results=args.max_results)
        if payload.get("error"):
            print(f"❌ {payload['error']}")
            if payload.get("hint"):
                print(f"💡 {payload['hint']}")
            return 1
        if args.json:
            print(json.dumps(payload, indent=2))
            return 0
        for answer in payload.get("answers", []):
            print(f"ANSWER: {answer}\n")
        for i, result in enumerate(payload.get("results", []), 1):
            print(f"{i}. {result['title']}")
            print(f"   {result['url']}")
            if result.get("snippet"):
                print(f"   {result['snippet'][:200]}")
        return 0
