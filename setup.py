from setuptools import setup, find_packages

setup(
    name="dgxcoder",
    version="1.2.0",
    packages=find_packages(),
    install_requires=[
        "pyyaml>=6.0",
        "rich>=13.0.0",
        "requests>=2.28.0",
        "sentence-transformers>=3.0.0",
        "sqlite-vec>=0.1.0",
    ],
    entry_points={
        "console_scripts": [
            "dgxcoder=dgxcoder.cli:main",
        ],
    },
)

