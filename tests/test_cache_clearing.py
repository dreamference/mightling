"""`ling-admin clear` removes model weights only, never what sits next to them.

Both commands used to delete the *parent* of the directory they named: `clear tensorize-cache`
removed all of `~/.cache/dreamference` (the ling build cache, vLLM's compile cache), and
`clear model-cache` also removed all of `~/.cache/huggingface`, including the login token.
"""

from dreamference.hardware.model_downloader import ModelDownloader


def _layout(tmp_path, monkeypatch):
    hf = tmp_path / "huggingface"
    dream = tmp_path / "dreamference"
    for path in [hf / "hub" / "models--x", dream / "tensorizer" / "m", dream / "mightling-codex" / "target", dream / "vllm"]:
        path.mkdir(parents=True)
        (path / "f").write_text("x")
    (hf / "token").write_text("hf_secret")
    monkeypatch.setenv("HF_HOME", str(hf))
    monkeypatch.setattr(ModelDownloader, "TENSORIZER_CACHE_HOME", dream / "tensorizer")
    return hf, dream


def test_clearing_the_tensorizer_cache_keeps_the_build_and_compile_caches(tmp_path, monkeypatch):
    hf, dream = _layout(tmp_path, monkeypatch)
    assert ModelDownloader.clear_tensorizer_cache() is True
    assert not (dream / "tensorizer").exists()
    assert (dream / "mightling-codex" / "target" / "f").exists()
    assert (dream / "vllm" / "f").exists()
    assert (hf / "hub" / "models--x").exists()


def test_clearing_the_model_cache_keeps_the_huggingface_token(tmp_path, monkeypatch):
    hf, dream = _layout(tmp_path, monkeypatch)
    assert ModelDownloader.clear_cache() is True
    assert not (hf / "hub").exists() and not (dream / "tensorizer").exists()
    assert (hf / "token").read_text() == "hf_secret"
    assert (dream / "mightling-codex" / "target" / "f").exists()


def test_a_directory_that_cannot_be_removed_is_reported_not_claimed(tmp_path, monkeypatch):
    hf, dream = _layout(tmp_path, monkeypatch)
    monkeypatch.setattr("shutil.rmtree", lambda *a, **k: None)
    assert ModelDownloader.clear_tensorizer_cache() is False


def test_paths_resolved_from_home_at_import_are_redirected_in_tests():
    # conftest re-points them; without that, a test that ran start_server stamped the real
    # compile-signature file and the next real `server start` discarded the torch.compile cache.
    import os
    from conftest import REAL_HOME
    from dreamference.runner import codex_branded_builder
    from dreamference.vllm_server import vllm_server_manager

    for path in (vllm_server_manager.VLLM_CACHE_HOME, codex_branded_builder.INSTALL_DIR, codex_branded_builder.PATH_LINK):
        assert not str(path).startswith(REAL_HOME + os.sep), path
        assert str(path).startswith(os.environ["HOME"]), path
