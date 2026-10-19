# Qwen3.8-Flash-Next: the day-time smoke boot (plan, not yet run)

The first boot of `qwen3.8-flash-next-nvfp4-mtp` (the registry entry on branch `models/flash-next`; background in [flash-next-feasibility-2026-10-19.md](flash-next-feasibility-2026-10-19.md)). It decides whether the trial gets its SWE-bench night. Nothing below has been run.

**Where.** A GB10 with nothing else on its GPU, by day, with someone at hand. Not second-puffin: its GPU is shared, and night 12 holds it on 2026-10-20. The likely machine is one of the owner's two new DGX machines, once paired. Every command names it by node name (`$NODE`), never by address.

**What it measures.** Peak load memory, and from it the load transient that replaces the provisional `load_transient_gb` (8.0). Also MemAvailable while serving and boot time. Then the server-start canaries, a greedy determinism check past 2,048 prompt tokens (vLLM #54521), a long-context needle check, and decode speed.

**Stop rules.** Stop the boot and do not retry with any safety layer relaxed if:
- the PSI watchdog trips;
- the container is OOM-killed;
- the desktop stops responding.

The fixes for those go into the recipe (`gpu_memory_utilization`, `max_model_len`), never into the gates.

## 0. Before the slot (any time that day)

On this machine:

```bash
NODE=<the new DGX's node name>          # as `ling-admin node list` shows it
ssh "$NODE" true                        # resolves by name (mDNS); never an IP
# The branch's code goes to the node as a separate copy, beside whatever it runs normally.
git -C /home/stan/PycharmProjects/dgxcoder-flash-next fetch origin models/flash-next
git -C /home/stan/PycharmProjects/dgxcoder-flash-next archive --format=tar origin/models/flash-next \
  | ssh "$NODE" 'rm -rf ~/flash-next-trial && mkdir -p ~/flash-next-trial && tar -C ~/flash-next-trial -xf -'
```

On the node (`ssh "$NODE"`):

```bash
python3 -m venv ~/flash-next-trial/.venv
~/flash-next-trial/.venv/bin/pip install -q -e ~/flash-next-trial
LA=~/flash-next-trial/.venv/bin/ling-admin
MODEL=qwen3.8-flash-next-nvfp4-mtp
REPO=local-inference-lab/Qwen3.8-Flash-Next-NVFP4
REV=6c01ac378d22f6652c9f392c87700496eca8e5dc
IMAGE=vllm/vllm-openai@sha256:1ff6716fe240a922d0d518b8884261e25b0c02eda8e2ce90888e364978249cad

# The checkpoint at the pinned commit (106.4 GB, about 16 min at 110 MB/s). The node downloads it
# itself, so the boot does not depend on second-puffin's copy.
~/flash-next-trial/.venv/bin/hf download "$REPO" --revision "$REV"
du -shL ~/.cache/huggingface/hub/models--local-inference-lab--Qwen3.8-Flash-Next-NVFP4/snapshots/$REV   # ~99G

# The image, pulled ahead (the arm64 entry of the index is c6c3c615…).
docker pull "$IMAGE" && docker image inspect -f '{{.Architecture}}' "$IMAGE"           # arm64

# The production chat-template patches must match this checkpoint's template (prints a path, not None).
~/flash-next-trial/.venv/bin/python -c "
from dreamference.hardware import get_model_launch_overrides as g
from dreamference.vllm_server.chat_template_patcher import ChatTemplatePatcher as P
r = g('$MODEL'); print(P.prepare('$REPO', r['revision'], r['chat_template_patches']))"

# The command that will run, for review (prints only).
~/flash-next-trial/.venv/bin/python -c "
from dreamference.vllm_server import VLLMServerManager
print(' '.join(VLLMServerManager().build_launch_command(model='$MODEL', gate=False)))"
```

Review the printed command for the following:
- `vllm serve local-inference-lab/Qwen3.8-Flash-Next-NVFP4` with `--revision`/`--tokenizer-revision 6c01ac37…`;
- `--max-model-len 131072`, `--gpu-memory-utilization 0.86` and `--kv-cache-dtype fp8`;
- `--speculative-config {"method": "mtp", "num_speculative_tokens": 3}`;
- `--chat-template /root/.cache/dreamference/chat-templates/…`;
- `--mamba-cache-mode align` and `--max-num-seqs 4`;
- `--memory=110g`, which is the cgroup cap clamped at total − 12 GiB;
- no `--quantization`.

## 1. Free the machine

On the node:

```bash
$LA host check                                       # every host-safety check passes; else `ling-admin host setup`
docker ps --format '{{.Names}} {{.Image}}'           # note what runs, to restore it in §7
nvidia-smi --query-compute-apps=pid,name,used_memory --format=csv   # empty: nothing else on the GPU
ling-admin server stop                               # only if a model server runs (the node's own install)
loginctl list-sessions                               # close any desktop session on the node: it is the largest
                                                     # thing besides the model, and the budget has ~15 GiB slack
grep -E 'MemTotal|MemAvailable' /proc/meminfo        # MemAvailable must exceed 0.86 x MemTotal + 8 GiB (~112.6 GiB)
```

## 2. Start the memory recorder

```bash
( while :; do
    printf '%s %s %s\n' "$(date +%s)" "$(awk '/^MemAvailable/{print $2}' /proc/meminfo)" \
      "$(awk '/^full/{sub("avg10=","",$2); print $2}' /proc/pressure/memory)"
    sleep 1
  done ) > ~/flash-next-mem.log 2>&1 &
echo $! > ~/flash-next-mem.pid                       # stopped in §7 by this pid, never by pattern
```

## 3. Boot, and measure the load

```bash
date +%s > ~/flash-next-boot-start
$LA server start --model "$MODEL" --no-gate 2>&1 | tee ~/flash-next-boot.log
date +%s > ~/flash-next-boot-end
```

`server start` runs every gate before the load:
- `check_host_safety()`;
- the arena checks;
- the start gate, on this model's 8.0 GiB figure, whose refusal names "this model's own figure, load_transient_gb".

After the load it runs the post-launch canaries:
- the correctness canary;
- the NVFP4 canary, because the alias contains `nvfp4` (all-`!` output is the SM121 symptom);
- the Responses-API tool-call canary.

The PSI watchdog guards the load throughout. Keep the whole log.

Read the result:

```bash
# Boot time.
echo $(( $(cat ~/flash-next-boot-end) - $(cat ~/flash-next-boot-start) )) s
# vLLM's own accounting: weights, KV pool, concurrency, speculation.
docker logs dreamference-vllm-8000 2>&1 | grep -E 'Model loading took|KV cache|Maximum concurrency|non_torch|requested memory|speculative|mtp' | tail -20
# The container's own peak against its cap.
CG=/sys/fs/cgroup/system.slice/docker-$(docker inspect -f '{{.Id}}' dreamference-vllm-8000).scope
echo peak $(( $(cat $CG/memory.peak) >> 30 )) GiB of cap $(( $(cat $CG/memory.max) >> 30 )) GiB
docker inspect -f 'OOMKilled={{.State.OOMKilled}} Restarts={{.RestartCount}}' dreamference-vllm-8000
# The load transient: the drop in MemAvailable from before the launch to its trough, less the arena.
awk -v start="$(cat ~/flash-next-boot-start)" -v end="$(cat ~/flash-next-boot-end)" \
    -v total="$(awk '/^MemTotal/{print $2}' /proc/meminfo)" '
  $1 < start { before = $2 } $1 >= start && $1 <= end && (min == "" || $2 < min) { min = $2 }
  $1 >= start && $1 <= end && $3 > psi { psi = $3 }
  END { g = 1048576; arena = 0.86 * total / g
        printf "before %.1f GiB, trough %.1f GiB, arena %.1f GiB, transient %.1f GiB, max PSI full avg10 %s\n",
               before / g, min / g, arena, (before - min) / g - arena, psi }' ~/flash-next-mem.log
```

**Recording the figure.** Set `load_transient_gb` in the registry entry to the measured transient plus 2 GiB, rounded up, and drop the PROVISIONAL comment. If that is above 8.0, the gate becomes stricter, as intended. If the result then needs more than the machine has free, the trial waits for vLLM's disk offload of the n-gram table (#59958), which frees 27 GiB. The gate does not move.

**If the boot fails** (stop rules above first), the cause decides the fix:

| Symptom | Recipe change, then boot again |
| --- | --- |
| Not enough KV cache for `max_model_len` | `max_model_len` 65536 |
| Prefix-caching or mamba-alignment error at start | `enable_prefix_caching: False`, drop `--mamba-cache-mode align` |
| MTP error at start | `speculative_config` removed, to isolate it (speed then 26–33 tok/s; the trial is not worth a night that way) |
| OOM-killed, peak at the cap | `gpu_memory_utilization` 0.84 |
| Weights do not load (#56273 path) | none: the image is wrong; stop |

## 4. Memory while serving

```bash
sleep 60; grep MemAvailable /proc/meminfo            # idle, after the warm-up
```

The SWE-bench night needs at least 16 GiB here: the 8 GiB reserve plus one task container's `task_memory` of 8G. Below that, the night runs with `[swe_bench] task_memory = "6G"`, or not at all. Two instances at once, the `swe_bench_max_parallel` 1 raised to 2, need at least 24 GiB.

## 5. Correctness

```bash
cd ~/flash-next-trial
# Greedy determinism: a 1,500-token control under the indexer budget and a 6,000-token prompt over it,
# five runs each, one at a time, then four at once (batching changes kernels).
python3 scripts/long_prompt_probe.py determinism --runs 5 --out ~/flash-next-determinism.json
python3 scripts/long_prompt_probe.py determinism --runs 4 --parallel 4 --out ~/flash-next-determinism-p4.json
python3 scripts/long_prompt_probe.py determinism --tokens 40000 --runs 3 --out ~/flash-next-determinism-40k.json
# Needle retrieval at 120K tokens, at 10%, 50% and 90% depth.
python3 scripts/long_prompt_probe.py needle --tokens 120000 --out ~/flash-next-needle.json
grep MemAvailable /proc/meminfo                      # after a 120K prefill: the serving trough
# A real agent turn through the launcher (by the node's own ling).
DREAMFERENCE_VLLM_HOST=http://localhost:8000 ling exec "Write a Python function that parses ISO-8601 durations, with tests, in /tmp/flash-next-task/; run the tests."
```

**The 200K needle** needs `max_model_len` 262144, the native context. Try it only if the KV line in §3 shows a pool of at least 262,144 tokens. In that case, set it in `~/flash-next-trial`'s registry entry and boot again (§3, without re-measuring), then run:

```bash
python3 scripts/long_prompt_probe.py needle --tokens 200000 --out ~/flash-next-needle-200k.json
```

Commit the change to the branch only if it passes.

## 6. Speed

```bash
python3 scripts/decode_speed.py --runs 3 --out ~/flash-next-speed.json
docker logs dreamference-vllm-8000 2>&1 | grep -iE 'acceptance|accepted' | tail -5      # MTP acceptance
```

Production measured prose 32.0, code 68.6 and JSON 84.1 tok/s with this script on 2026-10-09 (`specs/DREAMFERENCE_INFERENCE.md` §5). The feasibility note expects Flash-Next at 45–55 on prose.

## 7. Restore the machine

```bash
$LA server stop
kill "$(cat ~/flash-next-mem.pid)"
# Restart what §1 found running (the node's own install), and check it answers.
ling-admin server start && curl -s localhost:8000/v1/models | head -c 300
ls -la ~/flash-next-*                               # the results, kept on the node
```

## 8. What decides the night

The trial gets its SWE-bench night only if all of the following hold:
- **The boot**: the boot is clean, with no watchdog trip, no OOM kill and no restart. Every canary passes.
- **Determinism**: both determinism cases are identical and uncorrupted, sequential and four at once, at 6K and 40K.
- **Needle**: the 120K needle is found at all three depths.
- **Memory**: MemAvailable while serving is at least 16 GiB.
- **Speed**: decode beats production on prose and code with this script.

A determinism failure over the budget, with the control clean, is vLLM #54521. It ends the trial on this image, because every agent prompt is over the budget.

The results go into the feasibility note and the registry entry's `notes`. The measured `load_transient_gb` replaces the provisional one. The go comes after that, from the owner.
