# Contributing to Mightling

Thank you for your interest. Two things to know before you open a pull request:

1. **Every contributor signs the [Contributor License Agreement](CLA.md) once.** A check on each pull request
   asks for it, and the pull request cannot be merged until it is signed. The agreement lets Dreamference
   relicense the code (for example, to offer it under terms other than the AGPL), which keeps the project's
   ownership simple. You keep the copyright in what you write.
2. **Start from [AGENTS.md](AGENTS.md).** It is the project guide: how to install, how to run the tests
   (`.venv/bin/python -m pytest tests/ -q`, no GPU or Docker needed) and the rules the code follows. The
   design is in [`specs/`](specs/README.md) and the longer notes are in [`docs/dev/`](docs/dev/).

Bugs and ideas are welcome as issues, no agreement needed. Security problems go through
[SECURITY.md](SECURITY.md) instead.
