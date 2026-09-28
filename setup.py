from setuptools import setup, find_packages

setup(
    name="dreamference",
    version="1.2.0",
    license="AGPL-3.0-or-later",
    classifiers=[
        "License :: OSI Approved :: GNU Affero General Public License v3 or later (AGPLv3+)",
    ],
    packages=find_packages(),
    install_requires=[
        "pyyaml>=6.0",
        "toml>=0.10.2",
        "rich>=13.0.0",
        "requests>=2.28.0",
        "sentence-transformers>=3.0.0",
        "sqlite-vec>=0.1.0",
        "tensorizer>=2.0.0",
        "einops>=0.7.0",
        "fonttools[woff]>=4.50.0",
    ],
    entry_points={
        "console_scripts": [
            "puffin-admin=dreamference.cli:main",
            "puffin=dreamference.cli:puffin_main",
        ],
    },
)

