# Clients, nodes and jobs

Developer notes behind the node line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_NODE.md`; §18 says what is built.

Mightling is a client and a node, and `ling` finds the node.

## Node or client

- A **node** is a machine with `~/.config/dreamference/node-id` (written by `server start`, `codex build`, `desktop build` and `node enable`): its `ling` uses loopback and never browses.
- Anything else is a **client**: the launcher (`ling-rs/src/node.rs`) resolves the model server through tiers:
  1. `DREAMFERENCE_VLLM_HOST`/`vllm_host`;
  2. `MIGHTLING_NODE`;
  3. node → loopback;
  4. the node remembered in `$CODEX_HOME/node.json`, found again **by id**;
  5. an mDNS browse of `_mightling-node._tcp` with the `mdns-sd` crate.

  It never adopts a node other than the remembered one without asking. `ling node list|use|forget` is the same logic from a shell.

Only the launcher and `ling-app` browse; `ling-search` reads `node.json` through `ling-rs/node-locator/`, a std-only crate with **byte-identical copies** in `ling-web-rs/src/node_locator.rs` (a test compares them, as for the air-gap resolver; the desktop app's `desktop/electron/src/node_locator.ts` is a port with the crate's tests).

## Advertising a node

On the node, `ling-admin node enable` installs an Avahi service file (root once; `server start|stop` rewrite its `state` afterwards as the user) and publishes the web UI and SearXNG on every interface, which **reverses the two loopback fixes of 2026-09-29 on an advertised node only**. The advert's `web` record names `ling web` on 3100, which `node enable` starts with `--lan` (a device is served only once paired; `--no-web` and `disable` put it back on loopback); Onyx's port 3000 is still published beside it until Onyx is retired. Whether a node is advertised lives in `~/.config/dreamference/node-advertise.json`, not in `dreamference.toml`, because that file is resolved from the working directory.

`ling-app` on a client runs its Ask window on its own `ling web`, against the node's model server; the TCP forwarder to the node's port 3000 was removed on 2026-10-08 (ASK §17).

## Pairing and jobs, over SSH

**Managing and using a second node goes over SSH, never an open port** (`node/node_pairing.py`, `node_serve.py`, `node_remote.py`, `node_job.py`, `node_job_sender.py`). `node add` has the other node authorise a key for one forced command, `ling-admin node serve-job`, which refuses anything but its operations (info, status, start, stop, `set-model <matrix key>`, unpair, the two git services for `jobs/<name>.git`, and the job requests) and carries each out with that node's own `ling-admin`.

A job (`node run <node> -- <command>`) is a systemd user unit with a mandatory memory cap and time limit, run inside bubblewrap with the home folder and `/run` hidden and no GPU. Its changes come back as `job/<id>`; `--setup` environments are bound read-only, `--out` comes back as files, `--bind` only under the node's `[node] bindable`; finished jobs are pruned a day after the fetch.

- A paired node serving the same model is an extra model server (a *lane*) for Night Shift and SWE-bench (`[night|swe_bench] nodes`; the tasks still run here, SWE-bench reaches it through a relay on its network's gateway).
- `node sync-model` copies a model's cache over the pairing.
- `/night add --on <node>` hands a task to that node's own runner (`night_shift/night_shift_remote.py`), where it runs in the job sandbox with a `CODEX_HOME` of its own; spec §18.8.
- `/node` in the TUI (patch `0025`) runs `ling-admin node …` in the terminal; see [codex-build.md](codex-build.md).

## Keep when touching this

- Every unattended caller of `ling` (Night Shift, the egress audit, SWE-bench, the Codex test runner) names the model server in `DREAMFERENCE_VLLM_HOST`, because a browse is a multicast DNS query and the audit counts one as a failure.
- Tests never write `/etc/avahi` or run sudo (conftest points `NodeServiceFile.service_path` at scratch).
- A job sent over SSH does not inherit a permissive AppArmor profile; see [host-safety.md](host-safety.md#bubblewrap-needs-an-apparmor-profile-and-this-machine-now-has-one).
