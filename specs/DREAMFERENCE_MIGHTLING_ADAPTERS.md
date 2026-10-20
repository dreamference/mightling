# Mightling Adapters — signed LoRA specialists on the served base

> - **Status:** proposed, nothing built. The design was decided by the owner on 2026-10-19 (§2), and four of this document's open questions were answered the same day (§2.1: signing in CI, the publishing bar, the Rust benchmark, adapters on paired nodes); everything else here is the specification of that design, with the points still open in §17.
> - **The probe answered §8.4 on 2026-10-19** (branch `research/lora-serving-probe`, `docs/dev/lora-serving-probe-2026-10-19.md`): on the pinned v0.5.21 with DFlash2, adapters on the FP8 layers (full-attention q/k/v/o and the Gated DeltaNet projections, in SGLang's names `in_proj_q/k/v/z` stacked into `in_proj_qkvz`) work with the production launch unchanged, at 54–56 against 57.7 tok/s; adapters on the NVFP4 MLP crash with torch.compile and the fused SiLU+FP4 path; the drafter runs unadapted; `/v1/responses` ignores the adapter until a one-line patch passes `lora_path`; the GPU pool costs about 0.17 GiB per slot on the FP8 layers at rank 16 (0.30 GiB with the MLP). Where this document assumed otherwise (§7.1's first targets, §8.2's module list, §8.4's open answers), the probe's measurement wins.
> - **2026-10-20: the owner decided to build the serving side** (Phases 1a–1c, the desktop picker later; the signing job as a workflow file, not run): the adapter commands are `ling-admin adapter list|install|remove|verify` (`install` where this document says `download`), the recipe targets the attention and GDN modules only, and switching is per session (`/adapter rust|off`).
> - **Builds on:** the model registry and the served checkpoint ([MODELS](./DREAMFERENCE_MODELS.md)), the SGLang recipe, the model gate and host safety ([INFERENCE](./DREAMFERENCE_INFERENCE.md), `docs/dev/swe-bench.md`), the release key and SSHSIG format ([RELEASE_SIGNING](./DREAMFERENCE_RELEASE_SIGNING.md)), the launcher and the patch budget ([MIGHTLING_CODEX](./DREAMFERENCE_MIGHTLING_CODEX.md) §3–§4), the desktop app's model chip ([MIGHTLING_DESKTOP_PARITY](./DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md) §2.3), the egress audit ([MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md)), and the drafter work of [SELF_SPEEDING](./DREAMFERENCE_SELF_SPEEDING.md).

---

## Table of Contents

