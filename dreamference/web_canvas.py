import http.server
import json
import os
import socketserver
import sys
import threading
from typing import Dict, Any
from dreamference.hardware import detect_gb10_hardware
from dreamference.vllm_server import VLLMServerManager
from dreamference.context_engine import ContextEngine

CANVAS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dreamference Canvas - Pair Programming & Hardware Monitor</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;600&family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
  <style>
    :root {
      --bg-dark: #0a0c10;
      --card-bg: rgba(18, 22, 31, 0.75);
      --card-border: rgba(255, 255, 255, 0.1);
      --accent-green: #76b900; /* NVIDIA Green */
      --accent-teal: #00e5ff;
      --accent-purple: #9d4edd;
      --text-main: #f0f4f8;
      --text-muted: #8b9bb4;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Inter', sans-serif;
      background-color: var(--bg-dark);
      color: var(--text-main);
      background-image: 
        radial-gradient(circle at 15% 15%, rgba(118, 185, 0, 0.08) 0%, transparent 40%),
        radial-gradient(circle at 85% 85%, rgba(0, 229, 255, 0.08) 0%, transparent 40%);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }

    header {
      padding: 1rem 2rem;
      background: rgba(10, 12, 16, 0.9);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--card-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      font-weight: 700;
      font-size: 1.5rem;
      letter-spacing: -0.5px;
      color: var(--accent-green);
    }
    .brand span {
      background: linear-gradient(90deg, #fff, var(--accent-green));
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    .brand-badge {
      background: linear-gradient(135deg, var(--accent-green), var(--accent-teal));
      color: #000;
      padding: 0.2rem 0.6rem;
      border-radius: 6px;
      font-size: 0.75rem;
      font-weight: 700;
      text-transform: uppercase;
    }

    main {
      padding: 2rem;
      display: grid;
      grid-template-columns: 1fr 340px;
      gap: 1.5rem;
      flex: 1;
    }

    .glass-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      backdrop-filter: blur(16px);
      padding: 1.5rem;
      box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
    }

    .card-title {
      font-size: 1.1rem;
      font-weight: 600;
      margin-bottom: 1rem;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    .gauge-container {
      margin-bottom: 1.2rem;
    }

    .gauge-label {
      display: flex;
      justify-content: space-between;
      font-size: 0.85rem;
      color: var(--text-muted);
      margin-bottom: 0.4rem;
    }

    .gauge-bar {
      height: 10px;
      background: rgba(255, 255, 255, 0.08);
      border-radius: 5px;
      overflow: hidden;
    }

    .gauge-fill {
      height: 100%;
      background: linear-gradient(90deg, var(--accent-green), var(--accent-teal));
      width: 0%;
      transition: width 0.5s ease;
    }

    .code-diff-view {
      font-family: 'Fira Code', monospace;
      font-size: 0.85rem;
      background: rgba(0, 0, 0, 0.4);
      padding: 1rem;
      border-radius: 8px;
      border: 1px solid var(--card-border);
      overflow-x: auto;
      white-space: pre-wrap;
      max-height: 400px;
    }

    .diff-add { color: #4eff91; background: rgba(78, 255, 145, 0.1); }
    .diff-del { color: #ff5252; background: rgba(255, 82, 82, 0.1); }

    .mermaid-container {
      background: rgba(0, 0, 0, 0.3);
      padding: 1rem;
      border-radius: 8px;
      min-height: 200px;
      display: flex;
      justify-content: center;
      align-items: center;
    }

    .status-pill {
      display: inline-block;
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background-color: var(--accent-green);
      box-shadow: 0 0 8px var(--accent-green);
      margin-right: 0.4rem;
    }
  </style>
</head>
<body>

  <header>
    <div class="brand">
      <span>⚡ Dreamference</span>
      <span class="brand-badge">NVIDIA GB10</span>
    </div>
    <div style="font-size: 0.85rem; color: var(--text-muted);">
      <span class="status-pill" id="statusPill"></span>
      <span id="statusText">System Ready</span>
    </div>
  </header>

  <main>
    <div style="display: flex; flex-direction: column; gap: 1.5rem;">
      <div class="glass-card">
        <div class="card-title">🧩 Architecture & Agent Execution Stream</div>
        <div class="mermaid-container" id="mermaidDiagram">
          classDiagram
            class GB10Hardware {
              +128GB Unified Memory
              +Blackwell Tensor Cores
            }
            class GooseAgent {
              +ContextEngine AST
              +vLLM Local Server
            }
            GB10Hardware <|-- GooseAgent : Accelerated By
        </div>
      </div>

      <div class="glass-card">
        <div class="card-title">✨ Active Code Proposal / Diff Stream</div>
        <div class="code-diff-view" id="diffView"><span class="diff-del">- old_code()</span>
<span class="diff-add">+ dreamference_gb10_accelerated_run()</span>
  return "100% On-Premise Air-Gapped Execution"</div>
      </div>
    </div>

    <div style="display: flex; flex-direction: column; gap: 1.5rem;">
      <div class="glass-card">
        <div class="card-title">🖥️ GB10 Hardware Monitor</div>

        <div class="gauge-container">
          <div class="gauge-label">
            <span>128GB Unified Memory</span>
            <span id="memVal">0.0 GB</span>
          </div>
          <div class="gauge-bar"><div class="gauge-fill" id="memFill"></div></div>
        </div>

        <div class="gauge-container">
          <div class="gauge-label">
            <span>vLLM KV-Cache Alloc</span>
            <span id="kvVal">0%</span>
          </div>
          <div class="gauge-bar"><div class="gauge-fill" id="kvFill" style="background: linear-gradient(90deg, var(--accent-purple), var(--accent-teal));"></div></div>
        </div>

        <div style="font-size: 0.8rem; color: var(--text-muted); line-height: 1.6; margin-top: 1rem;">
          <div><b>Target GPU:</b> <span id="gpuName">NVIDIA GB10</span></div>
          <div><b>vLLM Host:</b> <span id="vllmHost">http://localhost:8000</span></div>
          <div><b>Active Model:</b> <span id="activeModel">qwen3.5-122b-a10b-nvfp4</span></div>
          <div><b>Indexed Files:</b> <span id="indexedFiles">0</span></div>
        </div>
      </div>
    </div>
  </main>

  <script>
    mermaid.initialize({ startOnLoad: true, theme: 'dark' });

    async function fetchStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        
        const total = data.hardware.total_unified_memory_gb || 128;
        const used = data.hardware.used_memory_gb || 0;
        const pct = Math.min(100, Math.round((used / total) * 100));

        document.getElementById('memVal').innerText = `${used} / ${total} GB (${pct}%)`;
        document.getElementById('memFill').style.width = `${pct}%`;
        document.getElementById('kvVal').innerText = data.vllm.healthy ? "Active (PagedAttn)" : "Inactive";
        document.getElementById('kvFill').style.width = data.vllm.healthy ? "45%" : "0%";

        document.getElementById('gpuName').innerText = data.hardware.gpu_name || "NVIDIA GB10";
        document.getElementById('vllmHost').innerText = data.vllm.host;
        document.getElementById('activeModel').innerText = data.vllm.models[0] || "qwen3.5-122b-a10b-nvfp4";
        document.getElementById('indexedFiles').innerText = data.context.total_indexed_files;
      } catch (e) {
        console.error("Failed to fetch status:", e);
      }
    }

    setInterval(fetchStatus, 3000);
    fetchStatus();
  </script>
</body>
</html>
"""

class CanvasHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress default server logs

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(CANVAS_HTML.encode("utf-8"))

        elif self.path == "/api/status":
            hw = detect_gb10_hardware()
            vllm_mgr = VLLMServerManager()
            vllm_stat = vllm_mgr.get_server_status()
            ctx = ContextEngine()
            ctx_stat = ctx.get_summary() if ctx.load_index() else {"total_indexed_files": 0}

            payload = {
                "hardware": hw,
                "vllm": vllm_stat,
                "context": ctx_stat
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()

def start_web_canvas_server(port: int = 8501, daemon: bool = True) -> threading.Thread:
    """Launches local Dreamference Web Canvas UI on specified port."""
    server = socketserver.TCPServer(("0.0.0.0", port), CanvasHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=daemon)
    thread.start()
    print(f"🌐 Dreamference Web Canvas UI running at: http://localhost:{port}")
    return thread
