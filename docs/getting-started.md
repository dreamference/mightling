# Get started

!!! tip "The quickest way: install a release"
    Every [release](https://github.com/dreamference/mightling/releases) carries `install.sh`, which
    installs the prebuilt binaries and, on a GB10, `mling-admin` and the host settings:

    ```bash
    curl -fsSLO https://github.com/dreamference/mightling/releases/latest/download/install.sh
    bash install.sh
    ```

    Then continue at [step 2](#2-start-the-model-server). The steps below install from a checkout,
    which is what you want for changing Mightling itself.

## What you need

- **Any NVIDIA GB10 machine**: the DGX Spark, or the Acer Veriton GN100, ASUS Ascent GX10, Dell
  Pro Max with GB10, Gigabyte AI TOP ATOM, HP ZGX Nano, Lenovo ThinkStation PGX or MSI EdgeXpert.
  All have the same chip and 128 GB of unified memory, and ship DGX OS 7 (Ubuntu 24.04 underneath);
  Mightling was developed on the ASUS. A 1 TB drive is enough. The RTX Spark laptops run Windows and
  cannot be a Mightling node.
- **Docker with the NVIDIA Container Toolkit.** The model server runs in a container. DGX OS
  ships both; on a machine reinstalled with plain Ubuntu, `mling-admin host check` lists them,
  with bubblewrap and Avahi, among what to install.
- **Python 3** (Mightling is developed on 3.12) and **Git**.
- **Disk space for model weights.** The default model's weights are tens of gigabytes, downloaded
  from Hugging Face the first time the server starts.
- **Swap and an out-of-memory guard.** `mling-admin server start` checks the host before loading a
  model and explains any setting it wants changed (swap, `sysctl` values, `earlyoom` or
  `systemd-oomd`). See [Architecture](architecture.md#keeping-the-host-alive).

## 1. Install

```bash
git clone --recurse-submodules https://github.com/dreamference/mightling.git mightling
cd mightling
python3 -m venv .venv
.venv/bin/pip install -e .
```

This installs `mling-admin`. Put the environment on your `PATH`, for example in `~/.bashrc`:

```bash
export PATH="$HOME/mling/.venv/bin:$PATH"
```

!!! warning "`mling-admin`, `mling-search` and `mling-fetch` have to be on your PATH"
    The agent searches the web and reads pages by running `mling-search` and `mling-fetch`, and
    reads Gmail by running `mling-admin gmail`, as shell commands. If they are not on the `PATH`
    the agent inherits, those commands fail and the agent concludes it has no web access.
    `mling-admin codex build` links all three into `~/.local/bin`.

## 2. Start the model server

```bash
mling-admin server start
```

The first start downloads the model weights, then loads the model. Expect several minutes. Later
starts only load the model.

Check that it is answering:

```bash
mling-admin endpoints
```

## 3. Build the terminal agent

```bash
mling-admin codex build
```

This compiles `mling` from the source in the repository, installs it
under `~/.local/share/dreamference/mightling/`, and links `~/.local/bin/mling` to it. It installs a
Rust toolchain if you have none. The first build compiles several hundred dependencies and takes a
while; rebuilds after small changes take a few minutes.

## 4. Run it

```bash
cd ~/my-project
mling
```

See [Terminal agent](mling.md) for what it can do.

## 5. Optional: the web chat and desktop app

```bash
mling-admin chat start        # deploy the web chat (PostgreSQL, API and web servers)
mling-admin chat configure --email you@example.com --password '<a strong password>'
```

`configure` connects the chat to the local model, switches its features on and, if no account
exists yet, registers the one you give as the administrator.

!!! warning "Always pass your own credentials"
    Run without `--email` and `--password`, `configure` creates the administrator account with a
    built-in default e-mail and password. Give your own, or change the password straight away.

Open <http://localhost:3000> and sign in. See [Web chat](web-chat.md).

For a window of its own:

```bash
mling-admin desktop install     # build tools for the desktop app (asks for sudo once)
mling-admin desktop run
```

After that, `mling app` also opens it. See [Desktop app](desktop.md).

## Known issues

- **`mling update` finds nothing yet.** It installs the latest published Mightling release, and none
  has been published. Until then, update by pulling the repository and running
  `mling-admin codex build`.
