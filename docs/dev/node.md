# Clients, nodes and jobs

Developer notes behind the node line in `AGENTS.md`. Spec: `specs/DREAMFERENCE_MIGHTLING_NODE.md`; §18 says what is built.

Mightling is a client and a node, and `ling` finds the node.

## Node or client

- A **node** is a machine with `~/.config/dreamference/node-id` (written by `server start`, `codex build`, every `desktop` command, `node enable` and `node id`): its `ling` uses loopback and never browses.
- Anything else is a **client**: the launcher (`ling-rs/src/node.rs`) resolves the model server through tiers:
  1. `DREAMFERENCE_VLLM_HOST`/`vllm_host`;
  2. `MIGHTLING_NODE`;
  3. node → loopback;
  4. the node remembered in `$CODEX_HOME/node.json`, found again **by id**;
  5. an mDNS browse of `_mightling-node._tcp` with the `mdns-sd` crate.

  It never adopts a node other than the remembered one without asking. `ling node list|use|forget` is the same logic from a shell; `ling node list --json` is what the desktop app's Settings › Node reads (names, never an address; [desktop.md](desktop.md)).

  **A browse that wants a node waits for it.** With a node remembered (or named by `MIGHTLING_NODE` or `ling node use`), another node's answer does not start the 400 ms settle; the browse listens until the wanted node answers or a 4.5 s deadline, one query more than the 2 s of a browse for any node (`after_answer`, `browse_timeout`; with 2 s the remembered node still missed five of ten browses on that box and was reached only at its last address). With nothing wanted, only an answer from a node that serves (`main=1`, not `state=stopped`) starts it. On Wi-Fi with power saving the stopped node-b often answered first, and the remembered gx10-1a2b was declared gone (2026-10-19: 1.6.0 failed 2 of 4 `ling exec` runs on the owner's desktop, the desktop app's agent server 4 of 7 launches). **A stopped node is no choice:** of several coding nodes with none remembered, the only one not advertising `state=stopped` is used; a stopped node used because it is the only one is not remembered; and a remembered node that does not answer while only stopped nodes do is tried at its last address. The owner keeps node-b paired with its model server usually stopped, so clients must cope with that on their own.

  When a browse misses (tier 5 finds nothing, the remembered node of tier 4 does not answer, or `MIGHTLING_NODE` names a node that is not there; `browse_missed`), a Linux client reads its own Wi-Fi the way `WifiPowerSave` does (below) and, if a connected Wi-Fi connection saves power, adds one line naming it with the commands that turn it off (`ling_node_locator::wifi_power_save_hint`). It is a hint: the error or the fallback to the last address is unchanged, `ling` runs no `sudo`, and on another system, without NetworkManager or on Ethernet it says nothing. `ling node list` with no answer and `ling node use` with a name nothing answered to print it too.

Only the launcher browses (`ling-app` through the bundled `ling` its `ling app-server` runs); `ling-search` reads `node.json` through `ling-rs/node-locator/`, a std-only crate with **byte-identical copies** in `ling-web-rs/src/node_locator.rs` (a test compares them, as for the air-gap resolver). The desktop app has no copy of its own: its TypeScript port, `desktop/electron/src/node_locator.ts`, went with the app's own node discovery on 2026-10-08, and `tests/test_node.py` checks it stays gone.

## Advertising a node

On the node, `ling-admin node enable` installs an Avahi service file (root once; `server start|stop` rewrite its `state` afterwards as the user) and publishes the web UI and SearXNG on every interface, which **reverses the two loopback fixes of 2026-09-29 on an advertised node only**. The advert's `web` record names `ling web` on 3100, which `node enable` starts with `--lan` (a device is served only once paired; `--no-web` and `disable` put it back on loopback); Onyx's port 3000, published beside it until then, went with Onyx's retirement (ASK §10). Whether a node is advertised lives in `~/.config/dreamference/node-advertise.json`, not in `dreamference.toml`, because that file is resolved from the working directory.

