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


def test_a_hub_kept_on_another_disk_through_a_link_is_emptied_and_stays_linked(tmp_path, monkeypatch):
    # `rmtree` refuses a link, and with ignore_errors said nothing: nothing was cleared, and the
    # message blamed root-owned files and suggested `sudo rm -rf`.
    hf, _dream = _layout(tmp_path, monkeypatch)
    disk = tmp_path / "big-disk" / "hub"
    disk.parent.mkdir()
    (hf / "hub").rename(disk)
    (hf / "hub").symlink_to(disk)
    assert ModelDownloader.clear_cache() is True
    assert (hf / "hub").is_symlink() and disk.is_dir() and list(disk.iterdir()) == []
    assert (hf / "token").read_text() == "hf_secret"


def test_the_hub_cli_fallback_gets_the_token_in_its_environment_not_its_argv(monkeypatch):
    # An argument is readable by every user of the machine through `ps` and /proc.
    import subprocess

    import huggingface_hub

    def no_library(**kwargs):
        raise RuntimeError("offline")

    calls = []
    monkeypatch.setattr(ModelDownloader, "is_model_downloaded", classmethod(lambda cls, *a: False))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", no_library)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(subprocess, "run", run)
    ModelDownloader.download_model("org/model", hf_token="hf_argv_secret")
    ((cmd, kw),) = calls
    assert "hf_argv_secret" not in " ".join(cmd)
    assert kw["env"]["HF_TOKEN"] == "hf_argv_secret"


def test_a_linked_cache_that_cannot_be_emptied_is_told_how_to_empty_it_not_unlink_it(
    tmp_path, monkeypatch, capsys
):
    # `sudo rm -rf <link>` removes the link alone: the root-owned files stay on the other disk.
    disk = tmp_path / "big-disk" / "tensorizer"
    disk.mkdir(parents=True)
    (disk / "model.tensors").write_bytes(b"\0")
    link = tmp_path / "tensorizer"
    link.symlink_to(disk)
    monkeypatch.setattr(ModelDownloader, "_empty_dir", classmethod(lambda cls, path: False))
    assert ModelDownloader._remove_cache_dir(link) is False
    advice = capsys.readouterr().out
    assert f"rm -rf {link}\n" not in advice
    assert f"sudo find {link}/ -mindepth 1 -delete" in advice


def test_the_removal_advice_for_a_path_with_a_space_names_that_path_alone(
    tmp_path, monkeypatch, capsys
):
    # `sudo rm -rf /data/my models/hub` removes `/data/my` and `models/hub`.
    import shlex

    cache = tmp_path / "my models" / "hub"
    cache.mkdir(parents=True)
    monkeypatch.setattr("shutil.rmtree", lambda *a, **k: None)
    assert ModelDownloader._remove_cache_dir(cache) is False
    advice = capsys.readouterr().out.split("Remove them with: ", 1)[1].strip()
    assert shlex.split(advice) == ["sudo", "rm", "-rf", str(cache)]
