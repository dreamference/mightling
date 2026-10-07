# Architecture

Mightling is a set of local services around one model server. Everything below runs on the GB10.

Mightling is made by **Dreamference**, and its Python side is the `dreamference` package. That is why
settings and paths are named `dreamference.toml`, `DREAMFERENCE_VLLM_HOST` and
`~/.local/share/dreamference/`.

```mermaid
flowchart LR
    T["mling<br/>terminal agent"]
    B["Browser"]
    D["mling-app<br/>desktop window"]
    O["Web chat (Onyx Lite)<br/>:3000"]
    H["Helpers<br/>SearXNG · Whisper<br/>Gmail · Image search"]
    subgraph models["Model server"]
        V["vLLM, main model<br/>:8000"]
    end
    T --> V
    T -- "search, fetch, Gmail" --> H
    B --> O
    D --> O
    O --> V
    O --> H
```

| Part | What it is | Where it runs |
|---|---|---|
| Model server | SGLang (or vLLM) serving the main model through the standard `/v1` chat API | Docker container, port 8000 |
| `mling` | The terminal agent | A native binary, linked from `~/.local/bin` |
| Web chat | Onyx Lite: web server, API server, PostgreSQL | Docker containers, port 3000 |
| SearXNG | Metasearch: queries public search engines for web search | Docker container, reachable only from this machine |
| Whisper | Speech-to-text for the microphone button, on the CPU | Docker container |
| Gmail service | Read-only IMAP search over connected Gmail accounts | Docker container, reachable only from this machine |
| Image search | Finds and serves images for the web chat | Docker container |
| `mling-app` | A Tauri window showing the web chat | Native app |
| `mling-admin` | Manages all of the above | Python CLI |

The web chat reaches the model server through Docker's bridge network, because the model server
shares the host's network while Onyx runs on Docker's default bridge. `configure` sets that up.

## How `mling` is built

```mermaid
flowchart LR
    C[Upstream source<br/>pinned release] --> E[exported copy]
    P[Mightling patches] --> E
    L[Mightling launcher<br/>Rust crate] --> E
    E --> X[cargo build] --> Y[mling +<br/>codex-code-mode-host]
```

- **Pinned source.** The source Mightling was forked from sits in the repository pinned to a stable
  release, and is never edited in place.
- **Built from a copy.** `mling-admin codex build` exports that release to a scratch directory,
  adds Mightling's launcher crate, applies a short series of patches, and compiles.
  - The patches are small: the product name in the interface, and one-line hooks into the launcher.
  - The rest of Mightling's changes live in the launcher: finding the model, the prompt, and the
    removed cloud-only commands.
- **Easy to upgrade.** Moving to a newer upstream release is a change of pin plus refreshing
  whichever patches stop applying.
- **Two programs.** The build produces `mling` and `codex-code-mode-host`, the helper that runs
  the agent's Code Mode JavaScript in its own V8 engine. They are installed side by side.
- **Rebuilt only when needed.** A build is recorded against the exact source, patches and launcher
  it came from, so an unchanged tree is not rebuilt.

## Keeping the host alive

On the GB10 the GPU and the operating system share one pool of memory. A model load that
overshoots doesn't just fail: it can push the whole machine into a stall. Mightling guards against
that in two layers:

1. **Before a load.** `mling-admin server start` inspects swap, kernel memory settings and whether
   an out-of-memory guard (`earlyoom` or `systemd-oomd`) is present and configured. If loading
   could freeze the host, it stops and explains what to change.
2. **During a load.** A watchdog samples the kernel's memory-pressure signal (PSI) every
   second. If pressure spikes, or stays high, it kills the model container before the host stalls,
   through the fastest route the host can still take.