**Wi-Fi power saving loses the advert.** Both GB10 machines here are on Wi-Fi only (MediaTek MT7925), where NetworkManager turns power saving on for every connection by default (Ubuntu's `/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf` sets `wifi.powersave = 3`, and a connection's own `802-11-wireless.powersave` is `default`). A sleeping radio gets multicast only at DTIM beacons, and on 2026-10-11/12 up to ~80% of the mDNS queries sent to a node went unanswered: `node-b.local` did not resolve and clients could not find their node. `WifiPowerSave` (`vllm_server/wifi_power_save.py`) reads each connected Wi-Fi interface (`nmcli -g 802-11-wireless.powersave connection show <connection>` for the setting, `iw dev <if> get power_save` for the radio); `ling-admin host check` reports one whose setting is not `disable` or whose radio still says `on`, `host setup` fixes it with `nmcli connection modify <connection> 802-11-wireless.powersave 2` (kept across reconnects) and `iw dev <if> set power_save off` (now, with no reconnect that would drop an SSH session), and `node enable` prints the same warning without stopping. Ethernet only, or no NetworkManager: nothing is said. `node prepare` applies the same step, the one NetworkManager setting it changes (decided 2026-10-12): `NodePrepare.allowed` lets those two commands through whole (`WifiPowerSave.is_fix_command`) and still refuses every other `nmcli` or `iw` command.

`ling-app` on a client runs Ask and Work in its windows on its own app-server, against the node's model server; the TCP forwarder to the node's port 3000 was removed on 2026-10-08 (ASK §17).

`node enable` also sets up the node's forge (Forgejo; `--no-forge` skips it): the first node is the forge of record, the others mirrors ([forge.md](forge.md)).

## Pairing and jobs, over SSH

**Managing and using a second node goes over SSH, never an open port** (`node/node_pairing.py`, `node_serve.py`, `node_remote.py`, `node_job.py`, `node_job_sender.py`). `node add` has the other node authorise a key for one forced command, `ling-admin node serve-job`, which refuses anything but its operations (info, status, start, stop, `set-model <matrix key>`, unpair, `model-receive` and `adapter-receive` for `sync-model`, the two git services for `jobs/<name>.git`, the job requests, and the Night Shift, forge and smart-plug ones, `night-*`, `forge-*` and `power-*`) and carries each out with that node's own `ling-admin`.

