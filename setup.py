from setuptools import setup, find_packages

setup(
    name="dreamference",
    version="1.6.0",
    license="AGPL-3.0-or-later",
    classifiers=[
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
    ],
    packages=find_packages(),
    # Floors for requests, sentence-transformers, sqlite-vec, fonttools and Pillow are the first
    # releases without a published advisory (specs/DREAMFERENCE_MIGHTLING_DEPENDENCY_AUDIT.md).
    install_requires=[
        "pyyaml>=6.0",
        "toml>=0.10.2",
        "rich>=13.0.0",
        "requests>=2.33.0",
        "sentence-transformers>=5.6.0",
        "sqlite-vec>=0.1.3",
        "tensorizer>=2.0.0",
        "einops>=0.7.0",
        "fonttools[woff]>=4.60.2",
        # Imported directly (brand assets, image search, web tools), not just a dependency of a
        # dependency: they were only ever present locally because another package pulled them in.
        "Pillow>=12.3.0",
        "beautifulsoup4>=4.12.0",
    ],
    extras_require={
        # The code standard's tools (specs/DREAMFERENCE_PYTHON_QUALITY.md §4), pinned: the ratchet
        # test compares ruff's findings with a baseline that only the pinned version recorded.
        "dev": ["ruff==0.16.10", "mypy==2.4.0"],
    },
    entry_points={
        "console_scripts": [
            "ling-admin=dreamference.cli:main",
            # `ling-search` and `ling-fetch` are not console scripts: they are Rust binaries
            # (ling-web-rs/) that `ling-admin codex build` installs beside `ling`.
        ],
    },
)

