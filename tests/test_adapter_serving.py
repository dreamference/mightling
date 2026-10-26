"""Serving adapters: the recipe's flags, the runtime patch, the gate's list, the gate's rules.

specs/DREAMFERENCE_MIGHTLING_ADAPTERS.md §16 (Gate, Recipe). No docker, no engine: the image's file
comes from a replaced `docker` call, and the engine behind the gate is a stand-in on a scratch port.
"""

import asyncio
import hashlib
import json
import pathlib
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from dreamference.hardware.adapter_store import AdapterStore
from dreamference.hardware.model_downloader import ModelDownloader
from dreamference.hardware.model_matrix_registry import DEFAULT_MODEL_ALIAS, QWEN38_LORA_RECIPE
from dreamference.vllm_server.adapter_serving import AdapterServing
from dreamference.vllm_server.model_gate import ModelGate
from dreamference.vllm_server.model_gate_adapters import KEY_FILE, LIST_FILE, ModelGateAdapters
from dreamference.vllm_server.model_gate_service import ModelGateService
from dreamference.vllm_server.sglang_runtime_patcher import SGLangRuntimePatcher
from dreamference.vllm_server.vllm_server_manager import VLLMServerManager

BASE = "RadixArk/Qwen3.8-27B-NVFP4"
MINIMA = "qwen3.8-27b-minima-nvfp4-dflash2"
COMMIT = "2" * 40


def install(name="rust", base_revision=None, files=None):
    """An installed adapter whose files exist in the test's hub cache."""
    from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry

    snapshot = f"models--dreamference--mightling-{name}/snapshots/{COMMIT}"
    folder = ModelDownloader.get_hf_cache_dir() / snapshot
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "adapter_model.safetensors").write_bytes(b"x" * (3 << 20))
    digest = "sha256:" + hashlib.sha256(b"x" * (3 << 20)).hexdigest()
    entry = {
        "name": name,
        "version": "1.0.0",
        "display_name": name.title(),
        "description": "d",
        "trust": "dreamference",
        "repo": f"dreamference/mightling-{name}",
        "commit": COMMIT,
        "snapshot": snapshot,
        "manifest_sha256": "sha256:" + "0" * 64,
        "base_repo": BASE,
        "base_revision": base_revision
        or ModelMatrixRegistry.get_launch_overrides(DEFAULT_MODEL_ALIAS)["revision"],
        "files": files or {"adapter_model.safetensors": digest},
    }
    AdapterStore.put(entry)
    return entry


@pytest.fixture
def verified(monkeypatch):
    """Every installed adapter passes its check (the checks have their own tests)."""
    AdapterServing.checked.clear()
    monkeypatch.setattr(
        "dreamference.hardware.adapter_installer.AdapterInstaller.verify",
        classmethod(lambda cls, entry, base: None),
    )


def memory_cap(cmd):
    return int(
        next(a for a in cmd if a.startswith("--memory=")).removeprefix("--memory=").rstrip("g")
    )


def flag(cmd, name):
    return cmd[cmd.index(name) + 1]


# -- the recipe ----------------------------------------------------------------------------------


def test_without_an_installed_adapter_the_launch_line_is_unchanged(verified):
    mgr = VLLMServerManager()
    assert "--enable-lora" not in mgr.build_launch_command(model=DEFAULT_MODEL_ALIAS, gate=True)


def test_an_installed_adapter_turns_on_the_recipes_lora_flags_behind_the_gate(verified):
    install()
    mgr = VLLMServerManager()
    plain = mgr.build_launch_command(model=DEFAULT_MODEL_ALIAS, gate=True, serve_adapters=False)
    cmd = mgr.build_launch_command(model=DEFAULT_MODEL_ALIAS, gate=True)
    assert flag(cmd, "--max-lora-rank") == str(QWEN38_LORA_RECIPE["max_lora_rank"]) == "16"
    assert (flag(cmd, "--max-loras-per-batch"), flag(cmd, "--max-loaded-loras")) == ("4", "8")
    assert "--lora-strict-loading" in cmd and "--lora-target-modules" not in cmd
    assert flag(cmd, "--lora-paths") == (
        f"rust=/root/.cache/huggingface/hub/models--dreamference--mightling-rust/snapshots/{COMMIT}"
    )
    assert flag(cmd, "--api-key") == AdapterServing.engine_key()
    assert (AdapterServing.gate_dir() / KEY_FILE).stat().st_mode & 0o777 == 0o600
    image = next(a for a in cmd if a.startswith("lmsysorg/sglang@sha256:"))
    mount = cmd[cmd.index(image) - 1]
    assert mount.endswith(
        "/sgl-workspace/sglang/python/sglang/srt/entrypoints/openai/serving_responses.py:ro"
    )
    # The loaded adapters are host memory the container's cap counts.
    assert memory_cap(cmd) == memory_cap(plain) + 1
    # Behind no gate, or with a caller's API key, nothing is served: the engine's key is the gate's.
    assert "--enable-lora" not in mgr.build_launch_command(model=DEFAULT_MODEL_ALIAS)
    assert "--enable-lora" not in mgr.build_launch_command(
        model=DEFAULT_MODEL_ALIAS, gate=True, api_key="k"
    )


