"""What kind of setup key a bundle carries (spec §4.5)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BundleOptions:
    """What kind of setup key a bundle carries and whether it holds the node's CA."""

    ephemeral: bool = False
    minutes: int | None = None
    with_ca: bool = True
