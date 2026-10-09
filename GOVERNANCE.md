# Governance

Mightling is maintained by Dreamference. Dreamference reviews and merges changes and makes the final
call on the design, on what goes into the product and on when a release is cut. Decisions are written
down where the code is: a change to how something works lands together with its spec in
[`specs/`](specs/README.md), which records what was decided and why.

## Proposing a change

1. **Open an issue** for anything larger than a small fix: a bug with what you ran and what happened,
   or an idea with the problem it solves. That is where the design is agreed before code is written.
2. **Open a pull request** that refers to the issue. Every contributor signs the
   [Contributor License Agreement](CLA.md) once; a check on the pull request asks for it, and nothing
   is merged without it. [CONTRIBUTING.md](CONTRIBUTING.md) says how to build and test.
3. **Review.** The pull request is merged when its tests pass and the maintainers accept it; a change
   that alters behaviour also updates the spec and the docs it touches.

Security problems are not proposed in public: see [SECURITY.md](SECURITY.md).

## Releases

A release is cut by hand from `main`, with the **Release** workflow
([`.github/workflows/release.yml`](.github/workflows/release.yml)). It builds every binary on GitHub's
runners and publishes them as a release tagged `v<version>`. The last step runs in the GitHub `release`
environment, which only `main` can use and which waits for a maintainer's approval; its key, held only
there, signs `SHA256SUMS` (Ed25519), and each file carries a build-provenance attestation. `ling update`
and `install.sh` check the signature against the keys in `ling-rs/release-signing.pub` before
installing anything. Windows builds are an unsigned preview for now.
