# LoRA adapters

Developer notes for `ling-admin adapter` and serving adapters on the default model. Spec: `specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md`; the probe that decided what is served: `docs/dev/lora-serving-probe-2026-10-19.md` on branch `research/lora-serving-probe`.

## Installing (`dreamference/hardware/adapter_*.py`)

- `AdapterManifest` parses `manifest.json` strictly: unknown schema or key, a missing key, a file that is not safetensors or JSON, a path leaving the folder, a digest that is not sha256, all refused.
- `AdapterSignature` checks the SSHSIG signature with `ssh-keygen -Y verify` under the namespace `mightling-adapter`, against `RELEASE_KEYS` (the lines of `ling-rs/release-signing.pub`; a test keeps them equal). A `mightling-release` signature never passes as an adapter's.
- `AdapterVerifier` is the one check `install`, `verify`, a receiving node and `server start` share: the catalogue's pin of the manifest, the manifest, the signature (waived only for an adapter installed with `--third-party`), every file's sha256, the configuration and the tensors against the recipe's `lora` key (attention and GDN modules only, rank ceiling), and the base. The base is asked of the server (`/v1/models`); with none answering, the configured model stands in.
- `AdapterBaseDigest` takes the digest of §5.2 without reading the weights: a weight file in the hub cache links to a blob named by its sha256, so the link target is its hash; only the small files are hashed. Cached by (repo, revision) in `~/.cache/dreamference/adapters/base-digests.json`.
- `AdapterInstaller.install` fetches `manifest.json` and its signature alone first, checks them and the base, and only then fetches the files the manifest lists (`allow_patterns`). Files live in the hub cache as a snapshot at the pinned commit; `installed.json` (`AdapterStore`, 0600) records the snapshot relative to the hub, so the engine's container finds it under its own mount.
- `ADAPTER_CATALOG` (`adapter_catalog.py`) is empty until the first adapter meets the publishing bar; the organisation's slug `dreamference` is assumed (spec §17, question 9).

## Signing (`.github/workflows/adapter-sign.yml`, `scripts/adapters/sign_manifest.py`)

Manifests are signed in CI, never by hand (spec §5.1). The input is a manifest committed to this repository at `adapters/<name>/<version>/manifest.json`, the folders naming the manifest's own `name` and `version`, with `source.json` beside it: `{"repo": "dreamference/mightling-<name>", "commit": "<40 hex>"}`, the adapter's Hugging Face repository and the commit its weights were uploaded at (the manifest has no field for either, and its parser refuses unknown keys). A push of one to `main` runs the job in the `release` environment:

- `check`: `AdapterManifest.parse`; every listed file's sha256 against the repository's metadata at that commit (LFS files by their recorded sha256, the few small files downloaded, checked against their git blob id and hashed); `base` and `drafter_base` against the registry's pins (the matrix entry's `revision`, its `speculative_config` drafter); and each digest against `AdapterBaseDigest.compute` over a snapshot staged from the base repository's metadata (weights as links named by their LFS sha256, as in the hub cache), so publishing and installing take the digest with the same code.
- `sign`: checks again, writes `manifest.json.sig` with `ssh-keygen -Y sign -n mightling-adapter` (the key a 0600 file for that step only), and verifies it with `AdapterSignature` against `ling-rs/release-signing.pub`; a key that is not a release key stops the job. The pair is attested (build provenance).
- `publish`: checks and verifies again, uploads both files as one commit whose parent is exactly the checked commit (`HF_ADAPTER_TOKEN`), and writes the catalogue entry; `catalog` adds it to `ADAPTER_CATALOG` and the job opens the pull request.

Any failure stops before anything is signed or uploaded. `publish --dry-run --key <file>` checks and signs with a key of your own and never uploads; `tests/test_adapter_signing.py` runs it with session keys and the hub mocked. The repository must allow GitHub Actions to create pull requests (Settings, Actions, General).

## Paired nodes

`node sync-model` follows a successful model copy with `NodeAdapterSync.send`: one `adapter-receive <name> <repo> <commit> <size>` per installed adapter that Dreamference signed for that model, a tar of its cache folder over the same forced command. The receiving node unpacks into a staging folder, checks the blobs and the adapter itself (its own key list), and only then merges and records it. A third-party adapter is never sent.

## Output

New modules outside `dreamference/cli/` may not `print` (the quality ratchet's T201): the installer raises `ValueError` with the reason and returns entries, `AdapterCommand` (`dreamference/cli/adapter_command.py`) prints, and the receiving side of a sync writes its JSON line and refusals to the SSH session's own streams.