def test_minima_never_serves_adapters(verified):
    install(base_revision="16e768e7d0461b0b86e565ecedd08a24eca53e9a")
    assert "--enable-lora" not in VLLMServerManager().build_launch_command(model=MINIMA, gate=True)


def test_an_adapter_that_fails_its_check_is_not_served(monkeypatch, capsys):
    AdapterServing.checked.clear()
    install()
    monkeypatch.setattr(
        "dreamference.hardware.adapter_installer.AdapterInstaller.verify",
        classmethod(lambda cls, entry, base: "adapter_model.safetensors does not match"),
    )
    assert "--enable-lora" not in VLLMServerManager().build_launch_command(
        model=DEFAULT_MODEL_ALIAS, gate=True
    )
    assert "rust is not served" in capsys.readouterr().out


# -- the runtime patch ---------------------------------------------------------------------------


ORIGINAL = """        x = GenerateReqInput(
                        require_reasoning=require_reasoning,
                    )

                    generator = self._generate_with_builtin_tools(
"""


def patch_for(text):
    return {
        "path": "/sgl/serving_responses.py",
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "substitutions": [
            [
                "require_reasoning=require_reasoning,\n",
                "require_reasoning=require_reasoning,\n    lora_path=None,\n",
            ]
        ],
    }


@pytest.fixture
def image_file(monkeypatch):
    calls = []

    def docker(cls, command):
        calls.append(command[:2])

        class Done:
            returncode = 0
            stdout = "container-id\n"

        if command[1] == "cp":
            with pathlib.Path(command[3]).open("w", encoding="utf-8") as handle:
                handle.write(ORIGINAL)
        return Done()

    monkeypatch.setattr(SGLangRuntimePatcher, "_docker", classmethod(docker))
    return calls


def test_the_runtime_patch_is_applied_to_the_images_own_file_and_mounted_read_only(image_file):
    patch = patch_for(ORIGINAL)
    assert SGLangRuntimePatcher.prepare("image@sha256:x", [patch]) is None
    written = SGLangRuntimePatcher.host_dir() / SGLangRuntimePatcher.file_name(patch)
    assert "lora_path=None" in written.read_text()
    assert image_file == [["docker", "create"], ["docker", "cp"], ["docker", "rm"]]
    assert SGLangRuntimePatcher.mount_args([patch]) == [
        "-v",
        f"{written}:/sgl/serving_responses.py:ro",
    ]


def test_a_file_that_is_not_the_one_the_patch_was_written_for_is_never_patched(image_file):
    assert "not the file" in SGLangRuntimePatcher.prepare("image", [patch_for("another file")])
    stale = dict(patch_for(ORIGINAL), substitutions=[["no such anchor", "x"]])
    assert "no longer match" in SGLangRuntimePatcher.prepare("image", [stale])


def test_the_recipes_patch_passes_lora_path_at_both_places_responses_builds_a_request():
    patch = QWEN38_LORA_RECIPE["runtime_patches"][0]
    assert patch["path"].endswith("entrypoints/openai/serving_responses.py")
    replacements = [new for _, new in patch["substitutions"]]
    assert "lora_path=self._resolve_lora_path(request.model, None)" in replacements[0]
    assert "lora_path=adapted_request.lora_path" in replacements[1]


def test_prepare_writes_the_gates_list_or_removes_it(verified, monkeypatch):
    monkeypatch.setattr(
        SGLangRuntimePatcher, "prepare", classmethod(lambda cls, image, patches: None)
    )
    assert AdapterServing.prepare(DEFAULT_MODEL_ALIAS, "image", gated=True) is True
    assert not (AdapterServing.gate_dir() / LIST_FILE).exists()
    install()
    assert AdapterServing.prepare(DEFAULT_MODEL_ALIAS, "image", gated=True) is True
    listing = json.loads((AdapterServing.gate_dir() / LIST_FILE).read_text())
    assert listing["base"] == BASE and listing["max_loaded"] == 8
    assert listing["adapters"]["rust"]["path"].startswith(
        "/root/.cache/huggingface/hub/models--dreamference"
    )
    assert ModelGate.run_command(8000, "image")[
        ModelGate.run_command(8000, "image").index("--entrypoint") - 1
    ].endswith(":/root/.cache/huggingface/hub:ro")
    AdapterServing.withdraw("rust")
    assert json.loads((AdapterServing.gate_dir() / LIST_FILE).read_text())["adapters"] == {}
    monkeypatch.setattr(
        SGLangRuntimePatcher, "prepare", classmethod(lambda cls, image, patches: "no file")
    )
    assert AdapterServing.prepare(DEFAULT_MODEL_ALIAS, "image", gated=True) is False
    assert not (AdapterServing.gate_dir() / LIST_FILE).exists()


