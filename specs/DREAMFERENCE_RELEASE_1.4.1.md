# Puffin 1.4.1 — release notes

**Status:** published 2026-10-07 as Latest (`v1.4.1`, workflow run 37540845556: every job passed). Drafted 2026-10-06 from `git log v1.4.0..main`. The text between the two rules is the GitHub release's description.

**Version.** setup.py, `dreamference.__version__`, the MCP server's `serverInfo`, `tauri.conf.json` and `puffin-app`'s Cargo manifest say `1.4.1`; the release workflow stamps the version it is given into setup.py, `tauri.conf.json` and the `puffin` binary in its own checkout.

---

Puffin now covers every GB10 machine, not only the one it was built on, and has moved to **github.com/dreamference/puffin**. The old address, `dgxcoder/dgxcoder`, redirects here, so links, clones and `puffin update` on 1.4.0 keep working.

**Install** on a GB10 (DGX OS 7, or Ubuntu 24.04 with Docker and the NVIDIA Container Toolkit):

```bash
curl -fsSLO https://github.com/dreamference/puffin/releases/latest/download/install.sh
bash install.sh
```

Already on 1.4.0: `puffin update`, and `pip install -U` of the wheel for `puffin-admin`.

## Every GB10 machine

- **Eight machines, one chip.** NVIDIA DGX Spark, Acer Veriton GN100, ASUS Ascent GX10, Dell Pro Max with GB10, Gigabyte AI TOP ATOM, HP ZGX Nano, Lenovo ThinkStation PGX and MSI EdgeXpert all carry the GB10, 128 GB of unified memory and DGX OS 7. The default model (~20 GB of weights) fits the smallest drive any of them ships with, 1 TB.
- **Found without a driver.** `install.sh` and `puffin-admin` recognise a GB10 by its GPU's PCI id (`10de:2e12`) when `nvidia-smi` is missing or fails, as on a fresh install.
- **Only a GB10 counts as one.** A GPU named "Blackwell" or a machine with 100 GB of RAM no longer qualifies, so a discrete Blackwell workstation or a large server is not given GB10 recipes or advertised as a node.
- **`puffin-admin status` names the machine** (DMI vendor and product) and the operating system (DGX OS version over Ubuntu), so a report says whose box it came from.
- **Plain Ubuntu is covered.** `puffin-admin host check` and `host setup` now handle what DGX OS would have had: Docker and the NVIDIA Container Toolkit are explained with their install links, the `docker` group is joined, bubblewrap is installed with its AppArmor profile, and `node enable` installs Avahi when it is missing.
- **zram is not counted as swap,** since its pages live in the memory a model load is short of; `host setup` resizes `/swap.img` even with zram beside it.

Verified on hardware: the ASUS Ascent GX10 on DGX OS 7.5. The other seven machines and plain Ubuntu are covered by tests only.

## Also

- The README's first-start download is ~20 GB for the default model (the 122B fallback is ~70 GB).
- A specification for Puffin on Windows on Arm (the RTX Spark laptops) is in `specs/DREAMFERENCE_PUFFIN_WINDOWS_ARM.md`. Nothing for Windows is built yet.

---
