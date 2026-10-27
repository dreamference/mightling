# Get started

!!! tip "The quickest way: install a release"
    Every [release](https://github.com/dreamference/mightling/releases) carries `install.sh`, which
    installs the prebuilt binaries and, on a GB10, everything else, unattended: `ling-admin`, the
    host settings, advertising on your network, and the model, downloaded and started. It asks for
    your sudo password once, at the start, and whether to install pending system updates:

    ```bash
    curl -fsSLO https://github.com/dreamference/mightling/releases/latest/download/install.sh
    bash install.sh
    ```

    When its summary says every step is done, the model server is running. The steps below install
    from a checkout, which is what you want for changing Mightling itself.

## What you need

- **Any NVIDIA GB10 machine**: the DGX Spark, or the Acer Veriton GN100, ASUS Ascent GX10, Dell
  Pro Max with GB10, Gigabyte AI TOP ATOM, HP ZGX Nano, Lenovo ThinkStation PGX or MSI EdgeXpert.
  All have the same chip and 128 GB of unified memory, and ship DGX OS 7 (Ubuntu 24.04 underneath);
  Mightling was developed on the ASUS. A 1 TB drive is enough. The RTX Spark laptops run Windows and
  cannot be a Mightling node.
- **Docker with the NVIDIA Container Toolkit.** The model server runs in a container. DGX OS
  ships both; on a machine reinstalled with plain Ubuntu, `ling-admin host check` lists them,
  with bubblewrap and Avahi, among what to install.
- **Python 3** (Mightling is developed on 3.12) and **Git**.
- **Disk space for model weights.** The default model's weights are tens of gigabytes, downloaded
  from Hugging Face the first time the server starts.
- **Swap and an out-of-memory guard.** `ling-admin server start` checks the host before loading a
  model and explains any setting it wants changed (swap, `sysctl` values, `earlyoom` or
  `systemd-oomd`). See [Architecture](architecture.md#keeping-the-host-alive).

## 1. Install

```bash
git clone --recurse-submodules https://github.com/dreamference/mightling.git mightling
cd mightling
python3 -m venv .venv
.venv/bin/pip install -e .
```

This installs `ling-admin`. Put the environment on your `PATH`, for example in `~/.bashrc`:

```bash
export PATH="$HOME/ling/.venv/bin:$PATH"
```

!!! warning "`ling-admin`, `ling-search` and `ling-fetch` have to be on your PATH"
    The agent searches the web and reads pages by running `ling-search` and `ling-fetch`, and
    reads Gmail by running `ling-admin gmail`, as shell commands. If they are not on the `PATH`
    the agent inherits, those commands fail and the agent concludes it has no web access.
    `ling-admin codex build` links all three into `~/.local/bin`.

## 2. Start the model server

```bash
ling-admin server start
```

The first start downloads the model weights, then loads the model. Expect several minutes. Later
starts only load the model.

Check that it is answering:

```bash
ling-admin endpoints
```

## 3. Build the terminal agent

```bash
ling-admin codex build
```

This compiles `ling` from the source in the repository, installs it
under `~/.local/share/dreamference/mightling/`, and links `~/.local/bin/ling` to it. It installs a
Rust toolchain if you have none. The first build compiles several hundred dependencies and takes a
while; rebuilds after small changes take a few minutes.

## 4. Run it

```bash
cd ~/my-project
ling
```

See [Terminal agent](ling.md) for what it can do.

## 5. Optional: the web UI and desktop app

```bash
ling web start               # Ask and Work in a browser, on this machine only
ling web open                # sign this machine's browser in (a link that works once)
ling-admin images start      # optional: image search in Ask
ling-admin voice start       # optional: the microphone button (speech-to-text on the CPU)
```

See [Web UI](web.md). An install that had the retired Onyx web chat is offered its removal once;
`ling-admin chat remove` does it at any time.

For a window of its own:

```bash
ling-admin desktop install     # build tools for the desktop app (asks for sudo once)
ling-admin desktop run
```

After that, `ling app` also opens it. See [Desktop app](desktop.md).

## 6. Optional: your laptop as a client

Your other computers can use the GB10's model. On the GB10, let them find it:

```bash
ling-admin node enable          # publishes this machine to your local network (asks for sudo once)
```

!!! warning "A GB10 on Wi-Fi: turn power saving off"
    Clients find the node by multicast DNS, and Wi-Fi power saving, which NetworkManager turns on
    by default, loses most of it: with it on, up to ~80% of the queries to a GB10 went unanswered
    and laptops could not find it. `node enable` and `ling-admin host check` warn when it is on,
    with the commands that fix it; `ling-admin host setup` (or `sudo ling-admin node prepare`) turns
    it off for good, with no reconnect. A Linux laptop on Wi-Fi loses the answers the same way: when
    its `ling` finds no node and its own power saving is on, it says so, with the commands.
    On Ethernet there is nothing to do.

Then on a Linux laptop (Intel/AMD or Arm) or a Mac (Apple silicon or Intel):

```bash
curl -fsSL https://github.com/dreamference/mightling/releases/latest/download/install.sh | bash
cd ~/my-project && ling
```

The script installs the client: `ling`, `ling-search`, `ling-fetch` and `ling-code`, and on a
Mac, from 1.7.2, the [desktop app](desktop.md) as well (`--no-app` leaves it out). On Windows 11,
the same in PowerShell (a preview; Smart App Control must be off):

```powershell
irm https://github.com/dreamference/mightling/releases/latest/download/install.ps1 | iex
```

A Mac is never installed as a node. `ling` finds the GB10 on the network by itself and asks before
it uses a node it has not used before; `ling node list` shows what it found. To name one yourself:
`MIGHTLING_NODE=<host> ling`.

On Windows, `install.ps1` installs the client into `%LOCALAPPDATA%\Programs\Mightling\bin` and
the desktop app (`-NoApp` leaves it out), puts that folder on your `Path`, and asks once for
Administrator rights: for the sandbox the agent's commands run in, the firewall rules that let
`ling` and the app hear nodes announce themselves, and an egress audit. Over SSH there is no
desktop for that prompt; the script says so and prints the command an administrator runs instead.

To remove the client, on any of them: `ling uninstall` (it asks first; `--purge` also removes
`~/.mightling`, your sessions and settings). The desktop app stays: remove it as any other app.

!!! note "`/airgapped` on a Mac"
    macOS's own sandbox enforces `/airgapped on`, but it is set once, when `ling` starts: use
    `ling airgapped default on` (or `DREAMFERENCE_MIGHTLING_AIRGAPPED=on ling`) and restart. Typed
    inside a running session, `/airgapped on` and `off` say so and change nothing.

## Known issues

- **`ling update` finds nothing yet.** It installs the latest published Mightling release, and none
  has been published. Until then, update by pulling the repository and running
  `ling-admin codex build`.