# -- the gate's rules ----------------------------------------------------------------------------


LISTING = {
    "base": BASE,
    "max_loaded": 2,
    "adapters": {
        "rust": {
            "path": "/x",
            "files": {},
            "trust": "dreamference",
            "display_name": "Rust",
            "version": "1.0.0",
        }
    },
}


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"model": BASE}, None),
        ({"model": f"{BASE}+rust"}, "rust"),
        ({"model": f"{BASE}+cpp"}, (404, "adapter_not_installed")),
        ({"model": "Other/Model+rust"}, (404, "adapter_base_mismatch")),
        ({"model": f"{BASE}:rust"}, (400, "adapter_not_allowed")),
        ({"model": BASE, "lora_path": "/etc"}, (400, "adapter_not_allowed")),
    ],
)
def test_the_gate_decides_what_a_request_names(body, expected):
    decision = ModelGateAdapters("/nonexistent", ("127.0.0.1", 9)).check(body, LISTING)
    if isinstance(decision, tuple):
        decision = (decision[0], decision[1]["error"]["code"])
    assert decision == expected


def test_the_model_list_shows_the_installed_adapters_and_never_the_engines_bare_names():
    engine = json.dumps(
        {
            "object": "list",
            "data": [
                {
                    "id": BASE,
                    "root": BASE,
                    "max_model_len": 262144,
                    "owned_by": "sglang",
                    "created": 1,
                },
                {"id": "rust", "root": "/x", "parent": BASE, "max_model_len": None},
            ],
        }
    ).encode()
    data = ModelGateAdapters.models(engine, LISTING)["data"]
    assert [entry["id"] for entry in data] == [BASE, f"{BASE}+rust"]
    assert data[1] | {"mightling_adapter": None} == {
        "id": f"{BASE}+rust",
        "object": "model",
        "created": 1,
        "owned_by": "sglang",
        "root": BASE,
        "parent": BASE,
        "max_model_len": 262144,
        "mightling_adapter": None,
    }
    assert data[1]["mightling_adapter"] == {
        "name": "rust",
        "version": "1.0.0",
        "display_name": "Rust",
        "description": None,
        "trust": "dreamference",
    }


@pytest.mark.parametrize("cut", range(1, 30))
def test_an_id_split_across_reads_is_still_swapped(cut):
    stream = b'data: {"model":"' + BASE.encode() + b':rust","x":1}\n\n'
    out, carry = ModelGateAdapters.replace_stream(
        stream[:cut], b"", f"{BASE}:rust".encode(), f"{BASE}+rust".encode()
    )
    more, carry = ModelGateAdapters.replace_stream(
        stream[cut:], carry, f"{BASE}:rust".encode(), f"{BASE}+rust".encode()
    )
    assert out + more + carry == stream.replace(b":rust", b"+rust")


# -- the gate, live ------------------------------------------------------------------------------


