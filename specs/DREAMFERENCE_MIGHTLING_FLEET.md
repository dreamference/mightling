# Mightling Fleet: setting up more GB10s from the one you have

**Status:** proposed on 2026-10-02; §15's open questions answered by the user on 2026-10-03. Phases 1–3 and part of 4 built on 2026-10-03 and tested offline only (§16); merged into `main` on 2026-10-08 (`249c262`, ported onto the renamed tree) and released in 1.5.1. A second GB10 has existed since 2026-10-08, but it was installed by hand over SSH, not by `node provision` (MIGHTLING_NODE §18.10), so `node provision` itself has still not run against a machine; Phase 0 (§13) is owed. On 2026-10-20 its dry run was run against that machine and what had gone stale was fixed (§17). The research is from NVIDIA's documentation and playbooks, read on 2026-10-02 (§2, sources at the end). The local facts were read on this GB10 (`gx10-1a2b`), without changing anything (§3). No second machine was available, so nothing between two machines was run; Phase 0 (§13) lists what has to be measured on the first new unit.
**Target:** new DGX Spark-class machines (DGX Spark and the partner GB10 units; this one is an ASUS Ascent GX10) on the same local network as an existing Mightling node.
**Builds on:**
- the client/node split, discovery and SSH pairing in [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md), in particular §9 (installing), §12.4 (no roles), §13.2 (pairing), §15.1 (control over SSH) and §18.6 (pairing as built);
- the release install `install.sh` and the host settings `ling-admin host check|setup` in [SETUP](./DREAMFERENCE_SETUP.md) §3.2–§3.3;
- the model matrix and the images its recipes pin ([MODELS](./DREAMFERENCE_MODELS.md), [DOCKER](./DREAMFERENCE_DOCKER.md));
- the trusted-LAN decision of 2026-09-30 ([README](./README.md), "Accepted by design").

**The answer, first.** Yes, the work can be automated from this machine, apart from a first-boot wizard of a few minutes on each new unit. That wizard runs from a phone, so it needs no monitor or keyboard. NVIDIA documents a way to skip it: a recovery image repacked with cloud-init. That route still needs a USB stick and a wired keyboard at every machine, and the scripts it depends on have no public download, so it is not the first route (§4.3). Everything after the wizard is done by one command run on this GB10, `ling-admin node provision <host>…`. Re-running the same command later is also how the fleet is updated.

**Decisions made here, stated first because each could be read the other way:**

