"""Where the harness keeps things: secrets under `~/.config/dreamference/`, the rest in the cache."""

from __future__ import annotations

from pathlib import Path


def config_dir() -> Path:
    """`~/.config/dreamference`."""
    return Path.home() / ".config" / "dreamference"


def telegram_dir() -> Path:
    """The TDLib session and the harness's Telegram state (0700)."""
    return config_dir() / "telegram-live"


def signal_dir() -> Path:
    """The test device's signal-cli data (0700)."""
    return config_dir() / "signal-live"


def cache_dir() -> Path:
    """TDLib's library, the voice fixture, the scratch bridge's home, the reports."""
    return Path.home() / ".cache" / "dreamference" / "messenger-live"


def private_dir(path: Path) -> Path:
    """Makes a 0700 folder (and fixes its mode) and returns it."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path
