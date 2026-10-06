# Get started

!!! tip "The quickest way: install a release"
    Every [release](https://github.com/dgxcoder/dgxcoder/releases) carries `install.sh`, which
    installs the prebuilt binaries and, on a GB10, `puffin-admin` and the host settings:

    ```bash
    curl -fsSLO https://github.com/dgxcoder/dgxcoder/releases/latest/download/install.sh
    bash install.sh
    ```

    Then continue at [step 2](#2-start-the-model-server). The steps below install from a checkout,
    which is what you want for changing Puffin itself.

## What you need

- **An NVIDIA GB10 workstation** with 128 GB of unified memory, running a 64-bit ARM Linux
  (Ubuntu). The default model needs about 72 GB of memory while it runs.
- **Docker with the NVIDIA Container Toolkit.** The model server runs in a container.
- **Python 3** (Puffin is developed on 3.12) and **Git**.
- **Disk space for model weights.** The default model's weights are tens of gigabytes, downloaded
  from Hugging Face the first time the server starts.
- **Swap and an out-of-memory guard.** `puffin-admin server start` checks the host before loading a
  model and explains any setting it wants changed (swap, `sysctl` values, `earlyoom` or
  `systemd-oomd`). See [Architecture](architecture.md#keeping-the-host-alive).

## 1. Install

```bash
git clone --recurse-submodules https://github.com/dgxcoder/dgxcoder.git puffin
cd puffin
python3 -m venv .venv
.venv/bin/pip install -e .
```

This installs `puffin-admin`. Put the environment on your `PATH`, for example in `~/.bashrc`:

```bash
export PATH="$HOME/puffin/.venv/bin:$PATH"
```

!!! warning "`puffin-admin`, `puffin-search` and `puffin-fetch` have to be on your PATH"
    The agent searches the web and reads pages by running `puffin-search` and `puffin-fetch`, and
    reads Gmail by running `puffin-admin gmail`, as shell commands. If they are not on the `PATH`
    the agent inherits, those commands fail and the agent concludes it has no web access.
    `puffin-admin codex build` links all three into `~/.local/bin`.

## 2. Start the model server

```bash
puffin-admin server start
```

The first start downloads the model weights, then loads the model. Expect several minutes. Later
starts only load the model.

!!! note "The 122B fallbacks need their own server image"
    The default model, Qwen3.8-27B, runs on a published SGLang image that `server start` pulls by
    itself. The Qwen 3.5 122B fallbacks run on a custom vLLM image,
    `dreamference-vllm-dflash:0.23.0-aeon-dense5`, built in stages from `Dockerfile.dflash` and
    `Dockerfile.dense` in the repository (their headers describe how). `server start` cannot build
    that image yet, so build it before choosing one of those models.

Check that it is answering:

```bash
puffin-admin endpoints
```

## 3. Build the terminal agent

```bash
puffin-admin codex build
```

This compiles `puffin` from the Codex source in the repository plus Puffin's changes, installs it
under `~/.local/share/dreamference/puffin/`, and links `~/.local/bin/puffin` to it. It installs a
Rust toolchain if you have none. The first build compiles several hundred dependencies and takes a
while; rebuilds after small changes take a few minutes.

## 4. Run it

```bash
cd ~/my-project
puffin
```

See [Terminal agent](puffin.md) for what it can do.

## 5. Optional: the web chat and desktop app

```bash
puffin-admin puffin start        # deploy the web chat (PostgreSQL, API and web servers)
puffin-admin puffin configure --email you@example.com --password '<a strong password>'
```

`configure` connects the chat to the local model, switches its features on and, if no account
exists yet, registers the one you give as the administrator.

!!! warning "Always pass your own credentials"
    Run without `--email` and `--password`, `configure` creates the administrator account with a
    built-in default e-mail and password. Give your own, or change the password straight away.

Open <http://localhost:3000> and sign in. See [Web chat](web-chat.md).

For a window of its own:

```bash
puffin-admin desktop install     # build tools for the desktop app (asks for sudo once)
puffin-admin desktop run
```

After that, `puffin app` also opens it. See [Desktop app](desktop.md).

## Known issues

- **`puffin update` finds nothing yet.** It installs the latest published Puffin release, and none
  has been published. Until then, update by pulling the repository and running
  `puffin-admin codex build`.