class Engine(BaseHTTPRequestHandler):
    """A stand-in SGLang: its model list, the load endpoints and a streamed /v1/responses."""

    protocol_version = "HTTP/1.1"
    seen: ClassVar[list] = []
    loaded: ClassVar[list] = []

    def log_message(self, *args):
        pass

    def answer(self, body, content_type="application/json"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Engine.seen.append(("GET", self.path, self.headers.get("Authorization")))
        data = [{"id": BASE, "root": BASE, "max_model_len": 262144}]
        data += [{"id": name, "parent": BASE} for name in Engine.loaded]
        self.answer(json.dumps({"data": data}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        Engine.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
        if self.path == "/load_lora_adapter":
            Engine.loaded.append(body["lora_name"])
        elif self.path == "/unload_lora_adapter":
            Engine.loaded.remove(body["lora_name"])
        self.answer(f'data: {{"model":"{body.get("model")}"}}\n\n'.encode(), "text/event-stream")


@pytest.fixture
def gate(tmp_path):
    Engine.seen, Engine.loaded = [], []
    engine = ThreadingHTTPServer(("127.0.0.1", 0), Engine)
    threading.Thread(target=engine.serve_forever, daemon=True).start()
    state = tmp_path / "gate"
    state.mkdir()
    folders = {}
    for name in ("rust", "cpp", "ts"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "adapter_model.safetensors").write_bytes(name.encode())
        folders[name] = {
            "path": str(folder),
            "trust": "dreamference",
            "display_name": name,
            "version": "1.0.0",
            "files": {
                "adapter_model.safetensors": "sha256:" + hashlib.sha256(name.encode()).hexdigest()
            },
        }
    (state / LIST_FILE).write_text(json.dumps({"base": BASE, "max_loaded": 2, "adapters": folders}))
    (state / KEY_FILE).write_text("engine-secret\n")
    loop = asyncio.new_event_loop()
    service = ModelGateService(
        ("127.0.0.1", 0), ("127.0.0.1", engine.server_address[1]), str(state)
    )
    port = loop.run_until_complete(service.start())
    threading.Thread(target=loop.run_forever, daemon=True).start()
    yield {"port": port, "folders": folders, "tmp": tmp_path}
    loop.call_soon_threadsafe(service.server.close)
    loop.call_soon_threadsafe(loop.stop)
    engine.shutdown()


def call(port, path, body=None, method="POST"):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Authorization": "Bearer client"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.read()


def test_an_adapter_is_loaded_once_from_the_list_and_its_id_rewritten_both_ways(gate):
    answer = call(gate["port"], "/v1/responses", {"model": f"{BASE}+rust", "input": "hi"})
    assert answer == f'data: {{"model":"{BASE}+rust"}}\n\n'.encode()
    loads = [entry for entry in Engine.seen if entry[1] == "/load_lora_adapter"]
    assert [entry[3] for entry in loads] == [
        {"lora_name": "rust", "lora_path": gate["folders"]["rust"]["path"]}
    ]
    forwarded = [entry for entry in Engine.seen if entry[1] == "/v1/responses"]
    assert forwarded[-1][3]["model"] == f"{BASE}:rust"
    assert {entry[2] for entry in Engine.seen} == {"Bearer engine-secret"}
    call(gate["port"], "/v1/responses", {"model": f"{BASE}+rust", "input": "again"})
    assert len([entry for entry in Engine.seen if entry[1] == "/load_lora_adapter"]) == 1


def test_past_the_engines_limit_the_least_recently_used_adapter_is_unloaded(gate):
    for name in ("rust", "cpp", "rust", "ts"):
        call(gate["port"], "/v1/responses", {"model": f"{BASE}+{name}"})
    unloads = [entry[3]["lora_name"] for entry in Engine.seen if entry[1] == "/unload_lora_adapter"]
    assert unloads == ["cpp"] and sorted(Engine.loaded) == ["rust", "ts"]


def test_an_adapter_whose_files_changed_is_refused_and_never_loaded(gate):
    (gate["tmp"] / "rust" / "adapter_model.safetensors").write_bytes(b"tampered")
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(gate["port"], "/v1/responses", {"model": f"{BASE}+rust"})
    assert (
        refused.value.code == 503
        and json.load(refused.value)["error"]["code"] == "adapter_unavailable"
    )
    assert not [entry for entry in Engine.seen if entry[1] == "/load_lora_adapter"]


@pytest.mark.parametrize(
    "path", ["/load_lora_adapter", "/load_lora_adapter_from_tensors", "/unload_lora_adapter"]
)
def test_the_load_endpoints_are_refused_on_the_public_port(gate, path):
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(gate["port"], path, {"lora_name": "evil", "lora_path": "/tmp/evil"})
    assert refused.value.code == 403
    assert not [entry for entry in Engine.seen if entry[1] == path]


def test_an_unknown_adapter_is_a_404_and_the_engine_never_sees_it(gate):
    with pytest.raises(urllib.error.HTTPError) as refused:
        call(gate["port"], "/v1/responses", {"model": f"{BASE}+evil"})
    assert (
        refused.value.code == 404
        and json.load(refused.value)["error"]["code"] == "adapter_not_installed"
    )
    assert not [entry for entry in Engine.seen if entry[0] == "POST"]


def test_the_gates_model_list_names_the_adapters_as_base_plus_name(gate):
    Engine.loaded = ["rust"]
    ids = [
        entry["id"] for entry in json.loads(call(gate["port"], "/v1/models", method="GET"))["data"]
    ]
    assert ids == [BASE, f"{BASE}+cpp", f"{BASE}+rust", f"{BASE}+ts"]