- [1. Goal](#1-goal)
- [2. The owner's decisions (2026-10-19)](#2-the-owners-decisions-2026-10-19)
- [3. Surface](#3-surface)
- [4. The adapter package and its manifest](#4-the-adapter-package-and-its-manifest)
- [5. Trust: signatures, pins and third-party adapters](#5-trust-signatures-pins-and-third-party-adapters)
- [6. The catalogue, download and storage](#6-the-catalogue-download-and-storage)
- [7. Training against the served base](#7-training-against-the-served-base)
- [8. Serving, Phase 1: SGLang multi-LoRA](#8-serving-phase-1-sglang-multi-lora)
- [9. Serving, Phase 2: LoRA in ling-engine](#9-serving-phase-2-lora-in-ling-engine)
- [10. The model id, the gate and the launcher](#10-the-model-id-the-gate-and-the-launcher)
- [11. Per-session switching: `/adapter` and the desktop picker](#11-per-session-switching-adapter-and-the-desktop-picker)
- [12. Memory and host safety](#12-memory-and-host-safety)
- [13. Security](#13-security)
- [14. Egress and the air gap](#14-egress-and-the-air-gap)
- [15. Phases and acceptance](#15-phases-and-acceptance)
- [16. Tests](#16-tests)
- [17. Open questions](#17-open-questions)
- [18. Sources (read 2026-10-19)](#18-sources-read-2026-10-19)

---

## 1. Goal

Make the one served model better at specific languages without serving a second model. A **LoRA adapter** is a small low-rank change to the model's linear layers; served **unmerged**, many adapters share one copy of the base weights, and each request names the adapter it wants. The first adapters are per-language specialists (Rust, C++/CUDA, TypeScript), chosen per session by the person using `ling` or the desktop app.

**Why adapters and not a second model.** On a GB10 the 128 GB is shared by the host and the GPU, and the served recipe already reserves half of it (`--mem-fraction-static 0.50`, plus 24 GB of container headroom, INFERENCE §5.3). A second 27B is not affordable; an adapter at rank 16 on every linear layer is about 220 MB (§12), and a library of 100 fits on disk at about 22 GB.

**Why it might not be worth it.** Nothing here is measured yet. An adapter is offered only when its own evaluation (§7.3) shows a gain on its language, no loss elsewhere beyond a set margin, and a decode speed within a set margin of the base with its drafter adapter on. An adapter that fails those is not published.

**Non-goals (out of scope for now, the owner's decision):**
- training many adapters (this spec covers the first few and the requirements every adapter meets, not a training pipeline at scale);
- automatic per-task selection (the person chooses; nothing picks an adapter from the prompt or the repository);
- third-party marketplaces (a third-party adapter can be installed by hand, behind a flag and a warning, §5.4; there is no index of them, no search and no ratings).

---

## 2. The owner's decisions (2026-10-19)

1. **Hosting.** Several adapters, published on a Dreamference organisation on Hugging Face, in safetensors.
2. **Trust.** Only adapters signed by Dreamference install by default, with the Ed25519 release key that releases already use. A third-party adapter needs an explicit flag and shows a warning.
3. **Switching.** Per session: `/adapter <name>` in `ling`, and a picker in the desktop app. Concurrent sessions may use different adapters.
4. **First adapters.** Per-language specialists, for example Rust, C++/CUDA and TypeScript.

### 2.1 Answers to this document's questions (2026-10-19)

The first draft's open questions 5, 7, 8 and 11 (§17) were answered by the owner on the day:

- **Signing is a CI job** in the GitHub `release` environment, with the release Ed25519 key, under the `mightling-adapter` namespace, and automatic, like releases (RELEASE_SIGNING §4, changed 2026-10-12). §5.1 specifies the job.
- **The publishing bar:** a clear gain on the adapter's language; at most **1 task lost** on a Python control night; at most **5% tok/s lost**, measured with its drafter adapter (§7.3).
- **The Rust benchmark** is the Rust subset of SWE-bench Multilingual (§7.3). The C++/CUDA and TypeScript sets are not chosen yet.
- **Paired nodes:** `ling-admin node sync-model` copies the installed **signed** adapters to paired nodes (§6.4).

The constraints set with them, each specified below: air-gapped by default, with downloading an explicit command (§6, §14); a signed manifest (§4); training against the exact served NVFP4 checkpoint, with a drafter adapter per adapter (§7); unmerged serving, SGLang multi-LoRA first and ling-engine second (§8, §9); the model id `base+<adapter>` (§10); and four security rules (§13).

---

## 3. Surface

| Command | Where | Effect |
|---|---|---|
| `ling-admin adapter list [--available] [--json]` | node | Installed adapters: name, version, base it is pinned to, rank, size, trust (`dreamference` or `third-party`), whether the served base matches it, and whether it is loaded now. `--available` adds the catalogue this release ships (§6.1), marking which are installed. **No network** either way |
| `ling-admin adapter install <name>[@<version>]` | node | Fetches a catalogued adapter at its pinned revision, verifies it (§5) and installs it. The only adapter command that reaches the network |
| `ling-admin adapter install --repo <org>/<repo>@<commit>` | node | An adapter signed by Dreamference but not in this release's catalogue (published after it). The commit is required: a branch name is refused |
| `ling-admin adapter install --third-party --repo <org>/<repo>@<commit>` | node | A third-party adapter (§5.4): prints the warning, asks for the typed confirmation, installs with `trust = third-party` |
| `ling-admin adapter verify [<name>]` | node | Checks each installed adapter again (signature, every file's hash, the served base) and says which pass; no network. `server start` runs the same check |
| `ling-admin adapter remove <name> [--version <v>]` | node | Unloads it if loaded (§8.3), deletes its snapshot and its entry. A running session on it has its next request refused with the reason, never silently answered by another model; `/adapter off` or a new session returns to the base (§10.3) |
| `ling-admin node sync-model <node>` | node | Also copies the installed signed adapters to the paired node, which verifies them itself; third-party adapters are never copied (§6.4) |
| `/adapter`, `/adapter <name>`, `/adapter off` | `ling` TUI | Shows the session's adapter and the installed ones; switches this session (§11.1) |
| `ling --adapter <name> …` | `ling` | Starts or resumes a session with that adapter; the launcher handles the flag before Codex parses its arguments, like `ling app` (§10.3) |
| `mightling_adapter = "<name>"` / `DREAMFERENCE_MIGHTLING_ADAPTER` | config / env | The adapter a new session starts with; unset means the base. The tier order is cave mode's: the session's own file, the variable, the TOML file, then the base |
| The model chip | desktop app | Lists the base and every installed adapter; the choice is the thread's (§11.2) |
| `swe-bench run --adapter <name>`, `[night] adapter` | arms | An adapter as an A/B arm, recorded in the manifest as `adapter` and `adapter_manifest_sha256` (§7.3) |

Every `ling-admin adapter` command rejects unknown arguments, as every `ling-admin` command does ([CLI](./DREAMFERENCE_CLI.md) §1); a group without a subcommand prints its help. `adapter` is a node command: on a client (no `~/.config/dreamference/node-id`) it says that adapters are installed on the node and names it. A client sees the node's adapters through `/v1/models` (§10.1) and needs nothing installed.

**Where the code goes.** The registry of catalogued adapters, the installer and the signature check sit in `dreamference/hardware/` beside `ModelMatrixRegistry` and `ModelDownloader` (`adapter_catalog.py`, `adapter_installer.py`, `adapter_signature.py`), one class per file, so no thirteenth package is added. The loader and the id rewrite are in `dreamference/vllm_server/` (`model_gate_service.py`, `lora_adapter_loader.py`). The launcher's side is `ling-rs/src/adapter.rs`.

---

## 4. The adapter package and its manifest

### 4.1 Files in the Hugging Face repository

One repository per adapter, `<org>/mightling-<name>` (for example `…/mightling-rust`; the organisation's slug is open, §17), with one tag per version:

| File | What |
|---|---|
| `adapter_model.safetensors` | The target adapter's A and B matrices, BF16. safetensors only: no pickle, so loading the file runs no code |
| `adapter_config.json` | PEFT's configuration (`r`, `lora_alpha`, `target_modules`, `base_model_name_or_path`), which SGLang reads |
| `drafter/adapter_model.safetensors`, `drafter/adapter_config.json` | The drafter adapter (§7.2), for the DFlash2 drafter |
| `manifest.json` | §4.2 |
| `manifest.json.sig` | The SSHSIG signature of `manifest.json` (§5.1) |
| `README.md` | The model card: what the adapter is for, its scores, its licence. Informational; nothing reads it |

Nothing else in the repository is downloaded: `download` fetches exactly the files the manifest lists (`allow_patterns`), so a file added later, or a `.py` file, never reaches the machine.

### 4.2 The manifest

`manifest.json` is the one thing the signature covers, and it names every other file by hash.

```json
{
  "schema": 1,
  "name": "rust",
  "display_name": "Rust",
  "version": "1.0.0",
  "description": "Rust specialist: crates, borrow checker errors, cargo and clippy output.",
  "base": {
    "registry_alias": "qwen3.8-27b-nvfp4-dflash2",
    "repo": "RadixArk/Qwen3.8-27B-NVFP4",
    "revision": "52d1adc5…",
    "digest": "sha256:…"
  },
  "drafter_base": {
    "repo": "maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal",
    "revision": "bd7a9342…",
    "digest": "sha256:…"
  },
  "lora": {
    "rank": 16,
    "alpha": 32,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    "dtype": "bfloat16"
  },
  "drafter_adapter": {
    "rank": 8,
    "target_modules": ["…"],
    "files": { "drafter/adapter_model.safetensors": "sha256:…", "drafter/adapter_config.json": "sha256:…" }
  },
  "files": {
    "adapter_model.safetensors": "sha256:…",
    "adapter_config.json": "sha256:…"
  },
  "evals": [
    {
      "suite": "…", "tasks_sha256": "sha256:…", "tasks": 50,
      "base": 0.0, "adapter": 0.0,
      "engine": "sglang", "image": "lmsysorg/sglang@sha256:b1259f3ea327…",
      "decode_tok_s": { "base": 0.0, "adapter_with_drafter_adapter": 0.0, "adapter_with_base_drafter": 0.0 },
      "accepted_per_step": { "base": 0.0, "adapter_with_drafter_adapter": 0.0, "adapter_with_base_drafter": 0.0 },
      "date": "2026-…"
    }
  ],
  "licence": { "spdx": "…", "base_licence": "…", "training_data": "…" },
  "signer": "mightling-release-2026-10"
}
```

- **`base.digest`** pins the adapter to the exact served checkpoint (§5.2). `registry_alias`, `repo` and `revision` say where it came from; the digest is what is checked.
- **`drafter_adapter`** is optional in the format and required for anything Dreamference publishes (§7.2). An adapter without one is served with the base drafter, and `list` says so.
- **`evals`** records the run that justified publishing it (§7.3), including the engine and image it ran on, so a later reading on another engine is not mistaken for the same measurement.
- **`licence`** names the adapter's own licence, the base model's, and a one-line account of the training data's sources and their terms.
- **`signer`** is the comment of the key that signed it, for the person reading `list`; the verifier does not trust it, it trusts the signature.
- An unknown `schema`, an unknown top-level key or a missing required one is refused: the manifest is parsed strictly, as the launcher parses `model_catalog.json`'s shape.

---

## 5. Trust: signatures, pins and third-party adapters

### 5.1 Signed by the release key, under its own namespace

The manifest is signed with the release key (RELEASE_SIGNING §4), in OpenSSH's SSHSIG format, under a **new namespace, `mightling-adapter`**:

```
ssh-keygen -Y sign -f release-ed25519.key -n mightling-adapter manifest.json
```

RELEASE_SIGNING §3 keeps `mightling-release` (checksum files) and `mightling-release-key` (key transitions) apart; `mightling-adapter` is a third. A release signature is refused as an adapter signature and an adapter signature is refused as a release signature, by every verifier, so a signed manifest can never be passed off as a signed `SHA256SUMS` or the reverse.

The chain is RELEASE_SIGNING §2's shape:

```
release key (Ed25519)
  └─ manifest.json.sig     ssh-keygen -Y sign -n mightling-adapter
       └─ manifest.json    base digest, drafter base digest, sha256 of every file:
            ├─ adapter_model.safetensors, adapter_config.json
            └─ drafter/adapter_model.safetensors, drafter/adapter_config.json
```

**Signing in CI (decided 2026-10-19).** Manifests are signed by a job of the release workflow's kind, never by hand:

- **Where:** a workflow job that declares `environment: release`, so it can read `RELEASE_SIGNING_KEY` only from `main` and `release/*` (RELEASE_SIGNING §4's branch policy). Like releases since 2026-10-12, it asks for no approval: a push of a manifest to `main` ends in a signed, published adapter.
- **Input:** a manifest committed to this repository (`adapters/<name>/<version>/manifest.json`) whose `files` name weights already uploaded to the adapter's Hugging Face repository at a named commit. The weights come from training (§7), not from the workflow.
- **Steps:** check that each file's sha256 in the manifest equals the Hugging Face repository's recorded hash for that file at that commit (from repository metadata, without downloading the weights); check `base.digest` and `drafter_base.digest` against the registry's pinned revisions; sign with `ssh-keygen -Y sign -n mightling-adapter`, the key written to a 0600 temporary file only for that call; verify the signature against `ling-rs/release-signing.pub` before publishing, as the release job checks its own (a key that does not match stops the job); upload `manifest.json` and `manifest.json.sig` to the adapter's repository as a new commit; attach a build-provenance attestation to both files, as releases do.
- **Output:** the new commit and the manifest's sha256, written back as the catalogue entry (§6.1) by a pull request the job opens, so the next release carries the pin.
- **Upload credential:** a Hugging Face write token for the organisation, a secret of the same `release` environment, used by this job alone.
- A manifest missing a required field, a hash that does not match, or a base that is not the registry's stops the job before anything is signed.

**Verifying in `ling-admin`.** Python's standard library has no Ed25519 (RELEASE_SIGNING §3), and the package has no cryptography dependency. `ling-admin` verifies with `ssh-keygen -Y verify`, exactly as install.sh does, against an `allowed_signers` file built from the public keys the package carries (`dreamference/hardware/release-signing.pub`, package data, so a release install has it too). A test keeps that file equal to `ling-rs/release-signing.pub`, as one already keeps install.sh's `RELEASE_KEYS` equal to it. A missing `ssh-keygen` stops `download` and names the package (`openssh-client`); there is no opt-out (the same reasoning as RELEASE_SIGNING §3). The alternative, a `ling adapter verify` subcommand backed by `release_signature.rs`, is §17's question 6.

**When it is checked.** At `download`, before the adapter is recorded as installed; at every `server start`, for every installed adapter, before the engine is told about it; and at every load (§8.3), where the files are hashed again against the manifest. A file changed on disk after installation is caught at its next load, and the adapter is refused, not served.

**Key rotation.** The key list is the release's, so a rotation (RELEASE_SIGNING §5) reaches adapters at the next update. Manifests are small: on a rotation every published manifest is re-signed with the new key and its `.sig` replaced at a new commit, and the catalogue's pins move with it (§6.1). No transition file is needed for adapters, because an older install verifies an older catalogue's pinned commit, whose signature its own key list accepts. A stolen key is handled as RELEASE_SIGNING §5 says, and the catalogue's pins are what an updated install trusts afterwards.

### 5.2 Pinned to the exact base

`base.digest` is `sha256` over a canonical listing of the base checkpoint's weight and configuration files: one line per file, `<sha256>  <path>`, sorted by path, covering `*.safetensors`, `config.json`, `tokenizer*.json` and the chat template, at the pinned revision. Hugging Face already records each large file's sha256 (its LFS object id), so the digest can be computed from repository metadata when publishing and from the files in the local snapshot when checking.

- **It is checked against the served model, never the configuration** (AGENTS.md's rule): the served id from `/v1/models` is mapped to its registry entry with `model_key_for_served_id` (which learns to strip `+<adapter>`, §10.1), and that entry's snapshot is hashed once per revision and cached by `(repo, revision)`. An adapter for the default model is therefore refused while Minima (MODELS §2.2) is served, and listed as `base mismatch`.
- **A base change retires every adapter at once.** When the registry moves the default to another checkpoint or revision, every adapter pinned to the old one is listed as `base mismatch`, is not loaded, and is not offered by `/adapter` or the picker. New versions trained on the new base are published with the release that moves it (§7.4).
- **The drafter is pinned the same way**: `drafter_base.digest` must match the drafter the recipe serves, or the drafter adapter is not loaded and the target adapter is served with the base drafter (and `list` says so).

### 5.3 What the pins do not cover

The signature says Dreamference published these bytes; the digest says they were made for this base. Neither says an adapter is good. That is what §7.3's evaluation is for, and why a signed adapter is still only installed by a person's explicit `download`.

### 5.4 Third-party adapters

A third-party adapter is anything without a valid Dreamference signature. It installs only with `--third-party` and an explicit commit:

```
⚠️  This adapter is not signed by Dreamference.
    An adapter changes what the model writes: it can make the model suggest
    insecure code, ignore your instructions or leave out what it was asked to keep.
    Mightling will check that its files match its manifest and that it was made
    for the model this machine serves, and nothing else.
    Type the adapter's name to install it:
```

- The manifest is still required and still parsed strictly; its files are still hashed against it, and its base digest still has to match the served base. Only the signature is waived.
- It is recorded as `trust = third-party`. `list`, `/adapter`, the desktop picker and the egress audit's report (§14.2) mark it on every line, and a session using one prints a notice at start (`notice.rs`).
- There is no `--yes`: the typed name is the confirmation, so a script or an agent cannot install one unattended. `ling-admin` is never run by the agent's sandbox (it needs the host), and the TUI's `/adapter` cannot install anything (§11.1).

---

## 6. The catalogue, download and storage

### 6.1 The catalogue ships in the release

`list --available` must work with no network, and `download <name>` must know exactly which commit to fetch. Both come from a **catalogue in the package**, `ADAPTER_CATALOG` in `dreamference/hardware/adapter_catalog.py`, a registry like `ModelMatrixRegistry.MATRIX`:

| Field | Example |
|---|---|
| name, display name, version | `rust`, `Rust`, `1.0.0` |
| repo, pinned commit | `<org>/mightling-rust`, `9f3c…` |
| `manifest_sha256` | the sha256 of the signed `manifest.json` at that commit |
| base alias | `qwen3.8-27b-nvfp4-dflash2` |
| size | bytes, for the memory and disk checks before downloading |

The catalogue is in the wheel, and the wheel is listed in the release's signed `SHA256SUMS` (RELEASE_SIGNING §2), so what `download <name>` fetches is pinned by a signed release *and* checked by the manifest's own signature. New adapters, new versions and re-signed manifests arrive with a release. An adapter published between releases is installed with `--repo <org>/<repo>@<commit>`, which needs the manifest signature alone.

The alternative, listing the Hugging Face organisation at `--available`, would need the network for a read-only command and would trust whatever the listing returned. It is not offered.

### 6.2 Download

`download` reuses `ModelDownloader`: `huggingface_hub.snapshot_download` at the pinned commit with `allow_patterns` set to the manifest's file list plus the manifest and its signature, then the same fallback to `huggingface-cli`. The steps, in order, each one stopping the command on failure:

1. Resolve the name to a catalogue entry (or take `--repo`), and refuse a branch name.
2. Check the disk and the base: the served base must match the entry's base alias (§5.2), or the command stops with `base mismatch` and the alias the adapter needs.
3. Fetch `manifest.json` and `manifest.json.sig` only. Check the manifest's sha256 against the catalogue (when catalogued), then the signature (§5.1), unless `--third-party`.
4. Fetch the files the manifest lists, and hash each against it.
5. Record it in the installed set (§6.3). If the server is running, the adapter is available at once: the loader (§8.3) loads it on first use. `download` itself never talks to the engine.

The token is the model download's (INFERENCE §2.1): `--hf-token`, `HF_TOKEN`, `DREAMFERENCE_HF_TOKEN`, `hf_token`. Public adapter repositories need none.

### 6.3 Storage

- **Files:** the Hugging Face hub cache, as snapshots at their pinned commits, exactly like the model's (INFERENCE §2). `ModelDownloader.container_volume_args()` already mounts that cache into the engine's container, so nothing new is mounted, and the `--revision` trap of INFERENCE §5.3 does not arise because adapters are passed to the engine as snapshot directories.
- **The installed set:** `~/.local/share/dreamference/adapters/installed.json`, mode 0600: for each adapter its name, version, repo, commit, snapshot path, `manifest_sha256`, trust, and the date installed. Written by `ling-admin` only, atomically (a temporary file and a rename), and mounted read-only into the gate's container (§8.3), together with the hub cache's adapter snapshots, which the gate hashes before each load.
- **Never in a repository.** Which adapters a person installed, and which one a session uses, are the user's choices, and live under the user's home (AGENTS.md's rule), never in a workspace a clone or the agent can write.
- **A library of 100** at rank 16 is about 22 GB on disk (§12). `remove` deletes the adapter's snapshot from the hub cache through `huggingface_hub`'s cache API, never a parent folder (the lesson of `clear model-cache`, README "Fixed on 2026-09-29").

### 6.4 Paired nodes (decided 2026-10-19)

`ling-admin node sync-model` (MIGHTLING_NODE) also copies the installed **signed** adapters to the paired node, over the same SSH forced command as the model, after the model itself:

- **Only `trust = dreamference` adapters are copied.** A third-party adapter is never copied: its install needs the typed confirmation of §5.4 on each node, by a person.
- **The receiving node verifies on its own** before recording anything: the signature against its own key list, every file's hash against the manifest, and the base digest against the model *it* serves. A node that serves another base receives the files but lists them as `base mismatch`, as a download would.
- **Nothing is removed on the other side.** An adapter removed here stays installed on the paired node until `adapter remove` runs there.
- Copying adds no destination outside the LAN: it is the node-to-node path the model already takes.

---

## 7. Training against the served base

This section sets the requirements every published adapter meets. How many are trained, on what data and where is outside this spec (§1, non-goals).

### 7.1 The exact NVFP4 checkpoint

Adapters are trained against the checkpoint production serves, `RadixArk/Qwen3.8-27B-NVFP4` at the registry's revision, **not** against the BF16 model it was quantized from. The forward pass during training uses the NVFP4 weights as served (each block dequantized with its own scales), so the adapter learns a correction for the model that actually answers, quantization error included. Training against BF16 and serving on NVFP4 would apply a delta learned for different weights.

- **The adapter itself is BF16**, and stays unmerged. Merging it into NVFP4 weights would re-quantize the sum and lose most of a rank-16 delta in the rounding.
- **The model is a hybrid** (MODELS §2.2): 48 Gated DeltaNet layers (`in_proj_qkv`, `in_proj_z`, `in_proj_a`, `in_proj_b`, `out_proj`), 16 attention layers (every fourth) and 64 MLPs. The target modules an adapter may use are those the serving engine applies LoRA to, which for SGLang on this architecture is open (§17, question 3). Until the probe answers, the first adapters target the attention projections and the MLPs only.
- **Tooling is open.** No common trainer does a LoRA backward through NVFP4 linear layers out of the box; the candidates are a dequantized BF16 copy of the NVFP4 weights (about 54 GB, which on a GB10 means the model server is stopped, as SELF_SPEEDING §5.3 found for the drafter) or a trainer with an NVFP4-aware forward. Which is used is a training decision; the requirement is only that the forward's weights equal the served ones.
- **The pin follows.** The base digest written into the manifest (§5.2) is computed from the same snapshot the training read.

### 7.2 A drafter adapter for every adapter

DFlash2 drafts from the target's hidden states, and an adapter moves both the hidden states and the tokens the target accepts. With the base drafter, acceptance per step would fall, and with it the speed that is the reason this model is served on SGLang at all (MODELS §2). So each adapter ships a **drafter adapter**: a small LoRA on the DFlash2 drafter (`maurienne-ai/Qwen3.8-27B-DFlash2-NVFP4-RTNcal`), trained on the adapted target's own outputs and hidden states on the adapter's training prompts, the on-policy route of SELF_SPEEDING §4.2 with the adapter on. The tooling is SELF_SPEEDING's (SpecForge's DFlash2 trainer), aimed at a LoRA instead of the full drafter.

Speculation stays exact whatever the drafter does: the target verifies every drafted token. A drafter adapter can therefore change speed, never output. It is still signed and hashed like the rest, because it runs in the engine.

### 7.3 Evaluation before publishing

Each adapter's `evals` (§4.2) record one run on this machine's serving stack, with these numbers for the base, the adapter with its drafter adapter, and the adapter with the base drafter:

- **its language's score** on a task set in that language. For Rust it is the **Rust subset of SWE-bench Multilingual** (decided 2026-10-19). C++/CUDA and TypeScript sets are still to be chosen (§17, question 7); SWE-bench Verified, the benchmark this project keeps, is Python only;
- **a Python control night**: the night's fixed SWE-bench Verified task list, base against adapter, to show the adapter costs nothing outside its language;
- **decode speed and accepted tokens per step**, single stream, `scripts/decode_speed.py` (INFERENCE §5.3), and the egress audit's `exec` pass with the adapter on (§14).

**The publishing bar (decided 2026-10-19).** An adapter is published only when all three hold:

| Condition | Bar |
|---|---|
| Its language | a clear gain over the base on its language's task set |
| The Python control night | at most **1 task** lost against the base |
| Speed | at most **5% tok/s** lost against the base, measured with its drafter adapter (single stream, `scripts/decode_speed.py`) |

Losing more than 5% only with the base drafter does not block publishing, but it is recorded in `evals`, because it is what Phase 1 serves if SGLang's drafter cannot take an adapter (§8.4). On a user's machine the same comparison is a `swe-bench run --adapter` arm; its result is the user's, and nothing is sent anywhere.

### 7.4 When the base moves

A new base (another checkpoint, a new revision, or Minima winning night 2) retires every adapter (§5.2). Retraining is not automatic: the release that moves the base ships the catalogue entries of whichever adapters were retrained for it, and the rest are listed as `base mismatch` until they are.

---

## 8. Serving, Phase 1: SGLang multi-LoRA

### 8.1 What SGLang offers

From SGLang's LoRA documentation (§18; the page describes the current release, and the pinned image is v0.5.21, so every flag below is checked on that image before it is used):

- `--enable-lora`; `--lora-paths` as `<NAME>=<PATH>` or JSON `{"lora_name", "lora_path", "pinned"}`;
- `--max-loras-per-batch` (default 8): the adapters one batch can use, which sizes the GPU pool; **one slot is the base model's**;
- `--max-loaded-loras`: the adapters held in CPU memory at a time, at least `--max-loras-per-batch`;
- `--lora-eviction-policy` `lru` (default) or `fifo`, for the GPU pool;
- `--max-lora-rank` and `--lora-target-modules`: fixed at launch; with dynamic loading the documentation recommends setting both, since an adapter of larger rank or other modules cannot be loaded later;
- `--lora-backend` `csgmv` (default, chunked SGMV) or `triton`;
- `POST /load_lora_adapter` and `POST /unload_lora_adapter` at run time; pinned adapters, at most `max-loras-per-batch − 1`;
- the adapter named in the compatible HTTP APIs as `model: "<base>:<adapter>"` on `/v1/chat/completions` and `/v1/completions`, and as `lora_path` on the native `/generate`.

The documentation does not mention `/v1/responses`, the only endpoint `ling` speaks (INFERENCE §5.3), nor speculative decoding with LoRA. Both are open (§8.4).

### 8.2 The recipe

LoRA settings are launch flags, so they are registry data (AGENTS.md: launch flags belong in the model registry). The main entry's `launch_overrides` gains a `lora` key, turned into SGLang flags by `SGLangLaunchBuilder.server_args`:

```python
"lora": {
    "max_lora_rank": 32,
    "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    "max_loras_per_batch": 4,       # the base and three adapters in one batch
    "max_loaded_loras": 8,          # the in-memory cache of §8.3
    "eviction_policy": "lru",
    "backend": "csgmv",
}
```

The values are starting points to be measured, not decided. With no adapter installed the flags are still passed, so installing one never needs a restart; the probe measures what `--enable-lora` alone costs the base (§8.4), and if it costs anything measurable the builder passes the flags only when `installed.json` lists an adapter for the served base, and `download` says a restart is needed. An adapter whose rank or modules fall outside the recipe's ceilings is refused at `download` with that reason.

### 8.3 Loading: the library, the cache and the pool

Three levels, as the owner described:

| Level | Holds | Size | Managed by |
|---|---|---|---|
| Library | every installed adapter, on disk | up to ~100 | `ling-admin adapter download/remove` |
| Loaded | adapters the engine holds in memory | `max_loaded_loras` (8) | the loader, least recently used out |
| Active | adapters in the GPU pool, usable by a batch | `max_loras_per_batch − 1` (3) | SGLang's LRU |

On a GB10, "CPU memory" and "GPU memory" are the same memory (MODELS §3), so the loaded and active levels are both counted in host safety (§12).

**The loader is in the model gate.** `server start` already puts the engine on `127.0.0.1:<port + 10000>` and the gate (`dreamference-gate-<port>`, `model_gate_service.py`) on the public port (`docs/dev/swe-bench.md`). Every request passes the gate, so the gate is where an adapter is loaded on first use:

1. A request names `<base>+<name>` (§10.1). The gate looks `name` up in `installed.json` (mounted read-only). Not installed (removed since the session chose it, or never installed) or `base mismatch`: answered 404 with the reason, in the API's usual error shape. The gate never substitutes the base: a thread's model does not change under it.
2. Not loaded: the gate hashes the adapter's files against the manifest (standard library `hashlib`; about 220 MB at rank 16, well under a second), unloads the least recently used loaded adapter if the cache is full, and calls the engine's `/load_lora_adapter` on loopback with the name and the snapshot path from `installed.json`. Concurrent first requests for one adapter wait on one load.
3. It rewrites the model id (§10.1) and forwards the request.

Nothing a request says ever becomes a path: the gate maps a name from the installed set to the path `ling-admin` recorded. That is how the rule "no user-triggered runtime loading through the server; only `ling-admin` loads" holds: the request selects, Mightling's own loader loads, from the verified library only. `server start` re-verifies every signature before it starts the gate (§5.1), and the gate refuses `POST /load_lora_adapter` and `POST /unload_lora_adapter` from every client on the public port, whatever the gate's state. Those paths are added to the gate's refusals and never to `READ_ONLY_PATHS`.

**The loopback port.** The engine's own port answers anything on this host, including a command run inside a `ling` sandbox with network access. To keep the load endpoints the gate's alone, the engine is started with an API key that only the gate holds (written for the gate's container, never into a workspace or `CODEX_HOME`), and the gate adds it towards the engine; `ling-admin`'s own calls to the engine's port (the readiness poll, the canaries) are given it too. Whether SGLang's `--api-key` covers the load endpoints on the pinned image is checked first (§17, question 4); if not, the engine's port moves to a unix socket or a network namespace the gate shares.

### 8.4 What the probe must answer

`research/lora-serving-probe` is testing whether multi-LoRA works together with DFlash2 on the pinned image. This spec leaves the answer open and says what each outcome means:

| Question | If yes | If no |
|---|---|---|
| Does `--enable-lora` start with the DFlash2 speculative flags and the recipe's mamba radix cache? | Phase 1 as written | Phase 1 cannot serve adapters with speculation. Serving them without speculation drops code from ~68 tok/s to about 26 (the published single-Spark figure without speculation, SELF_SPEEDING §1): not offered. Adapters wait for Phase 2 |
| Is an adapter's output exact with speculation on (temperature 0, the same tokens as the same adapter with speculation off)? | — | A correctness bug: not offered, whatever the speed |
| Does the drafter take a per-request LoRA? | Drafter adapters are loaded with their target adapters | The base drafter serves every adapter in Phase 1; drafter adapters wait for Phase 2, and the eval's `adapter_with_base_drafter` row (§7.3) is what Phase 1 serves |
| Does `/v1/responses` honour `<base>:<adapter>`? | The gate rewrites `+` to `:` | The gate must carry the adapter some other way, or Phase 1 is limited to what the Responses path supports (§10.1) |
| Which modules of the hybrid layers take LoRA? | — | Target modules limited to what SGLang applies (§7.1) |
| What does `--enable-lora` alone cost the base, and what does each distinct active adapter cost per step? | — | — |
| Do the `csgmv` and `triton` kernels give correct output on SM121? (The FP4 history, MODELS §2.1, is why this is asked.) | — | The other backend, or no adapters on this image |

### 8.5 Cost of concurrent adapters

The owner's estimate is **about 0.5–1 ms per step per distinct active adapter**. At 12 draft tokens one verify step on code takes about 64 ms single-stream (68.6 tok/s at about 4.4 accepted per step, INFERENCE §5.3), so each distinct adapter in a batch costs roughly 1–1.5% of a step. The recipe runs at most 8 requests; with `max_loras_per_batch` 4, a batch holds the base and three adapters, about 1.5–3 ms of a ~64 ms step. A fourth distinct adapter waits for a slot (SGLang's LRU); the probe measures how long. Sessions on the same adapter share its slot and cost nothing more.

**The prefix cache is per adapter.** An adapter changes the keys and values, so a prefix cached under one adapter is useless to another. A session that switches adapter mid-thread re-prefills its whole context on the next turn: at the measured 1,000–1,700 tok/s (MODELS §2), a 100K-token thread costs 60–100 s once (§11.1). Concurrent sessions on different adapters also share the 96 mamba state slots of the recipe, so each holds fewer cached states than one adapter alone would.

---

## 9. Serving, Phase 2: LoRA in ling-engine

ling-engine (`ling-engine/`, its own repository and spec) is the planned replacement for SGLang on this model. Adapters in Phase 2 are its work, specified in its own documents; what Mightling needs from it:

- **Batched multi-adapter kernels** beside its NVFP4 GEMMs: the base product once per batch, and each adapter's shrink and expand on only its own rows (the SGMV/BGMV pattern of Punica and S-LoRA), with the 0.5–1 ms per distinct adapter (§8.5) as the budget to meet or beat.
- **Drafter adapters per request**, so the drafter's acceptance follows the target's adapter (§7.2).
- **The LoRA layers its spec chooses for the Gated DeltaNet projections**, so adapters can target the 48 GDN layers as well.
- **Adapter-aware prefix caching**: cache entries keyed by adapter, and checkpointed states (ling-engine's own) per adapter.
- **The same contract as Phase 1**: `/v1/models` lists `base+<adapter>`, the loader loads from the verified library only, and nothing a request says becomes a path. The gate in front of it does not change.

The switch from Phase 1 to Phase 2 is the engine's switch; nothing in the adapter packages, the manifest or the surface changes with it.

---

## 10. The model id, the gate and the launcher

### 10.1 `base+<adapter>`

`/v1/models` lists the base, as today (the served name is the repository id, `RadixArk/Qwen3.8-27B-NVFP4`), and one entry per installed adapter whose base matches, as `RadixArk/Qwen3.8-27B-NVFP4+rust`, with the base's `max_model_len` and `owned_by`, and a `parent` naming the base.

- **The gate builds the list.** It passes the engine's `/v1/models` through (a read-only path that passes even while a SWE-bench run holds the gate) and appends the adapter entries from `installed.json`, so the list shows what is *installed*, not what happens to be loaded. Loading is the gate's business (§8.3).
- **The gate rewrites the id on the way in.** A request for `<base>+<name>` reaches SGLang as `<base>:<name>`; on ling-engine, as whatever its API says. The `+` form is Mightling's and stays stable across engines; `:` is SGLang's. If the probe finds that SGLang's `/v1/responses` ignores the suffix (§8.4), this rewrite is also where the adapter is carried some other way, and nothing above the gate notices.
- **The gate rewrites it back** in the response's `model` field, so a client sees the id it asked for.
- **`model_key_for_served_id` strips `+<adapter>`** before it looks the registry up, so the tool-call check, SWE-bench's model lookup and everything else that asks "which model is this" still finds the base's entry, and `main-model inspect` reports the adapter separately.

**The contract, as built (2026-10-20).** The gate and the launcher implement the two ends of this, so it is fixed here:

- An adapter's entry in the gate's `/v1/models` is `{"id": "<base>+<name>", "object": "model", "created": <base's>, "owned_by": <base's>, "root": "<base>", "parent": "<base>", "max_model_len": <base's>, "mightling_adapter": {"name", "version", "display_name", "description", "trust"}}`, appended after the base's own entry, one per adapter that `server start` verified for the served base (`trust` is `dreamference` or `third-party`). An entry with a `parent` is an adapter; the base is the entry without one.
- The engine's own adapter entries (SGLang lists every loaded adapter by its bare name) are dropped from the list the gate passes on, so a client never sees `rust` as a model of its own.
- The name the engine knows an adapter by is its installed name, verbatim (`[a-z0-9][a-z0-9-]{0,39}`), so `<base>+<name>` and SGLang's `<base>:<name>` have the same length and the gate rewrites them in place in a streamed body, both ways.
- A client may not name an engine-side adapter itself: a request whose `model` carries a `:` suffix, or that has a `lora_path` field, is refused with 400; `/load_lora_adapter`, `/load_lora_adapter_from_tensors` and `/unload_lora_adapter` are refused on the public port with 403.
- An unknown or removed `<base>+<name>` is answered 404 with `code: "adapter_not_installed"` (or `"adapter_base_mismatch"`) in the API's error shape.

### 10.2 The model catalog

The launcher reads `/v1/models` (MIGHTLING_CODEX §4, step 6) and today writes **one** entry to `$CODEX_HOME/model_catalog.json`. It writes one entry per listed id instead: the base and each adapter, each with the base's context window, compaction limits, reasoning level, tool mode and instructions, and a display name from the adapter's manifest ("Qwen3.8 27B · Rust"). Codex's strict serde shape is unchanged; there are only more entries. A third-party adapter's display name ends in "(third-party)".

### 10.3 Passing the session's choice through

Step 10 of the launcher prepends `--model <id>`. With adapters, `<id>` is the session's choice:

1. `ling --adapter <name>` (removed from the arguments before Codex sees them);
2. for `resume` and `fork`, the thread's own record, `$CODEX_HOME/adapter/<thread-id>` (§11.1), so a resumed thread keeps its adapter;
3. `DREAMFERENCE_MIGHTLING_ADAPTER`, then `mightling_adapter` in the TOML file;
4. the base.

A name that `/v1/models` does not list (removed, never installed, or `base mismatch` after a base change) falls back to the base with a notice at start ("The Rust adapter is not on this node; this session uses the base model."), never an error that stops the session. This fallback happens only at launch, where the person sees it; mid-session the gate refuses instead (§8.3).

**As built (2026-10-20, `ling-rs/src/adapter.rs`).** A tier that is set decides, even when it is empty: `""` or `off` in the flag, the record, the variable or the TOML key is the base, and the later tiers are not read, so the unattended callers' empty variable (§10.4) stops a user's `mightling_adapter` at the door and `/adapter off` keeps a resumed thread on the base. The record is the launcher's own because Codex's restore never runs under `ling`: `resume` keeps the model saved with the thread only when neither the model nor the provider is overridden, and the launcher always passes both. The thread is known at launch only from `ling resume <id>` and `ling fork <id>`; `resume --last` and the picker start from tier 3. `/adapter` writes the record; a session started with `ling --adapter <name>` has none until `/adapter` is used in it (its thread id does not exist at launch), and `ling resume <id> --adapter <name>` writes it. The base is the first `/v1/models` entry without a `parent`, and stays the catalog's first entry.

### 10.4 Unattended callers

Night Shift, SWE-bench, refine and the egress audit name their model explicitly. An adapter reaches them only as an arm (`--adapter`, `[night] adapter`), recorded in their manifests, never through a user's `mightling_adapter` default leaking into a benchmark run: those callers set `DREAMFERENCE_MIGHTLING_ADAPTER` themselves, empty for the base.

---

## 11. Per-session switching: `/adapter` and the desktop picker

### 11.1 `/adapter` in `ling`

- `/adapter` alone lists the installed adapters for the served base, with the session's current one marked and third-party ones flagged; `/adapter <name>` switches; `/adapter off` returns to the base.
- **The switch is Codex's own per-thread model override**, the same operation `/model` performs, to `<base>+<name>`. Each thread has its own model, so concurrent sessions on different adapters need nothing more. `/model` with the catalog of §10.2 reaches the same entries; `/adapter` is the short form with Mightling's wording, the manifests' descriptions and the warnings.
- The choice is written to `$CODEX_HOME/adapter/<thread-id>`, as cave mode keeps its level per thread (`cave.rs`), so `resume` restores it (§10.3).
- **Switching mid-thread costs a re-prefill** (§8.5). When the thread already holds more than a few thousand tokens, `/adapter` says so before switching, with the estimate: "Switching re-reads this thread's 84K tokens with the new adapter (about a minute)." *Not built yet (2026-10-20):* the hook passes the handler the thread id and its model only; the thread's token count would be one more argument in patch 0028.
- `/adapter` never installs, loads or downloads anything. An adapter that is not installed is answered with the `ling-admin adapter install <name>` line to run on the node.
- **Patch cost.** `/adapter` is one more entry registered the way `/cavemode` is (patch `0017`: the slash command, its dispatch arms, the handler in `ling-rs/src/adapter.rs`); the expected size is a few hundred bytes. As built, `0028-adapter-slash-command.patch` is 1,849 bytes: the variant, its description, two capability lists, the queue-drain list and two dispatch arms that print the handler's lines and send its model as `AppEvent::UpdateModel`. The list comes from the catalog the launcher wrote, with no request to the server. The patch cap that `test_the_patches_stay_small` enforces is raised only explicitly, by what the hook needs (MIGHTLING_CODEX §3). If the hook does not fit, `/model` and `ling --adapter` already give the whole behaviour without it.

### 11.2 The desktop app

The desktop app's composer already has a model chip that lists `model/list` and sends the chosen model with each `turn/start` (MIGHTLING_DESKTOP_PARITY §2.3, built 2026-10-16). With the catalog of §10.2, `model/list` returns the base and the adapters, so the **picker is that chip**:

- entries grouped as "Base" and "Adapters", each adapter with its manifest's display name and one-line description, third-party ones with a badge and the warning in the tooltip;
- the choice is the thread's, kept with the thread and sent with each `turn/start`, so two threads in two windows of the same project can use different adapters;
- switching on a long thread shows the re-prefill estimate, as `/adapter` does;
- Ask threads use the same chip.

`model/list` and `turn/start` are already in `ling-rs/web/policy.json`, so the bridge's policy needs no new method; a conformance vector is added for a `turn/start` naming an adapter id. `ling web` serves the same UI and needs nothing else.

---

## 12. Memory and host safety

**Size of one adapter.** From the served checkpoint's `config.json` (hidden 5,120, MLP 17,408, 64 layers, 24 query heads and 4 key-value heads of 256 for attention, the Gated DeltaNet's 16 key and 48 value heads of 128), a LoRA of rank *r* on every linear layer has about **6.8 million × r** parameters: the MLPs about 4.3 M × r, the GDN projections about 1.8 M × r, the attention projections about 0.7 M × r. In BF16 that is about **220 MB at rank 16**, 435 MB at rank 32 and 870 MB at rank 64; attention and MLP alone (§7.1) are about 5 M × r, 160 MB at rank 16. These are computed, not measured, and §15 replaces them with the measured sizes of the first adapters.

**What the arena check counts.** `start_server()` checks that the weights plus the drafters fit the arena and that the reserve stays outside it (INFERENCE §7). With adapters it also counts the GPU pool, `max_loras_per_batch × max_lora_rank`'s pool size, and the loaded cache, `max_loaded_loras ×` the largest installed adapter, each with its drafter adapter. At the starting values of §8.2 (rank ceiling 32, 8 loaded, 4 in the pool) that is about 3.5 GB loaded and about 1.7 GB of pool: well inside the 38.7 GB the served recipe leaves free (MODELS §2), and it is charged to the container's cgroup cap through `container_headroom_gb` rather than left to luck.

**Bounded by construction.** The loader never holds more than `max_loaded_loras`; a library of 100 on disk costs no memory. The PSI watchdog is unchanged, and an adapter load is a small read compared with the model load it guards.

---

## 13. Security

An adapter changes what the model writes, and `ling` runs what the model writes. A malicious adapter could make the model insert a backdoor, weaken a check, or follow a trigger phrase in a file; a careless one could make it worse at its job. The rules, each specified above:

| Rule | How it holds | § |
|---|---|---|
| **Signature check** | The release key under `mightling-adapter`; checked at download, at every server start and (by hash) at every load. Only a typed third-party confirmation waives it | 5.1, 5.4 |
| **Hash pins** | Every file by sha256 in the signed manifest; the manifest by sha256 in the release's catalogue; the adapter by digest to the served base and drafter | 4.2, 5.2, 6.1 |
| **No user-triggered runtime loading through the server** | Requests select by name from the installed set; the gate maps the name to a recorded path and loads it; `/load_lora_adapter` and `/unload_lora_adapter` are refused on the public port, and the engine's port takes a key only the gate has | 8.3 |
| **The audit lists installed adapters** | Every egress-audit record and report names the installed adapters, their trust and the adapter the traced session used | 14.2 |

Further:

- **safetensors only.** Pickle files are never fetched (`allow_patterns`), never loaded, and a manifest naming one is refused.
- **No code from the repository.** No `trust_remote_code` for adapters, and no file outside the manifest reaches the disk.
- **Nothing in the sandbox can install one.** `ling-admin adapter` runs on the host, needs a typed confirmation for anything unsigned, and the agent's `/adapter` only selects.
- **A node serves its adapters to the LAN** with the model, under the trusted-LAN premise (README, "Accepted by design"): any machine on the LAN that can send prompts can name an installed adapter. It cannot install or load one from a path.

---

## 14. Egress and the air gap

### 14.1 Nothing reaches the network unless asked

- `ling-admin adapter download` is the **only** step that reaches the network, and only when a person runs it, like `ling-admin model download`. Nothing downloads an adapter on its own: not `server start`, not `/adapter`, not the picker, not an update, and there is no update check for adapters (a new version arrives in a release's catalogue and is installed by `download`).
- `list`, `list --available` and `remove` need no network (§6.1).
- Using an adapter adds no destination: the session talks to the same model server on the same port. The gate's loader reads files on this host.
- `/airgapped on` is unaffected: the model server is the one thing it allows.

### 14.2 The audit

- **A clean audit with an adapter on** is part of each adapter's acceptance (§7.3) and of Phase 1's (§15): `ling-admin audit egress --adapter <name>` traces an `exec` session that uses it, with the verdict rules unchanged (loopback model server only, no DNS, no networked git).
- **Every record lists the installed adapters**, whatever the scenario: `adapters` in the JSON (name, version, repo, commit, `manifest_sha256`, trust, base match) and `adapter_used`, and a line in the report. A third-party adapter is printed with ⚠️. This is a listing, not a network finding: the verdict stays a statement about destinations.
- The flag is added to `ling-admin audit egress`'s parser, which rejects unknown arguments like the rest.

---

## 15. Phases and acceptance

| Phase | Builds | Done when (measured) |
|---|---|---|
| **0** | The probe (`research/lora-serving-probe`, under way); §8.4's table filled in on the pinned image | Each question in §8.4 answered with a measurement; the cost of `--enable-lora` alone and per distinct adapter recorded in INFERENCE; the decision to proceed with Phase 1 or wait for Phase 2 recorded here |
| **1a** | Manifest, signing (`mightling-adapter`), the catalogue, `ling-admin adapter list/download/remove`, the installed set; no serving | A test adapter signed with a session key installs, refuses a tampered file, a wrong base digest, a release-namespace signature and an unsigned manifest without `--third-party`; a real download of a published adapter from the organisation verifies |
| **1b** | The recipe's `lora` key, the gate's loader, id rewrite and model list, `model_key_for_served_id`'s strip, host-safety accounting | Base decode unchanged within noise with LoRA enabled; one adapter's output identical with speculation on and off at temperature 0; two concurrent `ling exec` sessions on two adapters and one on the base all complete; a ninth distinct adapter evicts the least recently used; `/load_lora_adapter` refused on the public port and without the key on the engine's |
| **1c** | The launcher's catalog entries and `--adapter`, `/adapter`, the desktop chip's grouping, the audit's `--adapter` and listing | A resumed thread keeps its adapter; a removed adapter falls back with the notice; the egress audit passes with an adapter on and lists it; the desktop app runs two threads on two adapters |
| **2** | The signing job (§5.1); first adapters: Rust, C++/CUDA, TypeScript, each with a drafter adapter, published, signed in CI and catalogued in a release; `node sync-model` carrying signed adapters (§6.4) | Each meets §7.3's bar on this machine (a clear gain on its language, Rust on SWE-bench Multilingual's Rust subset; at most 1 task lost on the Python control night; at most 5% tok/s lost with its drafter adapter), its numbers in its manifest; a synced node verifies and serves the same adapters, and skips a third-party one |
| **3** | LoRA in ling-engine (its spec), drafter adapters per request, GDN target modules | The same contract as Phase 1, the per-adapter cost at or under 0.5–1 ms per step, acceptance with drafter adapters within the margin of the base |

---

## 16. Tests

No test touches the real machine (AGENTS.md, `docs/dev/testing.md`): no real Hugging Face request, no real engine, no real gate (conftest's stubs), and signatures made with session keys from `ssh-keygen`, never the release key.

- **Manifest:** a valid one parses; an unknown schema, an unknown key, a missing field, a pickle file named, a branch instead of a commit are each refused.
- **Signature** (`ssh-keygen -Y` with session keys, as `tests/test_release_install.py` does): a signed manifest verifies; a changed byte, another key, the `mightling-release` namespace and a missing `.sig` are refused; the package's key file equals `ling-rs/release-signing.pub`.
- **Download** (with `ModelDownloader` replaced): catalogue name to pinned commit; `allow_patterns` is exactly the manifest's files; a file whose hash differs stops the install and leaves nothing recorded; `base mismatch` stops it before any adapter file is fetched; `--third-party` without the typed name installs nothing; every `adapter` subcommand rejects unknown arguments.
- **Base digest:** computed from a fixture snapshot; a changed config file changes it; checked against the served id, not the configuration.
- **Gate:** the model list appends installed adapters and only those whose base matches; `+name` becomes `:name` towards the engine and back in the response; an unknown adapter is a 404; the load endpoints are refused on the public port whatever the gate's state; the loader hashes before loading, evicts the least recently used past `max_loaded_loras`, and loads one adapter once under concurrent first requests; nothing in a request reaches the load call as a path.
- **Recipe:** the `lora` key becomes the SGLang flags; an adapter beyond the rank ceiling is refused at download; host safety counts the pool and the loaded cache.
- **Launcher** (`cargo test -p ling-launcher` in an export): one catalog entry per listed id; `--adapter` removed and turned into `--model`; the tier order; resume reads the thread's file; an unlisted adapter falls back with the notice.
- **Desktop:** the chip groups the base and adapters and marks a third-party one; a policy vector for `turn/start` with an adapter id.
- **Signing job:** `bash -n` on its `run:` blocks and a dry run with a session key against a fixture manifest; a hash that differs from the repository's metadata, a wrong base digest and a signature that does not verify against `release-signing.pub` each stop it before upload.
- **Sync:** `node sync-model` (with the SSH call replaced) sends signed adapters and never a third-party one; the receiving side refuses a tampered file and lists a base it does not serve as `base mismatch`.
- **Egress audit:** records list the installed adapters and the one used; `--adapter` traces a session on it.

---

## 17. Open questions

Questions 1–4 wait on the LoRA serving probe (`research/lora-serving-probe`, running on 2026-10-19). Questions 5, 8 and 11, and 7 for Rust, were answered by the owner on 2026-10-19 (§2.1) and are kept here, struck through, so their numbers stay stable.

1. **`/v1/responses` with an adapter.** SGLang documents `<base>:<adapter>` for chat and completions only; `ling` speaks the Responses API alone. The probe answers it; if it fails, is the gate carrying the adapter some other way acceptable, or should Phase 1 wait for ling-engine?
2. **DFlash2 with LoRA, and drafter adapters under SGLang** (§8.4). If the drafter cannot take a per-request LoRA, is Phase 1 acceptable with the base drafter, given the speed it loses (measured by the probe)?
3. **Target modules on the hybrid layers.** Does SGLang's LoRA cover the Gated DeltaNet projections (`in_proj_*`, `out_proj`), or only attention and the MLPs? The first adapters assume the latter.
4. **The engine's loopback port.** Does `--api-key` on the pinned SGLang cover `/load_lora_adapter` and `/unload_lora_adapter`? If not: a unix socket, or a network namespace shared with the gate?
5. ~~**Who signs, and where.**~~ **Decided 2026-10-19:** a CI job in the `release` environment, with the release key, under `mightling-adapter`, automatic like releases (§5.1).
6. **The verifier in `ling-admin`.** `ssh-keygen -Y verify`, as install.sh does (proposed), or a `ling adapter verify` subcommand on `release_signature.rs`, which would follow key transitions exactly as `ling update` does but makes `ling-admin` depend on the installed `ling`?
7. **Which evaluation for a language adapter.** **Rust decided 2026-10-19:** the Rust subset of SWE-bench Multilingual (§7.3). Still open: the sets for C++/CUDA and TypeScript (SWE-bench Multilingual's other subsets, Multi-SWE-bench, or tasks of our own), inside the benchmark decision of 2026-10-09 (keep SWE-bench Verified; investigate multilingual sets only).
8. ~~**The publishing bar.**~~ **Decided 2026-10-19:** a clear gain on its language, at most 1 task lost on a Python control night, at most 5% tok/s lost with its drafter adapter (§7.3).
9. **The organisation's slug on Hugging Face.** Not named anywhere in the tree; `dreamference` is assumed in the examples' shape (`<org>/mightling-<name>`).
10. **Rank and modules of the first adapters**, and the recipe's ceilings (§8.2): fixed ceilings mean a later, larger adapter needs a restart.
11. ~~**Second nodes.**~~ **Decided 2026-10-19:** `node sync-model` copies installed signed adapters to paired nodes, and each verifies on its own (§6.4).

---

## 18. Sources (read 2026-10-19)

- [SGLang: LoRA Serving](https://lmsysorg.mintlify.app/docs/advanced_features/lora): the server arguments (`--enable-lora`, `--lora-paths`, `--max-loras-per-batch`, `--max-loaded-loras`, `--lora-eviction-policy`, `--max-lora-rank`, `--lora-target-modules`, `--lora-backend`), `/load_lora_adapter` and `/unload_lora_adapter`, pinning (at most `max-loras-per-batch − 1`, one slot for the base), and the `base-model:adapter-name` syntax for the chat and completions APIs. The page describes the current release, not the pinned v0.5.21.
- S-LoRA and Punica, which the SGLang page names as the basis of its batched multi-adapter serving, and which §9 takes as the pattern for ling-engine.
- In this repository: [MODELS](./DREAMFERENCE_MODELS.md) §1–§3, [INFERENCE](./DREAMFERENCE_INFERENCE.md) §2, §3, §5.3, §7, [RELEASE_SIGNING](./DREAMFERENCE_RELEASE_SIGNING.md) §2–§5, [MIGHTLING_CODEX](./DREAMFERENCE_MIGHTLING_CODEX.md) §3–§4, [MIGHTLING_DESKTOP_PARITY](./DREAMFERENCE_MIGHTLING_DESKTOP_PARITY.md) §1.5, §2.3, [MIGHTLING_EGRESS](./DREAMFERENCE_MIGHTLING_EGRESS.md) §3, §10, [SELF_SPEEDING](./DREAMFERENCE_SELF_SPEEDING.md) §1, §4–§5, `docs/dev/swe-bench.md` (the model gate), `dreamference/vllm_server/model_gate_service.py`, and the served checkpoint's `config.json` (§12's sizes).