**A paired node's address is never trusted from its record.** Every connection (`NodePairing.ssh_command`, the lanes, the job repositories' URLs, `sync-model`) goes through `NodePairing.resolve`: a browse looks for the node's id and the address in that answer is used; the record remembers it for display only. A browse that does not show the node (nothing answering, or other nodes answering and this one not) is repeated for up to a minute (`resolve_wait_s`), then the node is reported off the network and its old address, which may be another machine's by now, is never tried (spec §6.1, tier 4; the user's rule of 2026-10-10: never use an address, always resolve). An answer is reused by the same process for thirty seconds, so a lane probe and the ssh it then runs browse once; the suite sets the budget to zero (conftest `_no_browse_wait`). The night of 2026-10-09 lost its second lane because the lanes used the recorded address directly after node-b's DHCP lease had moved; `--address` on `sync-model` is the one deliberate exception (`address_fixed`).

A job (`node run <node> -- <command>`) is a systemd user unit with a mandatory memory cap and time limit, run inside bubblewrap with the home folder and `/run` hidden and no GPU. Its changes come back as `job/<id>`; `--setup` environments are bound read-only, `--out` comes back as files, `--bind` only under the node's `[node] bindable`; finished jobs are pruned a day after the fetch.

- A paired node serving the same model is an extra model server (a *lane*) for Night Shift and SWE-bench (`[night|swe_bench] nodes`; the tasks still run here, SWE-bench reaches it through a relay on its network's gateway).
- `node sync-model` copies a model's cache over the pairing.
- `/night add --on <node>` hands a task to that node's own runner (`night_shift/night_shift_remote.py`), where it runs in the job sandbox with a `CODEX_HOME` of its own; spec §18.8.
- `/node` in the TUI (patch `0025`) runs `ling-admin node …` in the terminal; see [codex-build.md](codex-build.md).

## Provisioning a node with a role

`ling-admin node provision <node> --user <user> --role night|engine [--dry-run]` (`node/node_role_provisioner.py`) is `node provision` (FLEET §7.1: install, `node prepare`, advertise, model copy, pairing, start and verify) plus what the last node needed by hand. It is the one run that adds persistent access, which FLEET's decisions 3 and 6 rule out for a plain `node provision` (no `NOPASSWD`, no full-access key, never `sshd`), so it happens only with `--role`:

1. the main node's own key (the one `ssh -G <node>` names, else `~/.ssh/id_ed25519`) is appended to `~/.ssh/authorized_keys`; the run's sessions offer it with `IdentitiesOnly=yes -i`, so a re-run logs in once password SSH is off;
2. install and `node prepare` as usual, headless: the password is asked once with getpass even for one machine and reaches root only as `sudo -S`'s standard input (the log says `<password>`);
3. `/etc/docker/daemon.json`: `max-download-attempts: 100` and the `nvidia` runtime merged in, every other key kept; docker restarted only when the file changed and no `dreamference-vllm-*` container runs (otherwise reported);
4. the root helper `/usr/local/sbin/mightling-node-root` (`vllm_server/host_root_helper.py`), installed root:root 0755 after `sh -n`, then `/etc/sudoers.d/mightling-agents`, checked with `visudo -cf` before it is installed, whose one rule lets the node's user run that helper, and nothing else, without a password. The helper takes a fixed set of verbs, the root steps of `ling-admin host setup` (`probe`, `sysstat`, `earlyoom`, `swap`, `sysctl vm.min_free_kbytes|vm.watermark_scale_factor`, `docker-group`, `bubblewrap`, `bwrap-profile`), checks every argument before it asks for root, and runs system programs on a fixed `PATH`; `host setup --yes` on the node hands each step's verb to it when `sudo -n true` fails. Every other root command asks for the password. Until 2026-10-29 the drop-in allowed `systemctl`, `apt-get`, `apt` and `docker` with any arguments and `ling-admin host setup|node prepare` from paths the user can write, which is root to anyone with the user's shell (finding A4); the next role run replaces that rule, and a helper that differs from the rendered one, in place. A file granting `NOPASSWD: ALL` is reported, never removed;
5. model folders this machine has no copy of: `ling-admin model download` there (the pinned revision);
6. pairing as usual, then the node must be among the paired ones `node list` shows;
7. `forge on` if its forge is not running, then `forge mirrors sync` from here;
8. key-only SSH: a separate login with the key alone (no master, `BatchMode`, publickey only) must work first; then `/etc/ssh/sshd_config.d/00-mightling-key-only.conf` (`00-`: sshd keeps the first value, and `50-cloud-init.conf` may allow passwords), `sshd -t`, `systemctl reload ssh`, `sshd -T` must say `passwordauthentication no`, and the key must still work, else the drop-in is removed again;
9. after the model server is started and verified: `night` makes `~/.local/share/dreamference/swe-bench/{chain,pins}`, `~/.ling-gpu.lock` and `~/.ling-gpu-holds.log` and runs `swe-bench setup` and `smoke`; `engine` installs `cmake` and `ninja-build` and reads the CUDA version from `nvcc` (drivers are never touched);
10. a readiness table (host check, model server canary, egress audit, disk free, the role), with a non-zero exit when a check fails; `sudo -k` at the end.

Root files travel base64-encoded on sudo's command line (they hold no secret) into a `mktemp` file, never a fixed name under `/tmp`. Every step reads first and changes nothing when it is done; `--dry-run` only reads (sudo included) and prints `would:` lines.

## Keep when touching this

- Every unattended caller of `ling` (Night Shift, the egress audit, SWE-bench, the Codex test runner) names the model server in `DREAMFERENCE_VLLM_HOST`, because a browse is a multicast DNS query and the audit counts one as a failure.
- Tests never write `/etc/avahi` or run sudo (conftest points `NodeServiceFile.service_path` at scratch), and never read or change this machine's Wi-Fi: conftest stubs `WifiPowerSave._read` and fails any `nmcli`/`iw` command that changes something.
- The client's Wi-Fi reading lives in the locator crate, so it is in both byte-identical copies (`ling-web-rs` compiles it and never calls it); it mirrors `WifiPowerSave` (`LC_ALL=C`, iw also looked for in `/usr/sbin` and `/sbin`, off means `disable` or `2`, the radio's `on` counts), and its tests take nmcli's and iw's output as strings. Change one side's reading, change the other.
- A job sent over SSH does not inherit a permissive AppArmor profile; see [host-safety.md](host-safety.md#bubblewrap-needs-an-apparmor-profile-and-this-machine-now-has-one).
