import re

with open('specs/DREAMFERENCE_PREFIX_CACHE.md', 'r') as f:
    content = f.read()

new_sections = """### 1.7 Architectural Contrast: Hybrid vs. Pure Attention (Nemotron)

A frequent question is whether pure-Attention models (like `Llama-3.1-Nemotron-70B`) handle prefix caching better than this Qwen Hybrid stack. The answer is **yes, vastly better and simpler**, but with a long-context trade-off.

Because Nemotron lacks GDN (Mamba) layers, it bypasses "Coordination Hell" entirely. It uses standard PagedAttention, which natively caches in tiny, highly efficient **16-token blocks**.
* **No Grid Lock-in:** A cache hit can happen precisely at token 16, 32, 48, etc., instead of waiting for a massive 4480-token boundary.
* **No 157 MB Checkpoints:** It only stores standard KV tensors, which take up very little space per token, avoiding massive spikes in LRU cache pressure.
* **Zero Unification Waste:** Without the need to scale blocks up to match a Drafter's LCM, Nemotron wastes almost zero memory on block padding.

**The Trade-Off:** While Nemotron's caching is infinitely cleaner, pure Attention KV cache *grows linearly* with context size. At 100,000 tokens, a pure Attention model requires massive amounts of VRAM just to hold the active KV cache for a single request. Qwen uses GDN because the GDN state size is *fixed* (~157 MB) whether you are at 1,000 or 100,000 tokens, making it theoretically far more memory-efficient during extreme long-context generation.

### 1.8 Quantifying the Padding Waste: Is it still worth it?

Given that Page Unification forces Attention blocks into massive 4480-token buckets, how much memory is actually wasted, and should prefix caching be disabled to reclaim it?

**The Math on Padding Waste (Internal Fragmentation):**
* As established in Section 1.4, Attention KV requires roughly ~121 KB per token. Therefore, one unified 4480-token bucket consumes **~542 MB** of VRAM.
* Prompts rarely end exactly on a multiple of 4480. If your prompt is 4,481 tokens long, vLLM must allocate a full second bucket (542 MB) just to hold that 1 extra token.
* On average, the final "tail" bucket of any sequence is half-empty, wasting roughly **~271 MB per active sequence**.
* With 8 concurrent agent streams, plus a few older abandoned tails sitting in the LRU queue, the server wastes roughly **2.5 to 3.5 GB of VRAM** globally on empty padding (roughly 20-25% of the ~13 GB KV pool).

**The Verdict: Absolutely DO NOT turn off prefix caching.**
Wasting 3 GB of VRAM on empty padding is a cheap tax to pay for the massive speedup in agentic workflows. Agents operate in loops, repeatedly sending the exact same 10,000-token history back to the model with just a few new tool results appended. 
* **Without Caching:** The GPU must run a full, cold mathematical forward pass on all 10,000 tokens every single step. Time-To-First-Token (TTFT) takes **3 to 5 seconds** while the GPU grinds through historical text.
* **With Caching:** The engine hits the 8960-token boundary, instantly loads the state from memory, and computes only the delta. TTFT drops to **~0.2 seconds**. 

Until vLLM rewrites its kernels to support decoupled hybrid caching natively, paying the 3 GB memory tax is mandatory to keep the engine lightning fast.

---

## 2. The two defects"""

content = content.replace(
    "---\n\n## 2. The two defects",
    new_sections
)

with open('specs/DREAMFERENCE_PREFIX_CACHE.md', 'w') as f:
    f.write(content)
