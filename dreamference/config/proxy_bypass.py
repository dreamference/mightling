"""
Keeps local traffic away from any proxy a shell left set (specs/DREAMFERENCE_MIGHTLING_EGRESS.md §11).

An `HTTP_PROXY`, `HTTPS_PROXY` or `ALL_PROXY` in the environment also applies to the model server
and the loopback services: `requests` and `urllib` would send `ling-admin`'s health checks and
canaries through it, and a child (`ling exec` in Night Shift, a SWE-bench container) would send its
prompts there. `NO_PROXY` is the variable every client involved reads. The two spellings are read in
opposite orders (`requests` and `urllib` prefer `no_proxy`, upstream Codex and reqwest `NO_PROXY`), so
both are set, each to the union of what either held and the local hosts. `ling` does the same for
itself (ling-rs/src/proxy.rs).
"""

from typing import Final, List, MutableMapping, Optional, Tuple
from urllib.parse import urlsplit

# The names this machine answers to, always exempt.
LOOPBACK_HOSTS: Final[Tuple[str, ...]] = ("localhost", "127.0.0.1", "::1")
NO_PROXY_KEYS: Final[Tuple[str, str]] = ("NO_PROXY", "no_proxy")


class ProxyBypass:
    """
    Builds and applies the `NO_PROXY` value for loopback and the model server.
    """

    @classmethod
    def host_name(cls, url: Optional[str]) -> Optional[str]:
        """
        The host of a URL such as `http://192.168.1.20:8000/v1`, without scheme, port or brackets.

        Args:
            url (Optional[str]): The URL; a bare `host:port` is accepted.

        Returns:
            Optional[str]: The host, or None when there is none.
        """
        if not url or not url.strip():
            return None
        text = url.strip()
        if "://" not in text:
            text = f"http://{text}"
        try:
            host = urlsplit(text).hostname
        except ValueError:
            return None
        return host or None

    @classmethod
    def merged(cls, upper: Optional[str], lower: Optional[str], urls: Tuple[Optional[str], ...] = ()) -> str:
        """
        The `NO_PROXY` value: the existing entries of both spellings in order without repeats, then
        loopback and the host of each URL, each once.

        Args:
            upper (Optional[str]): `NO_PROXY` as it is.
            lower (Optional[str]): `no_proxy` as it is.
            urls (Tuple[Optional[str], ...]): The model server's URL and any other local endpoints.

        Returns:
            str: The comma-separated value.
        """
        entries: List[str] = []
        seen = set()
        existing = [entry for value in (upper, lower) if value for entry in value.split(",")]
        added = list(LOOPBACK_HOSTS) + [host for host in map(cls.host_name, urls) if host]
        for entry in existing + added:
            entry = entry.strip()
            if entry and entry.lower() not in seen:
                seen.add(entry.lower())
                entries.append(entry)
        return ",".join(entries)

    @classmethod
    def apply(cls, env: MutableMapping[str, str], *urls: Optional[str]) -> MutableMapping[str, str]:
        """
        Sets `NO_PROXY` and `no_proxy` in `env` to the merged value, keeping what was there.

        Args:
            env (MutableMapping[str, str]): `os.environ` or a child's environment.
            *urls (Optional[str]): The model server's URL and any other local endpoints.

        Returns:
            MutableMapping[str, str]: `env`, for chaining.
        """
        value = cls.merged(env.get("NO_PROXY"), env.get("no_proxy"), urls)
        for key in NO_PROXY_KEYS:
            env[key] = value
        return env