1. **Extend `ling-admin node`. Do not use Ansible** (§6). The logic already exists as `install.sh`, `host setup`, `node enable` and `node add`, and each of them reads the host before it changes it. Provisioning runs those same steps on another machine. It does not reimplement them.
2. **Two channels, kept apart.** A **provisioning session** is an ordinary SSH login, authenticated by the account's password, and it lasts only while the command runs. It is the only channel that installs anything or runs anything as root. The **pairing key** of MIGHTLING_NODE §13.2 is left as restricted as it is today. It is used to steer, read and check, never to install. The rule behind this is that **changing what a node runs needs the node's password; using and steering it needs only the pairing** (§8).
3. **No new persistent access.** There is no `NOPASSWD` sudoers entry. No full-access key is left on any node. NVIDIA's `discover-sparks` copies one shared private key to every machine; nothing like that happens here. Passwords are typed at the prompts of `ssh` and `sudo`, or held in memory for one run (§8.2). They are never written to disk and never put on a command line.
4. **The fleet runs what this machine runs.** By default a new node gets the Mightling version and the model that the machine typing the command has, copied over the LAN, and the same model-server image (`--model-source hf` instead downloads the public weights from Hugging Face on the node itself, verifies every file by SHA-256 against this machine's snapshot, and sends only what the Hub lacks by `rsync`, or copies from a donor node in the same run over their LAN; `FleetHubFetch` in `dreamference/node/fleet_hub_fetch.py`). A new node therefore needs no GitHub token and no Hugging Face token. It does still need the internet, for two things: the Python dependencies from PyPI (§7.2) and the digest-pinned images from their registry (§7.4). That lasts until Phase 0 settles the wheelhouse and the image-id check; after that, a node could be provisioned with no internet at all.
5. **Still no roles** (MIGHTLING_NODE §12.4). The managing machine is whichever one the command is typed on. Pairing goes one way: from it to each new node. There is no inventory file. The fleet is the set of paired nodes in `~/.config/dreamference/nodes/`.
6. **Provisioning never touches the network configuration, `sshd` or the user accounts.** A step that can lock you out of a machine is not worth automating on a fleet of five.

**Exception, `--role` (2026-10-27).** `node provision --role night|engine` deliberately leaves the access the last hand-provisioned node has: the main node's key authorised, a limited `NOPASSWD` sudoers drop-in for the agents (checked by `visudo -c`) and key-only SSH, turned on only after a separate key login worked. Without `--role` decisions 3 and 6 hold as written. See docs/dev/node.md, "Provisioning a node with a role".

---

## 1. Goals and non-goals

**Goals**
- Per new machine, as little done in person as NVIDIA's first boot allows, and no monitor or keyboard (§4).
- Then, on the existing GB10, one command brings each new machine to a working `mightling-node`:
  - Mightling installed;
  - the host settings applied, so `host check` passes;
  - the assigned model and its image present, copied over the LAN;
  - advertised (`node enable`);
  - paired (`node add`);
  - the model server started.
- The command is idempotent and safe to re-run. A second run on a finished node changes nothing and says so.
- The same command updates the fleet later. `node list` shows drift between nodes.
- One summary at the end: what was done on each machine, and what is left.

**Non-goals**
- Installing the operating system, or re-imaging a machine (§4.3 explains why not now).
- Splitting one model across machines (topology (b) in MIGHTLING_NODE §12.2).
- Firmware and OS upgrades by default. They are an opt-in step (§10.3).
- Any machine that is not a GB10.
- Joining a corporate configuration-management system. §6.3 gives the escape hatch.

---

## 2. What NVIDIA provides

All read on 2026-10-02. The DGX Spark documentation pages carry "Last updated Sep 10, 2026" unless another date is given.

### 2.1 First boot

| Question | Answer | Source |
|---|---|---|
| How is a new unit set up? | A first-boot wizard. With no display attached, the unit brings up a **Wi-Fi hotspot** whose SSID and password are printed on a sticker on the Quick Start Guide. A phone or laptop joins it, and a captive portal (or the setup page named in the Guide) runs the wizard. With a display and a keyboard attached, the same wizard runs on screen | first-boot.html |
| Steps | Language and time zone, keyboard (local path only), **EULA**, username and password, optional analytics, Wi-Fi network, then a software update | first-boot.html |
| Ethernet | The Wi-Fi step "is automatically skipped if an Ethernet cable that is providing internet access is connected" | first-boot.html |
| First-boot update | It downloads and installs the full software image. This "may continue for up to 10 minutes after the interface shows that the device is rebooting" and must not be interrupted. **Total duration not stated** | first-boot.html |
| SSH afterwards | Implied, not stated for Spark: first-boot.html goes straight on to connecting "via NVIDIA Sync tool, SSH, or remote desktop". The DGX OS 7 guide (written for servers) says the SSH server is installed and enabled. **Confirmed on this unit** (§3) | first-boot.html; DGX OS 7 user guide |
| mDNS | On. NVIDIA's own instructions use `spark-abcd.local`, and its `discover-sparks` script finds peers by browsing `_ssh._tcp` | dgx-dashboard.html; dgx-spark-playbooks |
| Docker | Docker and the NVIDIA Container Toolkit are preinstalled, but "by default, Docker requires `sudo`": the user is **not** in the `docker` group | nvidia-container-runtime-for-docker.html |
| NVIDIA Sync | A desktop application (Windows, macOS, Ubuntu). When a device is added it uses the password to "configure SSH key-based authentication", manages tunnels, and finds devices over mDNS. It has no documented command-line mode | nvidia-sync.html; connect-to-your-spark README |
| Out-of-band management | **No BMC is documented.** The listed ports are 10GbE RJ-45, 2× QSFP (ConnectX-7), USB-C, HDMI, Wi-Fi 7 and Bluetooth, with no management port. No page says so explicitly | hardware.html; enterprise-manageability.html |

### 2.2 Unattended paths

- **Custom install with cloud-init** (enterprise-custom-install.html).
  - Two image types can be repacked with a cloud-init seed: the BaseOS ISO (`repack_baseos.sh`) and the public recovery tarball, "FastOS" (`repack_fastos.sh -f <tarball> -c <cloud_init_dir>`).
  - The seed is copied to `/var/lib/cloud/seed/nocloud`.
  - **If the user-data creates a user with a password, the first-boot wizard is skipped**; the page says the workflow "bypasses individual end-user license prompts".
  - The user-data can set users, `ssh_authorized_keys`, hostname, network, packages and services. NVIDIA's example puts the user in `adm, sudo, audio, dip, plugdev, users, lpadmin`, **not `docker`**.
  - An `OEMDATA` USB partition can carry a `hook.sh`, `.deb`s, firmware capsules, an APT repository URL and an LVFS mirror.
  - **Not available to us as documented:** the repack scripts come from a "DGX OS customization repository (NVIDIA-provided reference package)" with no URL given, and the BaseOS ISO is obtained "through NVIDIA Enterprise or an OEM website".
- **PXE** (pxe.html).
  - PXE is switched on per machine in the UEFI setup. Secure Boot must be off, or the bootloader enrolled.
  - dnsmasq serves `grubnetaa64.efi.signed` over TFTP, and an HTTP server serves the ISO or the recovery tarball; the kernel line carries `autoinstall`.
  - **Unverified:** which NIC can boot this way, whether the cloud-init seed is read on this path, and whether UEFI HTTP boot works.
- **Recovery from USB** (system-recovery.html).
  - The recovery image is downloaded from nvidia.com/drivers and written to a USB stick of 16 GB or more.
  - At the machine: Esc/Del into the UEFI setup, Restore Defaults, then Boot Override to the stick, with a **wired USB keyboard**. Secure Boot may have to be switched off for the stick to boot.
  - It erases the internal SSD. No duration is given.
- **Fleet tooling** (enterprise-fleet-lifecycle.html).
  - NVIDIA recommends Canonical Landscape (needs Ubuntu Pro) or **Ansible**, both agentless over SSH.
  - It ships a ZIP of reference tools, among them `spark_updatectl.py` for updates and `spark_diagctl.py` for diagnostics.
  - Base Command Manager, Mission Control and Fleet Command are not mentioned for Spark. That they do not apply to it is our reading of their absence, not something stated.

### 2.3 NVIDIA's multi-Spark playbooks

- **Connect Two Sparks** (build.nvidia.com/spark/connect-two-sparks; about 1 h).
  - The same username on both systems, and one QSFP cable.
  - Static addresses written with netplan (`192.168.100.10/24` and `.11`).
  - Then `discover-sparks`, which browses `_ssh._tcp` on the ConnectX interfaces, generates **one shared ed25519 key** and copies the private and public halves to every node, asking each node's password.
- **Multi Sparks Through a Switch** (Mar 20, 2026; about 2 h).
  - A 200G QSFP switch.
  - `spark_cluster_setup.sh -c <json> --run-setup` sets addresses, sets up passwordless SSH and runs an NCCL test. **Its JSON holds each node's password in plain text.**
  - NVIDIA Sync's Cluster Assistant supports up to 3 Sparks cabled directly, or 4 through a switch (spark-clustering.html).
- **What they automate** is the network and SSH setup for *one model split across machines* (NCCL, MPI). That is topology (b), which Mightling does not use. They install nothing Mightling needs. What carries over is their convention (the same username everywhere) and their discovery method (browsing `_ssh._tcp`). Their two shortcuts are not adopted: a shared private key, and passwords in a file.

### 2.4 Updates

- The DGX Dashboard is NVIDIA's "primary and recommended" way.
- By hand, "from a remote or local terminal": `sudo apt update && sudo apt dist-upgrade`, `sudo fwupdmgr refresh`, `sudo fwupdmgr upgrade`, then `sudo reboot` (os-and-component-update.html).
- Both work over SSH. On this unit the Dashboard's update button drives `aptdaemon` over D-Bus (seen in the journal, §3).

---

## 3. What this GB10 shows (read-only, 2026-10-02)

| Question | Result |
|---|---|
| OS | Ubuntu 24.04.4 LTS. `/etc/dgx-release`: DGX Spark, platform `GX10`, software build 7.2.3 (2025-10-04), **OTA 7.5.0** (2026-08-04). Kernel `6.17.0-1029-nvidia`, driver 580.173.02. DMI: ASUSTeK `GX10` |
| How the OS was laid down | By Subiquity **autoinstall** from "Ubuntu-Server 24.04.3 LTS … arm64 (20250806.1)" (`/var/log/installer/media-info`; `autoinstall-user-data` is root-only). cloud-init is installed, with status `disabled` and `DataSourceNone`. The factory image is therefore an autoinstall image, as §2.2's path implies |
| The first-boot wizard | Package `dgx-oobe` 0.25.1. It provides a web service (`oobe-service -port 80 serve`, run as `gnome-initial-setup`), `dgx-oobe-hotspot.service`, and `dgx-oobe-hostname.service` (`set-hostname.sh`). All four units are `disabled` once setup is complete. Also installed: `nvidia-oem-config-{eula,bmc,crypt-passwd,grub-passwd,postact}` |
| Host name | `gx10-1a2b`. The wired MAC is `xx:xx:xx:xx:1a:2b`, so the suffix appears to be its last two bytes. That is inferred from one unit |
| SSH after the wizard | `openssh-server` 9.6 with `ssh.socket` enabled and active. **Password login allowed** (only `KbdInteractiveAuthentication no` is set). No `sshd_config.d` drop-ins |
| Advertised over mDNS | `dgx-oobe` installs `/etc/avahi/services/ssh.service`, so the machine advertises `_ssh._tcp` as "`<hostname> SSH`". `avahi-daemon` is active and `libnss-mdns` is installed. This is how a freshly set-up unit can be found (§9.1). It was browsed here only from the machine itself |
| Docker | `docker-ce` 29.2.1, storage driver `overlay2`, `nvidia-container-toolkit` 1.19.1, `nvidia` runtime configured. The user is in `docker`, added by `usermod` at 00:49 on the first day, about ten minutes after the first journal entries that show the wizard had completed. That fits NVIDIA's statement that the wizard does not add it |
| Host-safety prerequisites | `earlyoom` 1.7 and `sysstat` installed and active. `bubblewrap` 0.9.0 installed. `kernel.apparmor_restrict_unprivileged_userns = 1`, so `host check` fails its sandbox check here (SETUP §3.3) |
| Lingering | `Linger=no` |
| Other tools present | `rsync` 3.2.7, `rrsync`, `zstd`, `python3-venv`, `fwupd`. Not present: `sshpass`, `pigz`, `ansible` |
| NVIDIA's telemetry | `nvidia-dgx-telemetry` (7.3) is active and enabled. Its journal lines mention an "OOBE event" being sent |
| Network | This machine is on **Wi-Fi** (`wlP9s9`). The 10GbE port (`enP7s7`, Realtek 8127) has no carrier. **No ConnectX device appears on `lspci`.** `dgx-spark-mlnx-hotplug` is installed and its udev rule handles hot-plug, so the QSFP NIC is probably absent until a cable is in; that is unverified |
| Sizes a new node needs (default model) | SGLang image (digest-pinned) **33.4 GB**. RadixArk Qwen3.8-27B-NVFP4 **21 GB**. DFlash2 drafter 1.5 GB. Diffusion model 1.2 GB. nomic embedding model 0.6 GB. About **58 GB**, plus the `ling-admin` virtualenv (5.8 GB, mostly PyTorch; SETUP §3.2). The fallback's locally built DFlash images are **40.7 GB each** and cannot be pulled from anywhere; its weights are 72 GB |
| Disk | `/` is 916 GB, 292 GB free |
| `sudo` | Asks for a password (`sudo -n true` fails). `/etc/sudoers.d/` holds only its README |

---

## 4. What must be done in person

### 4.1 The minimum, per machine (the wizard route, chosen)

1. Unpack the unit. Connect power and **an Ethernet cable on the same LAN as the existing GB10, with internet behind it**. That makes the wizard skip its Wi-Fi step, and it is also the link the model is copied over (§7.5).
2. Power on. From a phone, join the hotspot printed on the Quick Start Guide's sticker; the setup page opens.
3. In the wizard, choose the language and time zone, accept the EULA, and create **the same username as on the existing GB10**, with a password. That is NVIDIA's own playbook convention, and `node add` defaults to it. Then make the analytics choice.
4. Let the first-boot update and the reboot finish. Do not power the unit off.
5. Nothing else. The host name is on the sticker, and `node provision` also finds the unit on its own (§9.1).

**No monitor, keyboard or USB stick is needed.** Several units can probably be set up at once, each through its own hotspot with its own SSID. That is assumed, not sourced. Attended time is assumed to be about five minutes per unit; the wall time, dominated by the update, is unknown. Both are the first Phase 0 measurement.

### 4.2 What cannot be removed on this route, and why

- **The EULA and the account** are accepted and created by a person, on purpose: NVIDIA's wizard is the licence step. Only the cloud-init route bypasses it, and that route has the customer accept the licence on behalf of the fleet.
- **The update** runs inside the wizard. It cannot be deferred or done from another machine.
- **No machine can be reached before the wizard has finished.** There is no BMC (§2.1). The wizard's own service listens on port 80 during setup, but its API is undocumented; driving it from another machine would be scraping a private interface that NVIDIA can change in any update, so it is rejected.

### 4.3 The zero-touch route: recorded, not chosen now

NVIDIA's documented route (§2.2): repack the recovery image with a cloud-init seed that creates the user, adds an SSH key and sets the host name, so the wizard is skipped. Why it is not the first route:

- **It is not less in-person work for a handful of units.** Every machine still needs a USB stick, a wired keyboard, Esc/Del into the UEFI setup, Restore Defaults and Boot Override, and possibly Secure Boot switched off and back on. That is more than four screens on a phone.
- **The tools are not obtainable as documented.** The repack scripts have no public URL. Whether the ASUS GX10's recovery image is NVIDIA's or ASUS's, and whether it accepts the same seed, is unknown.
- **It erases the disk**, which is harmless on a new unit but a sharp tool to hand to a fleet command.

If it is pursued later (question 8 answered *not now*, 2026-10-03), Mightling's part is small. `ling-admin node seed` would write the `user-data` and `meta-data`, containing:
- the user, in the `docker` group;
- the host name;
- a **one-time bootstrap key** that `node provision` removes once the pairing exists.

The licence acceptance is not something Mightling writes on the user's behalf. The seed would carry it only after the user has read the EULA and explicitly agreed.

**PXE is rejected at this scale.** It needs a DHCP/TFTP/HTTP server on a LAN whose DHCP is normally the router's, plus a UEFI change at each machine anyway.

---

## 5. The command

```bash
ling-admin node provision                       # list unprovisioned Sparks on the LAN (§9.1)
ling-admin node provision spark-1a2b gx10-77c0  # provision these
ling-admin node provision --all                 # re-run on every paired node: the fleet update
ling-admin node provision spark-1a2b --model qwen3.8-27b-nvfp4-dflash2 --web
```

| Option | Meaning |
|---|---|
| `<host>…` | A host name (`spark-1a2b`, `spark-1a2b.local`), an address, or a paired node's name |
| `--all` | Every paired node |
| `--user <name>` | The account on the new machines. Default: this user's name |
| `--model <key>` | The model each named node is assigned. Default: this machine's configured model. Only keys of the matrix, as `node set` (the 122B keys were removed in 1.5.1) |
| `--from this\|release[=X.Y.Z]` | What to install (§7.2). Default: `this` |
| `--model-source copy\|hf` | Where a node gets its weights (§7.4): `copy` (`rsync` from this machine, default) or `hf` (the node downloads the public weights from Hugging Face, this machine verifies every file by SHA-256 against its snapshot and sends only what the Hub lacks, falling back to copying from a donor node in the same run over their LAN) |
| `--role night\|engine` | Also configure the node for a role (`NodeRoleProvisioner` in `dreamference/node/node_role_provisioner.py`): main node's key authorised, Docker `daemon.json`, the agents' limited sudoers drop-in, model download when this machine has no copy, forge mirror, key-only SSH, and the role's setup and readiness report |
| `--per-host-password` | With several hosts: ask for each machine's password separately. **Default is one password**, asked once and used for every machine's login and `sudo` (§8.2; decided 2026-10-03) |
| `--mesh` | Also pair every provisioned node with every other, so any of them can manage the fleet. Off by default: only this machine pairs with each node (§9.3; decided 2026-10-03) |
| `--web` | Also share the web UI there: `node enable` without `--no-web`, which puts `ling web` on the LAN (until 1.7.0 it meant installing Onyx, `chat start` and `configure`). Off by default |
| `--no-forge` | Set the nodes up without Forgejo (`node enable --no-forge`). Off by default |
| `--no-start` | Leave the model server stopped |
| `--restart` | Restart a running model server whose image or model has changed. Without it, the summary says a restart is pending |
| `--os-update` | NVIDIA's documented update before everything else, with a reboot (§10.3). Off by default |
| `--via <address>` | Copy and install over this address instead (e.g. a QSFP link, §7.5) |
| `--match <pattern>` | Name pattern for the `_ssh._tcp` discovery browse when no host is given |
| `--start-timeout <seconds>` | Seconds to wait for a started model server (default: `1200`) |
| `--dry-run` | Connect and read only, then print what each machine would change |

**Why under `node` and not a new `fleet` group.** The fleet is nothing but the set of paired nodes, which `node list` already shows; a `fleet` group would suggest an inventory and a fleet manager, which do not exist (decision 5). The document is named FLEET because its subject is the set of machines and keeping it in step.

---

## 6. Ansible, or `ling-admin node`

NVIDIA recommends Ansible for Spark fleets (§2.2), and it would work. It is not chosen, for these reasons.

### 6.1 What Ansible would add and duplicate

- **Duplication.** The work is `install.sh`, `HostSafetySetup` (which reads every setting with the check's own helpers and changes only what fails), `node enable` and `node add`. A playbook either reimplements them as Ansible tasks, giving two copies that drift, as SETUP §3.3 records the check and the fix once did. Or it calls `ling-admin` through `command:`, at which point Ansible is an SSH loop with an inventory around our commands.
- **More to install and learn.** `ansible-core` is not on DGX OS (§3), and Ansible brings an inventory file, playbooks and roles to keep. The project's rule is few moving parts, and the user asked for it to be "as simple as possible".
- **What it would not do.** Find new units over mDNS, pin host keys to node ids, or install the restricted pairing key with its forced command. Those are Mightling's own (MIGHTLING_NODE §13.2) and would still be Python.

### 6.2 What Ansible gives for free, and what replaces it

| Ansible | Here |
|---|---|
| Parallel fan-out | Only what pays at this scale: the questions are asked for every machine first, installs run side by side, and copies from this machine run one at a time (§9.2) |
| `--ask-become-pass` | the password asked up front, once for all machines by default (§8.2) |
| Idempotent modules | Mightling's commands already read before they change |
| A run report | The summary table (§11) |

### 6.3 The escape hatch

If the fleet ever joins an organisation's own configuration management, a playbook that runs `ling-admin node prepare` and `install.sh --from` is a few lines. The commands of §7 are the stable interface for it. **Decided 2026-10-03: no example playbook is shipped**; one supported route.

---

## 7. What a run does, per machine

### 7.1 Order

Each step reads first and does nothing if the machine already satisfies it.

| # | Step | Channel | Root | Changes on the machine |
|---|---|---|---|---|
| 1 | **Connect.** Open the provisioning session (§8.1) and check that it is a GB10: `/etc/dgx-release`, `nvidia-smi` | session | no | nothing |
| 2 | **Read the state.** A plain shell probe, sent over the session (Mightling may not be there yet), reports: Mightling's version if installed; the facts `host check` reads (swap, the two sysctls, earlyoom, sysstat); the docker group; lingering; node id; whether the node is advertised; models and images present; free disk | session | no | nothing |
| 3 | *(opt-in)* OS update (§10.3) | session | yes | packages, firmware, reboot |
| 4 | **Install Mightling**, from a bundle copied from this machine (§7.2): `install.sh --from <dir> --role node --no-advertise --no-host-setup --no-model` | session | no | `~/.local/share/dreamference/{mightling,venv}`, links in `~/.local/bin` |
| 5 | **Root half**: `sudo ling-admin node prepare` (§7.3), in one command so `sudo` asks once | session | yes | §7.3's list |
| 6 | **User half of advertising**: `ling-admin node enable --no-web` (or without `--no-web` under `--web`, and with `--no-forge` under `--no-forge`). The Avahi file is now the user's, so this needs no root, as in MIGHTLING_NODE §18.2. It writes `node-advertise.json`, sets up the node's forge unless `--no-forge` is given, and moves SearXNG to every interface, which `ling-search` on clients needs | session | no | `~/.config/dreamference/node-advertise.json`; the forge; SearXNG's publish address |
| 7 | **Model and images** (§7.4): weights and local-tag images copied from this machine, and digest-pinned images pulled by the node itself, here and not inside step 11, so the pull is timed and reported on its own. Docker commands run as `sg docker -c '…'`, because the session's login predates the group that step 5 added. Then `ling-admin searxng start`, which `server start` does not do, so that `ling-search` on clients has a SearXNG to ask (its image, 254 MB, is pulled by the node) | session | no | `~/.cache/huggingface/hub/models--…`, Docker images, the SearXNG container |
| 8 | **Assignment**: `ling-admin main-model set <key>` there | session | no | `~/.config/dreamference/config.toml` |
| 9 | **Pair**: `node add` through the open session (§7.6) | session | no | one `authorized_keys` line; here, the node record and its pinned host key |
| 10 | **Close the session.** From here only the pairing key is used | — | — | — |
| 11 | **Start**: `node start <node>`, then wait for `state=ready` on the advert, with a time limit (default 20 min) | key | no | the model containers |
| 12 | **Verify**: one chat completion through the node's open model port, one JSON query to its SearXNG on port 8888 from this machine, and `node status` | open ports, key | no | nothing |

### 7.2 What is installed: a bundle from this machine

`install.sh` gains **`--from <dir>`**: install from a directory holding the same asset names and the same `ling-<target>.sha256sums`, with no network. The checksum checks stay as they are. It also gains `--no-host-setup`, because step 5 does that part, and (2026-10-08, when a node install began downloading and starting the model by itself) `--no-model`, because the model steps do that one. The bundle is built here, once per run, under `~/.cache/dreamference/fleet/bundle-this-<digest[:12]>/` (or `bundle-<tag or 'latest'>/` for a release):

- **`--from this`** (the default).
  - **On a machine running from a checkout**, as this one does, the bundle holds:
    - the binaries installed here (`ling`, `codex-code-mode-host`, `ling-search`, `ling-fetch`, `ling-code`, plus `ling-docs`, `ling-signal` and `ling-delegate` when present), gzipped under the release names;
    - a wheel of the checkout's `dreamference` package (`pip wheel --no-deps`);
    - a `sha256sums` file written for them.
    The new node then runs the same build as this one, including a source build that was never released. That is the point for a development fleet.
  - **On a release install** there is no wheel to copy, since pip does not keep the file. There `this` means `release=<the installed version>`, fetched as below.
- **`--from release[=X.Y.Z]`**. The release's own assets, downloaded once here with this machine's token (`GH_TOKEN` or `gh`), checked against the release's checksum file, then copied. The token stays on this machine.
- **Python dependencies.** The wheel's dependencies come to about 5.8 GB, mostly PyTorch. By default the node downloads them from PyPI. With a wheelhouse built here (`pip download` for `aarch64`/cp312, which is the same platform), the node installs with `--no-index` and needs no internet. Whether to make the wheelhouse the default is a Phase 0 measurement of the two times.
- The installed `ling-admin` and the binaries on every node are those of one bundle, and the bundle's version is recorded in the node record here (§11).

### 7.3 `ling-admin node prepare`: the root half

A new command that does **every root step of a single-machine install, and nothing else**, for the user who invoked `sudo` (`SUDO_USER`). It refuses to run if not root or if `SUDO_USER` is missing. It prints each step before doing it, the rule `host setup` follows. It is also the one command a person would type on a single machine (`sudo ling-admin node prepare`), so it is not fleet-only code.

| Step | Source of the logic | Change |
|---|---|---|
| Host settings | `HostSafetySetup.steps()` unchanged | sysstat, earlyoom and its arguments, swap to 64 GB, the two sysctls (SETUP §3.3) |
| Wi-Fi power saving | `HostSafetySetup.steps()`'s last step, `WifiPowerSave` (added 2026-10-12) | on a connected Wi-Fi connection only: `nmcli connection modify <connection> 802-11-wireless.powersave 2` and `iw dev <interface> set power_save off`. Power saving loses most of the multicast mDNS clients find a node by (MIGHTLING_NODE §2; up to ~80% of queries unanswered on 2026-10-11/12). Neither command reconnects, so an SSH session running `prepare` survives. Ethernet only, or no NetworkManager: no step |
| Docker without `sudo` | new, small | `usermod -aG docker <user>` (NVIDIA: not done by the wizard, §2.1) |
| Lingering | new, small | `loginctl enable-linger <user>`, which jobs and Night Shift need (MIGHTLING_NODE §13.7) |
| Advertising | `NodeAdvertiser.enable()`'s root part | `/etc/avahi/services/mightling-node.service`, owned by the user |
| Sandbox | `HostSafetySetup`'s AppArmor step (SETUP §3.3), adopted and verified on this machine on 2026-10-03 | `/etc/apparmor.d/puffin-bwrap` (`userns` for `/usr/bin/bwrap` alone), loaded with `apparmor_parser -r` |
| **NVIDIA's telemetry** | new, small (decided 2026-10-03) | **`systemctl disable --now nvidia-dgx-telemetry`, always**, as Mightling disabled vLLM's and Onyx's telemetry. It is NVIDIA's service, so the step is printed before it runs and named in the summary; no flag skips it |

What it **never** changes: `sshd`, netplan, NetworkManager beyond the one Wi-Fi setting above (decided 2026-10-12: every other `nmcli` command stays refused), the firewall, users and passwords, APT sources, kernel parameters other than the two sysctls, and NVIDIA's services other than the telemetry service above.

`node prepare` runs from the user's own virtualenv as root. Anyone who can write that virtualenv is the user, who has `sudo` anyway, so this grants no one anything new; it is stated so that the choice is visible.

### 7.4 The model and the images

What a node needs is computed **here**, from the matrix entry of the model assigned to it (`FleetModelPlan.plan` in `dreamference/node/fleet_model_plan.py`): the main repository at its pinned revision, a drafter if the recipe names one, any engine assets (`ModelMatrixRegistry.get_engine_assets`), the diffusion model when diffusion is enabled (`DIFFUSION_ENABLED = False` since 2026-10-03), the embedding model when this machine has it, and the recipe's image plus SearXNG (Onyx's web UI images were retired in 1.7.0).

- **Weights (`--model-source copy`, the default):** `rsync -aH --partial` of each `~/.cache/huggingface/hub/models--<org>--<name>/` directory, over the session (retrying up to `COPY_ATTEMPTS = 30` times if a link drops). With **`--model-source hf`** (`FleetHubFetch` in `dreamference/node/fleet_hub_fetch.py`), the node downloads the public weights from Hugging Face itself, this machine checks every file by SHA-256 against its snapshot and sends only what the Hub lacks by `rsync`, or copies from a donor node in the same run over their LAN (`FleetHubFetch.lan_copy`).
  - The cache is a tree of symbolic links into `blobs/`, so `-a`'s link handling matters, and `refs/` travels with it, so a pinned revision resolves offline (the SGLang trap in `docs/dev/models-and-engines.md`).
  - It resumes after an interruption, and a second run copies nothing.
  - The node's own `HF_HOME` is respected through `ModelDownloader`'s resolution.
- **Images built locally** (the fallback's `dreamference-vllm-dflash:…` tags), 40.7 GB each, exist nowhere else: `docker save <tag> | zstd -T0 -3 | ssh … 'zstd -d | docker load'`.
- **Images pinned by registry digest** (the default's `lmsysorg/sglang@sha256:…`) are a trap.
  - `is_image_present()` inspects `name@digest`.
  - With the `overlay2` image store, which this machine uses (§3), a `docker load` is generally known to leave the image without its repository digest. A copied 33 GB image would then not be found, and `server start` would pull it again.
  - Until Phase 0 measures this, **digest-pinned images are pulled by the node itself** (`docker pull` on the node, verified by digest), and the copy is used only for local tags.
  - If a copy is wanted for nodes without internet, the fix is for the registry entry to also record the image id, which `docker save` keeps, and for `is_image_present()` to accept either. That is one field and one comparison, decided after Phase 0.
- **Space** is checked in step 2 against the sum of what is missing, plus 20 GB, before anything is copied.
- **The torch.compile cache** (`~/.cache/dreamference/vllm`) is not copied. It is keyed on the engine and settings, a node rebuilds it once (8–12 min for vLLM recipes), and copying a cache written by a root container is the kind of operation that half-works.

### 7.5 Which link

- The session goes to the address the host name resolves to (trying the NetBird overlay DNS `<host>.<overlay_domain>` before `<host>.local` when remote access is configured, and recording `overlay_name` in the node record so it never browses mDNS for an overlay node), or to the address given.
- **Wired Ethernet or Wi-Fi, both supported** (decided 2026-10-03: a unit may be on either). Wired is recommended (§4.1, step 1) because it also skips the wizard's Wi-Fi step; this machine is on Wi-Fi today. A copy of about 58 GB over Wi-Fi may take longer than each node downloading for itself; Phase 0 measures both, and the summary prints the throughput seen. Nothing in provisioning depends on which.
- **QSFP, optional.** If two machines are cabled directly and configured as NVIDIA's playbook does, `--via <address>` names the address to copy over. Provisioning does not configure that link, because it changes netplan (decision 6). No ConnectX device is visible on this machine without a cable (§3).
- **Several new nodes copy one after another**, never in parallel. They share this machine's one link and its disk reads, and with sequential copies the summary's time for each is meaningful.

### 7.6 Pairing within the session

`node add` today browses `_mightling-node._tcp` to find its target and opens its own password login to run `node authorize` there (`NodePairing.add`, `authorize_on_node`). Two changes:

- **`node add <address>` without a browse.** It reads the node id and name through `node id` over the login, not from an advert. This is needed for a node advertised with `--no-advertise`, and for any node whose multicast does not reach this machine. Today `discover()` alone decides, so such a node cannot be paired.
- **Reuse of the session.** Provisioning passes its multiplexed connection (§8.1) to the pairing, so `authorize` costs no second password.
- The host key the provisioning session accepted on first contact is the one pinned to the node id. If the key changes between the two, pairing refuses.

### 7.7 Re-running, and the update

A second run on a provisioned node goes through the same table:
- step 2 reads the state;
- steps 4, 5 and 7 do nothing when the versions, the settings and the files match;
- step 9 finds the pairing and checks it with `info`;
- step 11 does not restart a running server unless `--restart` is given.

**`node provision --all` is the fleet update**: every paired node is brought to what this machine runs. Its cost is one password for all nodes, or one per node with `--per-host-password`.

---

## 8. Security on a trusted LAN

### 8.1 The provisioning session

- `ssh` with `ControlMaster=auto`, `ControlPath` in a 0700 directory under `$XDG_RUNTIME_DIR`, and `ControlPersist` for the run. The password is typed once per machine at `ssh`'s own prompt, every later command and copy rides the same connection, and the master is closed at the end (or by a signal handler on Ctrl-C).
- **First contact** uses `StrictHostKeyChecking=accept-new` into a known-hosts file of the provisioning run. The fingerprint is printed so it can be compared with what the machine shows locally, and the same key is then pinned to the node id at pairing (§7.6).
- **A group added during the run.** Multiplexed sessions are children of a login that has already taken its groups, so after `prepare` adds `docker` the session's commands do not have it. Rather than reconnecting (which would mean another password prompt), they run Docker through `sg docker -c '…'`, which needs only the group's entry in `/etc/group`. This machine's journal shows the same pattern on its first day: `usermod` adding the group, then `sg … switched to group 'docker'` sixteen seconds later. That `sg` reaches the Docker socket from a multiplexed session is assumed; Phase 0 checks it.
- No agent forwarding and no port forwarding.

### 8.2 `sudo`, and the passwords

- **One host named:** the password is typed at `ssh`'s own prompt, and step 5 runs as `ssh -t … sudo <venv>/bin/ling-admin node prepare`, so `sudo` prompts on the remote terminal. The password goes from the keyboard to that machine and nowhere else. `ling-admin` never sees it.
- **Several hosts named:** the `sudo` prompt cannot come before the slow part, because `prepare` needs the virtualenv that step 4 installs, which takes minutes. To keep §9.2's "questions first", `ling-admin` asks for the password at the start, with `getpass`: **once for all machines by default** (decided 2026-10-03, the same username everywhere makes one password the common case), or once per machine with `--per-host-password`. A machine that refuses the shared password is asked for its own once. It then uses that password for both prompts:
  - for `ssh`'s password prompt, through `SSH_ASKPASS` with `SSH_ASKPASS_REQUIRE=force` (OpenSSH 8.4 or later; this machine has 9.6). The askpass helper is `ling-admin` itself, reading the password from an inherited pipe, never from the environment or argv;
  - for `sudo`, through `sudo -S -p ''`, the password written to the remote command's standard input over the encrypted channel.
- Each password is checked when it is asked for, by opening that machine's session at once. It is held in memory for the run and nowhere else. A machine that refuses one is asked again once, then dropped from the run; nothing is retried in a loop.
- **Not done, and why:**
  - a `NOPASSWD` drop-in (permanent root for whoever holds a key);
  - a full-access key left in `authorized_keys`;
  - `sshpass` (not installed, and it takes the password on argv or in the environment);
  - NVIDIA's shared private key (§2.3);
  - a password in a config file (NVIDIA's cluster JSON).

### 8.3 The pairing key stays restricted

MIGHTLING_NODE built the pairing key so that it cannot open a shell: one forced command, no terminal, no forwarding, and jobs in a sandbox. Provisioning adds **no operation that installs anything through it**. The rule:

- **The key may choose among what the node already trusts.** It can start a model the node's own matrix names, which the node then downloads itself, and it can read the node's state.
- **It may not supply content the node will execute.** That rules out wheels, binaries, images, and even model repositories, since some carry code that runs at load (the diffusion model's remote code does). Every one of those comes over the password-authenticated session.

The one addition is read-only: `info` reports more fields, so that drift can be seen with no password (§10.1).

### 8.4 What the trusted LAN still covers

- **The copies are in clear inside SSH**, so nothing new is exposed on the wire.
- **First contact can be spoofed by another device answering on that name.** The printed fingerprint, and the check of `/etc/dgx-release` in step 1, are guards against accidents, not against an attacker, who by the trusted-LAN decision is not there.
- **A provisioned node is as open to the LAN as any node** (MIGHTLING_NODE §11): inference and search are open, and the web UI is open only with `--web`.

---

## 9. Several machines

### 9.1 Finding new units

`node provision` with no host browses `_ssh._tcp` for 2 s, which the wizard's Avahi file publishes on every finished unit (§3), and lists every SSH host:
- that is not also advertising `_mightling-node._tcp`;
- whose name looks like a GB10's (`spark-…`, `gx10-…`, `zgx-…`, or any name given with `--match`).

Nothing is assumed from the name. Step 1 checks `/etc/dgx-release` before anything is changed. The user picks from the list; nothing is provisioned unasked.

### 9.2 Questions first, then walk away

With several hosts, every question is asked before any slow step starts:
1. one password for all machines (or one per machine with `--per-host-password`), checked at once by opening that machine's session (§8.2);
2. the host-key fingerprint of each new machine, printed;
3. then, unattended: install, root half (with the password already held), advertise, copy, pair, start. The installs run on all machines at once, since each downloads its own Python dependencies or reads its own copy of the bundle; the copies from this machine run one after another (§7.5).

Someone setting up five units types everything in the first minutes and can leave. A machine whose password fails twice is dropped from the run and named, and the others go on.

### 9.3 Pairing between new nodes

Only this machine pairs with each new node, which keeps pairing one-way, as built. Managing the fleet from a different node means running `node add` there. **Decided 2026-10-03: hub only by default.** `--mesh` pairs every node with every other for those who want any node to manage the fleet; it is off by default because it multiplies keys.

---

## 10. Keeping the fleet in step

### 10.1 Drift

The `info` operation of `serve-job` (MIGHTLING_NODE §18.6) gains read-only fields:
- Mightling's bundle version and the `ling-admin` version;
- the result of `host check`;
- DGX OS's OTA version, the kernel and the driver;
- the assigned and the loaded model;
- the main image id;
- free disk.

`node list` adds one column, **drift**, naming what differs from this machine. A node on a different DGX OS version is also flagged. Because `info` goes through the pairing key, seeing drift needs no password.

**A source bundle and `ling update` disagree.** A binary built from a checkout carries no `MIGHTLING_VERSION`, so `ling update` typed on such a node treats it as behind and replaces it with the latest release. That is correct for a single machine and is drift on a fleet. The drift column shows it, and the next `node provision` from the managing machine puts the bundle back. Whether `ling update` should refuse on a node provisioned from a source bundle is left to that command's spec.

### 10.2 Updating

`node provision --all` (§7.7). Nothing updates by itself: no timer, no agent. A model server is restarted only with `--restart`, so people working on a node are not interrupted by someone else's update.

### 10.3 The operating system and firmware

- **Not by default.** NVIDIA's Dashboard remains the way a single owner updates. For a fleet, `--os-update` runs NVIDIA's documented sequence in the root half, before anything else: stop the model server; `apt update && apt dist-upgrade`; `fwupdmgr refresh`; `fwupdmgr upgrade`; reboot; wait for SSH; reopen the session.
- **One node at a time**, never two in parallel. A failed firmware update on one machine must not be repeated on the next.
- NVIDIA's `spark_updatectl.py`, Landscape and an LVFS mirror (§2.2) are the tools for fleets far larger than this one, and are not wrapped.

---

## 11. Failure and reporting

- **A failed step stops that machine at that step.** It never stops the others. Every step is safe to repeat (§7.1), so the fix is to correct the cause and re-run the same command.
- **Nothing can leave a machine unreachable.** The steps do not touch SSH, the network or the accounts (decision 6). A swap resize already refuses the cases it cannot do safely (SETUP §3.3).
- **Atomic where it matters.** `install.sh` checks every checksum before placing a file and renames over the old one. `rsync --partial` resumes. A Docker load either completes or leaves no image.
- **The model server does not start on a host that fails the model-load pre-flight** (`check_host_safety()`), because `server start` refuses by itself. Provisioning reports the refusal, whatever the reason. (`host check`'s bubblewrap line is not part of that pre-flight. A node can serve a model while failing it, which is why the summary lists it separately.)
- **The summary at the end** has one row per machine: host name, address, node id, the bundle version, `host check`, model present, image present, paired, `state`, the verify result, bytes copied and their throughput, and **what is left**, with the exact command for it. The exit code is non-zero if any machine is incomplete.
- **A log per machine and run** is written to `~/.local/state/dreamference/fleet/<date>-<host>.log` on this machine: each command run there and its output, **never a password**.
- **The node record** (`~/.config/dreamference/nodes/<id>.json`) gains `provisioned`: bundle version, model key, image id, and date. That is a cache for the summary. Each run reads the machine again and does not trust the record.

---

## 12. Changes to existing code and specs when this is built

| Where | Change |
|---|---|
| `install.sh` | `--from <dir>` (assets from a directory, same names and checksums, no network), `--no-host-setup` and `--no-model` |
| `dreamference/node/node_pairing.py` | `node add <address>` without a browse, the id read over the login; reuse of a given `ControlPath` |
| `dreamference/node/node_serve.py` | `info` gains the drift fields of §10.1. No new operation that writes |
| `dreamference/node/node_browser.py` | a browse of `_ssh._tcp` beside `_mightling-node._tcp`, for §9.1 |
| new `node_provisioner.py`, `node_prepare.py` (one class each) | §7 and §7.3; the CLI controller gains `node provision` and `node prepare` |
| `HostSafetySetup` | unchanged. If SETUP adopts a bubblewrap fix, `prepare` applies it because it calls `steps()` |
| `VLLMServerManager.is_image_present()` | after Phase 0 only: also accept a recorded image id (§7.4) |
| [SETUP](./DREAMFERENCE_SETUP.md) | a §3.5 pointing here; `prepare` as the single-machine root step |
| [MIGHTLING_NODE](./DREAMFERENCE_MIGHTLING_NODE.md) | §12.2's `node sync-model` is replaced by provisioning's copy over the session (§8.3 says why not through the key); §18.6's `info` fields |
| [CLI](./DREAMFERENCE_CLI.md) | the two commands |

---

## 13. Phases

| Phase | Work | Done when |
|---|---|---|
| 0 | **Measurements on the first new unit** (below) | each has a measured answer recorded here |
| 1 | `install.sh --from`, the bundle (`this` and `release`), `node add <address>`, the `info` fields, drift in `node list` | a node installed by hand from a bundle shows no drift against this machine |
| 2 | `node prepare`; `node provision <host>` for one machine: session, root half, install, copy, pairing, start, verify, summary, log | one new unit goes from the end of the wizard to answering a completion through `ling` on a laptop, with one command typed here |
| 3 | Several machines: finding new units, questions first, one password for all (`--per-host-password` to opt out), `--all`, `--mesh` | three units in one run, with every password typed in the first minutes |
| 4 | Opt-in extras: `--os-update`; `--web`; the image-id fix if Phase 0 calls for it; no `node seed` (question 8: not now) | each tested on one unit |

**Phase 0, on the first new unit:**
1. The wizard: attended time, total time to SSH-ready, and whether the Ethernet cable really skips the Wi-Fi step.
2. After the wizard: SSH on, password login allowed, user not in `docker`, `Linger=no`, and the host name pattern (is it the MAC's last two bytes?).
3. `_ssh._tcp` from the new unit seen by a browse **from this machine**.
4. `ssh -t … sudo …` prompting once; `sudo -S` with `-p ''`; `SSH_ASKPASS_REQUIRE=force` with a helper; a group added by `usermod` invisible to a multiplexed session, and `sg docker -c 'docker info'` reaching the socket from it.
5. Copy throughput over the actual link, for the weights (`rsync`) and for a 40 GB image (`docker save | zstd | docker load`), against the node's own downloads from Hugging Face and Docker Hub.
6. Whether a loaded digest-pinned image keeps its repository digest under `overlay2` (§7.4).
7. With a QSFP cable in: does the ConnectX appear (`lspci`, `ibdev2netdev`), and what does a copy reach over it?
8. The bubblewrap fix chosen by SETUP, applied by `prepare`, then `ling sandbox` from an SSH login and from a user unit.
9. The wheelhouse against PyPI, for the 5.8 GB of Python dependencies.

---

## 14. Tests

Offline, with `ssh`, `rsync`, `docker` and `sudo` replaced by stand-ins (conftest already fails real `docker` changes; add the same for `ssh` and `rsync`):

- **Passwords:** never present in any spawned process's argv or environment, nor in the log file. The askpass helper reads only from its pipe. A refused password is asked again once, for that machine only.
- **Session options:** `ControlPath` under a 0700 directory, `accept-new` only at first contact, no forwarding. Docker commands after `prepare` are wrapped in `sg docker -c`. With one host, `ling-admin` never reads a password. With several, every password is asked before the first install starts.
- **Advertising:** step 6 runs the user-level `node enable`, with `--no-web` unless `--web` is given.
- **`prepare`:** refuses without root or without `SUDO_USER`. For a matrix of host states, its command list is exactly the missing steps. It never emits a command touching `sshd`, netplan, NetworkManager (but for the two Wi-Fi power-saving commands, matched whole), users or APT sources.
- **Idempotency:** a second run against a satisfied stand-in host issues no changing command.
- **`install.sh --from`:** a checksum mismatch installs nothing, and no network is used. The bundle's checksum file covers every asset it holds.
- **Model plan:** the directories and images computed for each matrix entry, including drafter, diffusion and embedding models. Digest-pinned images go to `pull`, local tags to `save | load`. Insufficient disk is refused before any copy.
- **Failure:** a failure at each step stops that machine there, leaves the others running, and produces a summary row naming the remaining command and a non-zero exit.
- **Discovery:** `_mightling-node` hosts are excluded, the name filter applies, and nothing is provisioned without being chosen.
- **Pairing:** `node add <address>` with no browse, and a host key mismatch between session and pairing refused.
- **Drift:** `node list`'s column from stand-in `info` answers.

Live, from Phase 0 on: everything in §13's list, then one unit end to end, then three.

---

## 15. Open questions, answered (2026-10-03)

1. **The same username on every machine:** yes. `--user` stays for an exception.
2. **Network:** a unit may be on wired Ethernet **or** Wi-Fi; both are supported (§7.5). QSFP is optional (`--via`).
3. **Model per node:** the same as this one (the default of `--model`).
4. **The web UI on new nodes:** off by default; `--web` turns it on.
5. **NVIDIA's telemetry service:** disabled always by `node prepare` (§7.3), printed and named in the summary.
6. **OS and firmware updates:** opt-in with `--os-update`; otherwise NVIDIA's Dashboard.
7. **Pairing:** hub only by default; `--mesh` optional (§9.3).
8. **Zero-touch:** not now (§4.3 stays recorded, not chosen).
9. **Passwords:** one password by default for several hosts; `--per-host-password` to opt out (§8.2).
10. **An example Ansible playbook:** no (§6.3).
11. **The bubblewrap fix:** the AppArmor profile route, `ling-admin host setup` (`/etc/apparmor.d/puffin-bwrap`), loaded and verified on this machine on 2026-10-03: from a systemd user unit `bwrap --unshare-user --unshare-net` and `ling sandbox` succeed and `host check` reports nothing to do. `node prepare` applies the same step (§7.3).

---

## 16. As built (2026-10-03), offline only

Built on branch `fleet/provision`, unit-tested with stand-ins for every machine; **no second GB10 existed then, so nothing below has run against one** (the one that arrived on 2026-10-08 was installed by hand, MIGHTLING_NODE §18.10). Phase 0 (§13) is still owed in full. Merged on 2026-10-08 (`249c262`): `install.sh --from` was merged by hand with the signed-release checks, so a release bundle carries `SHA256SUMS` and its signature, and a bundle of this node's own build is checked against its checksums.

**Code**

| Where | What |
|---|---|
| `install.sh` | `--from <dir>`: the asset names are the folder's files and "fetching" copies them; same checksum checks; `curl` is not needed or called. A `VERSION` file names the version and a `wheelhouse/` folder, if present, makes pip use `--no-index`. `--no-host-setup` skips the host settings. Every install now records its version in `~/.local/share/dreamference/mightling/VERSION`, which the probe reads |
| `dreamference/node/fleet_bundle.py` (`FleetBundle`) | `--from this` on a checkout: the installed binaries gzipped under the release names, a `pip wheel --no-deps` of the checkout, `install.sh`, a sums file and `VERSION` (`this-<digest>`), cached under `~/.cache/dreamference/fleet/`. On a release install, `this` is the installed version's release. `--from release[=X.Y.Z]` downloads with `gh release download` |
| `dreamference/node/fleet_session.py` (`FleetSession`) | the provisioning session: `ControlMaster=yes` only on the command that opens it (`-f -N`, its output to a file, never a pipe), `ControlMaster=no` on every later command so a lost master fails instead of re-authenticating; the run's own known-hosts file, `accept-new` only at first contact; no agent or port forwarding; `sudo -S -p ''` with the password on stdin, or `sudo` on a remote terminal (`-t`) when no password is held; `sg docker -c` for Docker; `rsync -aH --partial --mkpath` over the master; `docker save | zstd` piped into `zstd -d | docker load`. A per-host log under `~/.local/state/dreamference/fleet/` records each command and output and writes `(input: <password>)` where a password was sent |
| `dreamference/node/fleet_askpass.py` (`FleetAskpass`) | **departure from §8.2:** ssh closes every descriptor above stderr before it runs its askpass program, so the "inherited pipe" cannot work. The run listens on a Unix socket (0600) in its 0700 folder; `SSH_ASKPASS` is a two-line script running `ling-admin node askpass` (hidden), which asks the socket for the host in `MIGHTLING_ASKPASS_HOST`. Only the socket's path and the host name are in the environment; the server checks the peer's uid (`SO_PEERCRED`) |
| `dreamference/node/fleet_probe.py` (`FleetProbe`) | the read-only POSIX shell probe of step 2 and its parser: GB10 check, bundle version, `docker` group, linger, Avahi file owner and content, AppArmor profile and the userns restriction, NVIDIA telemetry, free disk, hub path, assigned model, SearXNG running, and each needed model folder and image. Run on this machine (dash) as a check: it printed the expected readings and changed nothing |
| `dreamference/node/fleet_model_plan.py` (`FleetModelPlan`) | per matrix key: the checkpoint, drafter and engine-asset folders (`NodeModelSync.repos`), the embedding model when this machine has it, the recipe's image and SearXNG's; a reference with a `/` (digest-pinned or not) is pulled by the node, a bare local tag is copied; the 20 GB disk margin |
| `dreamference/node/fleet_hub_fetch.py` (`FleetHubFetch`) | `--model-source hf`: downloads public weights from Hugging Face on the node, verifies every file's SHA-256 against this machine's snapshot, sends only missing files by `rsync`, and falls back to copying from a donor node in the same run over their LAN |
| `dreamference/node/node_role_provisioner.py` (`NodeRoleProvisioner`) | `--role night\|engine`: main node's key authorised, Docker `daemon.json`, the agents' limited sudoers drop-in, model download when this machine has no copy, forge mirror, key-only SSH, and the role's setup and readiness report |
| `dreamference/node/node_prepare.py` (`NodePrepare`), `ling-admin node prepare` | §7.3 as specified, reading each setting first. Two details: `HostSafetySetup`'s own sandbox check runs bubblewrap from the invoking user's systemd, which root does not have, so `prepare` checks the AppArmor profile itself (file present and listed in `/sys/kernel/security/apparmor/profiles`); and the Avahi file is created **empty** and owned by the user (`install -m 644 -o <user> /dev/null …`), for `node enable` to fill as the user. Every command is checked against a list of words it may never contain (sshd, netplan, NetworkManager, firewall tools, account tools, APT sources, sudoers) and refused if it does; the one exception, since 2026-10-12, is the two Wi-Fi power-saving commands, matched whole (`WifiPowerSave.is_fix_command`): any other `nmcli` or `iw` command is refused |
| `dreamference/node/node_provisioner.py` (`NodeProvisioner`), `ling-admin node provision` | §7.1's steps 1–12 with every option of §5. Questions first: with several hosts one `getpass` for all (or one per host with `--per-host-password`), every session opened and every host-key fingerprint printed before the first install; a host refusing the shared password is asked for its own once. Each step reads the probe and does nothing when satisfied (assignment is skipped when the config already names the model, an unset config meaning the default; SearXNG when it runs). A failing step stops that machine there, the others go on, and the summary table names the step and the command left; exit 1 if any machine is incomplete. After the sessions close, start (`serve-job start`) and verify (a chat completion on the model port, a JSON query to SearXNG on 8888) go through the pairing key and the open ports only. The node record gains `provisioned` (bundle, model, image, date) |
| `dreamference/node/node_pairing.py` | `node add <address>` with no browse (`add_by_login`), and `pair_over_session`: the id and name read over the session (`node id && hostname`), `node authorize` run through it with no second password, the session's accepted host key pinned to the node id; a different key already pinned to that id is refused |
| `dreamference/node/node_browser.py` | `browse_service()` for any DNS-SD type and `unprovisioned()`: `_ssh._tcp` hosts not advertised as `_mightling-node._tcp`, named `spark-…`, `gx10-…` or `zgx-…` (or `--match`) |
| CLI, `sandbox_prerequisite.py`, `scripts/gen_admin_reference.py` | `node provision`, `node prepare`, hidden `node askpass`; the sandbox gate never asks during `node askpass` (it must print the password and nothing else) or `node prepare`; the reference generator skips hidden subcommands |
| `tests/conftest.py` | any real `ssh`, `scp`, `rsync`, `sudo` or `sg` from a test fails it. It caught one at once: the existing pairing test's "not on the network" case now falls through to pairing by address, which tried a real login |

**Tests:** `tests/test_fleet_provision.py`, 34 tests: session options, the password never in argv, environment or log, `sudo -S` against a terminal prompt, `sg docker`, the `-f` master never on a pipe, askpass over a real socket (its own host only, nothing printed otherwise), `prepare`'s refusals and its exact missing steps, no forbidden word in any real step, the probe never writing and its parsing, the plan for every offered model, the disk check, discovery's filtering, host-key pinning and mismatch, `node add` falling back to a login, and whole runs against stand-in machines: one machine end to end, questions before any install with two, one password by default and one per host on request, a second run issuing no changing command, a failure stopping only its machine, a non-GB10 left untouched, a dry run changing nothing, `--web`, another model assigned, an unknown model refused before connecting; and `install.sh --from` against a real bundle (no `curl`, the version recorded, a checksum failure installing nothing). Full suite: 767 passed, 76 skipped.

**Not built**
- `--mesh`: the flag is accepted and says so. Pairing node A with node B needs B's password typed on A, and the session to A has no terminal for it; the askpass route would have to run on A.
- The drift fields of `info` and the drift column in `node list` (§10.1).
- Installs running side by side on several machines (§9.2): machines are provisioned one after another.
- The wheelhouse as the default (Phase 0 item 9); `install.sh` uses one when the bundle carries it, but `FleetBundle` does not build one.
- The image-id fix of §7.4 (after Phase 0).
- `--os-update` is written (stop the server, `apt-get dist-upgrade`, `fwupdmgr`, reboot, wait for SSH, probe again) but is the least tested step: stand-ins only, no reboot ever observed.

**Unverified, and how Phase 0 settles each:** that `sg docker -c` reaches the socket from a multiplexed session (item 4); that `SSH_ASKPASS_REQUIRE=force` with the socket script answers a real password prompt; that `sudo -S -p ''` over `ssh -T` reads the password (item 4); copy throughput (item 5); whether an empty file under `/etc/avahi/services` only makes Avahi log a parse warning until `node enable` fills it; and that the readiness check by `/v1/models` on the node's host name resolves from here (a `.local` name needs mDNS on this machine).

---

## 17. Brought up to date (2026-10-20), before the first new units

Two more GB10s arrive on 2026-10-20. `node provision` had not changed since 2026-10-03, so it was run with `--dry-run` against the one real second machine (`node-b`, read-only: the probe of §7.1 step 2 over a key login), and read against everything that moved since. What it found, and what changed:

| Found | Change |
|---|---|
| **The probe works on a real machine**: every reading of §16's probe came back as expected from node-b (DGX release, GB10, the recorded version, docker group, lingering, the Avahi file, the AppArmor profile, telemetry off, free disk, the hub, the model folders and the SGLang image by digest) | none |
| **The host key was filed under `~/.ssh/config`'s alias**: node-b's entry sets `HostKeyAlias gx10-3c4d`, so the run's known-hosts file held the key under that name, the fingerprint printed `?`, and pairing would have stopped at "recorded no host key to pin" | `FleetSession` passes `HostKeyAlias=<the host as given>` (a port as `-port-N`, never `[host]:port`), so the key is filed and found under one name whatever the user's config says |
| **A login with an agent holding many keys never reached the password prompt**: ssh offered all seven of this machine's agent keys and sshd's `MaxAuthTries` (6) ended the login ("Too many authentication failures") | `IdentitiesOnly=yes`: only the keys `~/.ssh/config` names for that host, or the default files, are offered; then the password |
| **A fresh unit has no python3-venv** (measured on node-b's first day, 2026-10-08), and `install.sh` over the session has no terminal for `sudo`, so step 4 could only fail on a new unit | the probe reads `venv`; when it is missing, `sudo apt-get install -y python3-venv` runs over the session before the install (the one root command before `node prepare`, with the held password or at a remote terminal) |
| **A bare name such as `spark-1a2b` did not resolve** on this LAN's resolver (only `.local` names do, through mDNS), although §5 offers it | a name that does not resolve is reached as `<name>.local`; a paired node's name is tried as its record's name too, and then by a browse for its id |
| **`--all` connected to each record's remembered address**, the defect of MIGHTLING_NODE §18.10 (an address moved by DHCP, a lane lost) | each paired node is reached by name or by a browse for its id (`NodePairing.resolve`); one that neither finds is reported off the network and its old address is never tried. A paired node is logged into as its record's user unless `--user` is given |
| **The bundle left out `ling-docs` and `ling-signal`** (and later `ling-delegate`), which `install.sh` installs on Linux since 1.6.0 | all are bundled when installed here (`FleetBundle.OPTIONAL_BINARIES` in `dreamference/node/fleet_bundle.py`); a test compares the bundle's list with `install.sh`'s |
| **The dry run said "main-model set, pair, start" for every machine** and nothing about a version to replace | it names only what differs: python3-venv, an install or the version replaced (`this-<digest>` computed without building the bundle), `node prepare` (or that it runs if `host check` fails there), each missing folder and image, SearXNG, the model assignment, the pairing, the start |
| Unchanged and checked: the SGLang v0.5.21 image is still pulled by the node by digest (§7.4); the model gate needs no image of its own (it runs in the engine's); the AppArmor profile is still `node prepare`'s sandbox step; Wi-Fi power saving is still its one NetworkManager change; `node enable --no-web` still writes `node-advertise.json` as the user | none |

Still unrun against a new machine: everything after the probe. Phase 0 (§13) is measured on the first of the two units.

---

## Sources

NVIDIA, all read 2026-10-02:
- [DGX Spark: First boot](https://docs.nvidia.com/dgx/dgx-spark/first-boot.html) (updated Sep 10, 2026)
- [DGX Spark: Custom installation (cloud-init, BaseOS/FastOS repack)](https://docs.nvidia.com/dgx/dgx-spark/enterprise-custom-install.html) (Sep 10, 2026)
- [DGX Spark: PXE boot](https://docs.nvidia.com/dgx/dgx-spark/pxe.html) (Sep 10, 2026)
- [DGX Spark: System recovery](https://docs.nvidia.com/dgx/dgx-spark/system-recovery.html) (Sep 10, 2026)
- [DGX Spark: Fleet lifecycle](https://docs.nvidia.com/dgx/dgx-spark/enterprise-fleet-lifecycle.html) (Sep 10, 2026)
- [DGX Spark: Manageability](https://docs.nvidia.com/dgx/dgx-spark/enterprise-manageability.html) and [Hardware](https://docs.nvidia.com/dgx/dgx-spark/hardware.html) (Sep 10, 2026)
- [DGX Spark: OS and component update](https://docs.nvidia.com/dgx/dgx-spark/os-and-component-update.html) (Sep 10, 2026)
- [DGX Spark: NVIDIA Container Runtime for Docker](https://docs.nvidia.com/dgx/dgx-spark/nvidia-container-runtime-for-docker.html) (Sep 10, 2026)
- [DGX Spark: DGX Dashboard](https://docs.nvidia.com/dgx/dgx-spark/dgx-dashboard.html), [NVIDIA Sync](https://docs.nvidia.com/dgx/dgx-spark/nvidia-sync.html), [Clustering](https://docs.nvidia.com/dgx/dgx-spark/spark-clustering.html) (Sep 10, 2026)
- [NVIDIA Sync: direct connections](https://docs.nvidia.com/sync/latest/direct-connections.html) (Sep 02, 2026)
- [DGX OS 7 user guide: initial setup](https://docs.nvidia.com/dgx/dgx-os-7-user-guide/initial_setup.html) (undated; written for DGX servers)
- [Connect Two Sparks](https://build.nvidia.com/spark/connect-two-sparks) (page shows 7/21/2026), [Multi Sparks Through a Switch](https://build.nvidia.com/spark/multi-sparks-through-switch) (Mar 20, 2026), and their sources in [NVIDIA/dgx-spark-playbooks](https://github.com/NVIDIA/dgx-spark-playbooks) (`discover-sparks`, `connect-to-your-spark`; undated)

Others, read 2026-10-02:
- [Dell Pro Max with GB10](https://www.dell.com/en-us/blog/dell-pro-max-with-gb10-purpose-built-for-ai-developers/) (ships DGX OS 7; undated)
- [dgx-spark-headless-setup](https://github.com/AI-Architect-Lab-333/dgx-spark-headless-setup) (community; SSH on after the wizard on DGX OS 7.5.0, July 2026)
- NVIDIA developer forums: [switching to headless mode](https://forums.developer.nvidia.com/t/switching-to-headless-mode/352366) (Nov 2025); [deployment models](https://forums.developer.nvidia.com/t/deployment-models-and-multi-unit-workload-sharing-for-nvidia-dgx-spark-best-practices/360311) (Feb 2026); [DGX Manager](https://forums.developer.nvidia.com/t/dgx-manager-an-open-source-control-plane-for-your-dgx-spark-cluster-looking-for-testers-feedback/373172); an [Ansible set-up](https://forums.developer.nvidia.com/t/my-dgx-spark-setup-unsloth-qwen36moe-2x-llama-cpp-mtp-pr-ansible-for-easy-mode/369775). None has an NVIDIA staff answer
- [Fleet (fleetdm): managing DGX Spark](https://fleetdm.com/guides/manage-dgx-spark-with-fleet)

**Could not be verified** from a primary source:
- a statement that SSH is on after the wizard, specific to Spark (it is on, on this unit);
- the absence of a BMC on any GB10 unit (none is documented);
- where the repack scripts and the BaseOS ISO are obtained;
- whether PXE reads a cloud-init seed, and from which NIC;
- the recovery image's duration, and whether it is NVIDIA's or the vendor's on an ASUS unit;
- what the shipped OS and the wizard look like on Lenovo, MSI and Gigabyte units;
- what "SOL" means in NVIDIA's consent markers.
