# Get started

!!! info "Source access"
    Puffin's source repository is not public yet, and no packaged release has been published.
    These steps are for people with access to the repository.

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
git clone --recurse-submodules <repository-url> puffin
cd puffin
python3 -m venv .venv
.venv/bin/pip install -e .
```

This installs `puffin-admin`. Put the environment on your `PATH`, for example in `~/.bashrc`:

```bash
export PATH="$HOME/puffin/.venv/bin:$PATH"
```

!!! warning "`puffin-admin` has to be on your PATH"
    The agent searches the web, reads pages and reads Gmail by running `puffin-admin search`,
    `fetch` and `gmail` as shell commands. If `puffin-admin` is not on the `PATH` the agent
    inherits, those commands fail and the agent concludes it has no web access.

## 2. Start the model server

```bash
puffin-admin server start
```

The first start downloads the model weights and the server image, then loads the model. Expect
several minutes. Later starts only load the model. A small second model (a code-diffusion model)
starts beside the main one; see [Models](models.md).

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

- **First run with an empty `~/.codex`.** When `puffin` starts with no existing configuration
  directory, it can open on a "Sign in with ChatGPT" screen instead of the message box. Puffin does
  not use an OpenAI account; this screen is inherited from Codex and is being fixed.
- **`puffin update` finds nothing yet.** It installs the latest published Puffin release, and none
  has been published. Until then, update by pulling the repository and running
  `puffin-admin codex build`.
