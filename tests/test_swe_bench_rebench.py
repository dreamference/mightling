"""SWE-rebench as a second task pool (specs/DREAMFERENCE_MIGHTLING_SWE_BENCH.md §28).

The dataset names, the rows the pinned harness grades, the arm64 image builder and its archives.
`docker` is a stand-in that records every call and plays the few that write files (`save`), so
nothing here builds, pulls or loads an image, starts a container or reaches the network.
"""

import gzip
import json
import subprocess
from pathlib import Path

import pytest

from dreamference.swe_bench import (
    SweBenchCommand, SweBenchDocker, SweBenchEvaluator, SweBenchHarness, SweBenchImageBuilder, SweBenchImages,
    SweBenchRebenchRows, SweBenchRunner, SweBenchSettings,
)
from dreamference.swe_bench import swe_bench_settings
from dreamference.swe_bench.swe_bench_image_builder import REQUIREMENTS_HELPER

# A real row of the leaderboard's 2026_03 split (the smallest), as the dataset gives it.
SQL_METADATA = {
    "repo": "macbre/sql-metadata",
    "instance_id": "macbre__sql-metadata-625_interface",
    "base_commit": "425b46326d4a7b7e937f3ef19b321df0512b9163",
    "patch": "diff --git a/sql_metadata/nested_resolver.py b/sql_metadata/nested_resolver.py\nindex 771fd0b9..3efa9ef2 100644\n"
             "--- a/sql_metadata/nested_resolver.py\n+++ b/sql_metadata/nested_resolver.py\n@@ -15,6 +15,7 @@\n"
             "     from sql_metadata.parser import Parser\n \n from sqlglot import exp\n+from sqlglot.errors import ErrorLevel\n"
             " from sqlglot.generator import Generator\n \n from sql_metadata.utils import (\n",
    "test_patch": "diff --git a/test/test_mssql_server.py b/test/test_mssql_server.py\nindex 82081082..1035b4eb 100644\n"
                  "--- a/test/test_mssql_server.py\n+++ b/test/test_mssql_server.py\n"
                  "@@ -187,3 +187,6 @@ def test_mssql_catalog_double_dot():\n"
                  "     p = Parser(\"SELECT * FROM mydb..orders\")\n     assert \"mydb..orders\" in p.tables\n+\n+\n"
                  "+def test_for_xml_path_does_not_emit_sqlglot_warning(caplog):\n",
    "problem_statement": "Handle SQL Server bracketed identifiers in subquery column references without user-visible "
                         "parser warnings.\n\n## Interface",
    "hints_text": "",
    "created_at": "2026-05-07 20:17:44",
    "version": "unknown",
    "meta": {"commit_name": "", "failed_lite_validators": [], "has_test_patch": True, "is_lite": False,
             "num_modified_files": 0},
    "install_config": {
        "docker_specs": None, "env_vars": None, "env_yml_path": [],
        "install": "pip install -e . -q && pip install pytest pytest-cov -q", "log_parser": "parse_log_pytest",
        "no_use_env": False, "old_test_cmd": "", "packages": "", "pip_packages": [], "pre_install": [],
        "python": "3.13", "reqs_path": [],
        "test_cmd": "pytest --no-header -rA --tb=line --color=no -p no:cacheprovider test/test_mssql_server.py",
        "try_log_parser": [],
    },
    "FAIL_TO_PASS": ["test/test_mssql_server.py::test_for_xml_path_does_not_emit_sqlglot_warning"],
    "PASS_TO_PASS": ["test/test_mssql_server.py::test_sql_server_cte"],
    "environment_setup_commit": "425b46326d4a7b7e937f3ef19b321df0512b9163",
    "docker_image": "swerebench/sweb.eval.x86_64.macbre_1776_sql-metadata-625_interface:latest",
    "image_name": "swerebench/sweb.eval.x86_64.macbre_1776_sql-metadata-625_interface:latest",
    "interface": "`Parser(query).columns` should complete without emitting warnings from the `sqlglot` logger.",
}

BASE = SQL_METADATA["base_commit"]
DATASET = "rebench-2026-03"
IMAGE = "mightling-swe-rebench-arm64:macbre-sql-metadata-625_interface"


@pytest.fixture(autouse=True)
def arm64(monkeypatch):
    """Every test is on the machine the builder is for, whatever runs the suite."""
    monkeypatch.setattr(SweBenchSettings, "architecture", classmethod(lambda cls: "arm64"))


class Docker:
    """Records docker calls; `image inspect` answers for `present`, `save` writes a tar."""

    def __init__(self):
        self.calls = []
        self.present = set()
        self.fail = {}

    def run(self, args, timeout=None, input_text=None):
        self.calls.append((list(args), input_text))
        done = lambda code=0, out="", err="": subprocess.CompletedProcess(args, code, out, err)
        verb = args[0]
        for marker, code in self.fail.items():
            if marker in (input_text or "") or marker == verb:
                return done(code, "", f"{marker} broke\nlast words of {marker}")
        if verb == "image" and args[1] == "inspect":
            return done(0, f"null sha256:id-{args[2].split(':')[-1]}") if args[2] in self.present else done(1)
        if verb == "commit":
            self.present.add(args[-1])
        if verb == "save":
            Path(args[2]).write_bytes(b"tar of " + args[3].encode())
        if verb == "rmi":
            self.present.discard(args[1])
        if verb == "load":
            name = Path(args[2]).name[:-len(".tar.gz")]
            self.present.add(f"mightling-swe-rebench-arm64:{name}")
        return done()

    def verbs(self):
        return [args[0] if args[0] not in ("image",) else " ".join(args[:2]) for args, _ in self.calls]


@pytest.fixture
def docker(monkeypatch):
    fake = Docker()
    monkeypatch.setattr(SweBenchDocker, "run", classmethod(lambda cls, *a, **k: fake.run(*a, **k)))
    return fake


def write_snapshot(rows, dataset=DATASET):
    path = SweBenchHarness.snapshot_path(dataset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(SweBenchRebenchRows.adapt(r)) + "\n" for r in rows))
    path.with_suffix(".meta.json").write_text(json.dumps(
        {"revision": "rev-r", "rows": len(rows), "dataset": SweBenchSettings.dataset_id(dataset)}))


# -- the dataset names ------------------------------------------------------------------------------

def test_a_rebench_month_is_a_split_of_the_leaderboard_dataset_and_resolves_to_itself():
    assert SweBenchSettings.dataset_id("rebench-2026-03") == "nebius/SWE-rebench-leaderboard:2026_03"
    assert SweBenchSettings.dataset_id("Rebench") == "nebius/SWE-rebench-leaderboard"
    resolved = SweBenchSettings.dataset_id("rebench-2026-03")
    assert SweBenchSettings.dataset_id(resolved) == resolved
    assert SweBenchSettings.dataset_source(resolved) == ("nebius/SWE-rebench-leaderboard", "2026_03")
    assert SweBenchSettings.dataset_source("verified") == ("SWE-bench/SWE-bench_Verified", "test")
    assert SweBenchSettings.is_rebench(resolved) and SweBenchSettings.is_rebench("rebench")
    assert not SweBenchSettings.is_rebench("verified") and not SweBenchSettings.is_rebench(None)
    # One snapshot per month, and the manifest's dataset finds the same file.
    months = {SweBenchHarness.snapshot_path(name).name for name in ("rebench", "rebench-2026-02", "rebench-2026-03")}
    assert months == {"SWE-rebench-leaderboard.jsonl", "SWE-rebench-leaderboard-2026_02.jsonl",
                      "SWE-rebench-leaderboard-2026_03.jsonl"}
    assert SweBenchHarness.snapshot_path(resolved) == SweBenchHarness.snapshot_path("rebench-2026-03")
    assert SweBenchHarness.snapshot_path("verified").name == "SWE-bench_Verified.jsonl"


def test_the_download_asks_for_the_months_split_and_keeps_rows_the_harness_grades(monkeypatch):
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        out = json.dumps({"revision": "abc", "rows": 1}) + "\n" + json.dumps(SQL_METADATA) + "\n"
        return subprocess.CompletedProcess(command, 0, out, "")

    monkeypatch.setattr(SweBenchHarness, "execute", staticmethod(execute))
    meta = SweBenchHarness.download_dataset(DATASET)
    assert calls[0][-2:] == ["nebius/SWE-rebench-leaderboard", "2026_03"]
    assert meta["dataset"] == "nebius/SWE-rebench-leaderboard:2026_03"
    assert "not contamination-free" in meta["task_pool"]
    [kept] = SweBenchHarness.rows(DATASET)
    assert kept["log_parser"] == "parse_log_pytest" and kept["eval_type"] == "pass_and_fail"
    assert kept["eval_script"].startswith("#!/bin/bash\nset -uxo pipefail\n")
    # The interface this task's tests call was only in its own column.
    assert kept["problem_statement"].endswith("## Interface\n\n" + SQL_METADATA["interface"] + "\n")
    assert kept["interface_appended"] is True


# -- the rows ---------------------------------------------------------------------------------------

def test_the_eval_script_is_the_forks_for_a_real_row():
    script = SweBenchRebenchRows.eval_script(SQL_METADATA)
    reset = f"git checkout {BASE} test/test_mssql_server.py"
    assert script == "\n".join([
        "#!/bin/bash", "set -uxo pipefail",
        "source /opt/miniconda3/bin/activate", "conda activate testbed", "cd /testbed",
        "git config --global --add safe.directory /testbed", "cd /testbed", "git status", "git show",
        f"git -c core.fileMode=false diff {BASE}",
        "source /opt/miniconda3/bin/activate", "conda activate testbed",
        "pip install -e . -q && pip install pytest pytest-cov -q",
        reset,
        f"git apply -v - <<'EOF_114329324912'\n{SQL_METADATA['test_patch']}\nEOF_114329324912",
        ": '>>>>> Start Test Output'",
        # The fork appends the test patch's files to a test command that may already name them.
        "pytest --no-header -rA --tb=line --color=no -p no:cacheprovider test/test_mssql_server.py "
        "test/test_mssql_server.py",
        ": '>>>>> End Test Output'",
        reset,
    ]) + "\n"
    # The reset keeps the shape the per-file rewrite recognises.
    assert "git cat-file -e" in SweBenchHarness.per_file_reset(script, BASE)


def test_a_test_patch_that_only_adds_a_file_still_resets_it():
    added = ("diff --git a/tests/test_new.py b/tests/test_new.py\nnew file mode 100644\nindex 0000000..1111111\n"
             "--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1,2 @@\n+def test_new():\n+    pass\n")
    row = dict(SQL_METADATA, test_patch=added)
    assert SweBenchRebenchRows.modified_files(added) == []  # the fork's reset would be a bare checkout
    assert SweBenchRebenchRows.touched_files(added) == ["tests/test_new.py"]
    script = SweBenchHarness.per_file_reset(SweBenchRebenchRows.eval_script(row), BASE)
    assert f"git checkout {BASE}\n" not in script and f"git checkout {BASE} \n" not in script
    assert script.count('else rm -f -- "$f"; fi; done') == 2


def test_the_test_directives_leave_out_data_and_removed_files_and_name_django_modules():
    patch = "".join([
        "diff --git a/tests/test_a.py b/tests/test_a.py\n--- a/tests/test_a.py\n+++ b/tests/test_a.py\n@@ -1 +1 @@\n-x\n+y\n",
        "diff --git a/tests/data.json b/tests/data.json\n--- a/tests/data.json\n+++ b/tests/data.json\n@@ -1 +1 @@\n-1\n+2\n",
        "diff --git a/tests/test_old.py b/tests/test_old.py\ndeleted file mode 100644\n--- a/tests/test_old.py\n+++ /dev/null\n"
        "@@ -1 +0,0 @@\n--- a removed line that looks like a header\n",
    ])
    row = {"repo": "acme/widget", "test_patch": patch}
    assert SweBenchRebenchRows.test_directives(row) == ["tests/test_a.py"]
    assert SweBenchRebenchRows.modified_files(patch) == ["tests/test_a.py", "tests/data.json", "tests/test_old.py"]
    assert SweBenchRebenchRows.test_directives(dict(row, repo="django/django")) == ["test_a"]


# -- the build scripts ------------------------------------------------------------------------------

def test_the_base_layer_is_the_forks_for_aarch64():
    script = SweBenchImageBuilder.base_script()
    assert "Miniconda3-py311_23.11.0-2-Linux-aarch64.sh" in script and "conda init --all" in script
    assert SweBenchImageBuilder.platform() == "linux/arm64/v8"
    assert SweBenchImageBuilder.base_image() == "mightling-swe-rebench-base-arm64:ubuntu22.04-conda-py311_23.11.0-2"


def test_the_environment_follows_the_install_config():
    plain = SweBenchImageBuilder.env_script(SQL_METADATA)
    assert "conda create -n testbed python=3.13  -y" in plain
    config = dict(SQL_METADATA["install_config"], packages="requirements.txt", reqs_path=["requirements/test.txt"],
                  pip_packages=["pytest", "hypothesis==6.1"])
    text = SweBenchImageBuilder.env_script(dict(SQL_METADATA, install_config=config))
    assert f"python3 /root/mightling_requirements.py txt {BASE} requirements/test.txt > $HOME/requirements.txt" in text
    assert "python -m pip install pytest hypothesis==6.1" in text
    config = dict(SQL_METADATA["install_config"], packages="environment.yml", env_yml_path=["ci/env.yml"])
    text = SweBenchImageBuilder.env_script(dict(SQL_METADATA, install_config=config))
    assert "yml " + BASE + " ci/env.yml > environment.yml" in text and "conda env create --file environment.yml" in text
    assert text.rstrip().endswith('conda activate testbed" > /root/.bashrc')


def test_the_repository_is_cloned_at_the_base_commit_and_installed_without_sudo():
    clone = SweBenchImageBuilder.clone_script(SQL_METADATA)
    assert "git clone -o origin https://github.com/macbre/sql-metadata /testbed" in clone
    assert f"git reset --hard {BASE}" in clone and "git remote remove origin" in clone
    config = dict(SQL_METADATA["install_config"], pre_install=["apt-get update", "sudo apt install -y make gcc"])
    repo = SweBenchImageBuilder.repo_script(dict(SQL_METADATA, install_config=config))
    assert "\napt install -y make gcc\n" in repo and "sudo" not in repo
    assert repo.rstrip().endswith("git commit --allow-empty -am SWE-bench")


def test_the_requirements_helper_does_what_the_fork_does(tmp_path):
    repo = tmp_path / "testbed"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True)
    git("init", "-q")
    (repo / "requirements").mkdir()
    (repo / "requirements" / "test.txt").write_text("-e .\n# comment\npytest>=7\n-r base.txt\nnumpy==1.26\n")
    (repo / "requirements" / "base.txt").write_text("numpy>=1.0\nrequests\n.\n")
    git("add", "-A")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c")
    helper = tmp_path / "helper.py"
    helper.write_text(REQUIREMENTS_HELPER.replace('"/testbed"', repr(str(repo))))
    out = subprocess.run(["python3", str(helper), "txt", "HEAD", "requirements/test.txt"],
                         capture_output=True, text=True, check=True).stdout
    assert out.splitlines() == ["pytest>=7", "numpy==1.26", "requests"]


# -- building ---------------------------------------------------------------------------------------

def settings(**table):
    return SweBenchSettings({"build_memory": "12G", "build_cpus": 3, "build_step_timeout": "10m", **table})


def test_a_build_runs_each_step_in_a_capped_container_and_always_removes_it(docker, tmp_path):
    assert SweBenchImageBuilder.build(SQL_METADATA, settings(), tmp_path / "build.log") is None
    runs = [args for args, _ in docker.calls if args[0] == "run"]
    assert len(runs) == 2  # the base, then the instance
    for args in runs:
        assert args[args.index("--memory") + 1] == "12G" and args[args.index("--memory-swap") + 1] == "12G"
        assert args[args.index("--cpus") + 1] == "3" and args[args.index("--platform") + 1] == "linux/arm64/v8"
    assert runs[0][-3:] == ["ubuntu:22.04", "sleep", "infinity"]
    assert runs[1][-3] == SweBenchImageBuilder.base_image()
    steps = [text for args, text in docker.calls if args[0] == "exec"]
    assert len(steps) == 4 and "Miniconda3" in steps[0] and "git clone" in steps[1]
    assert "conda create" in steps[2] and "git commit --allow-empty" in steps[3]
    commits = [args for args, _ in docker.calls if args[0] == "commit"]
    assert commits[0][-1] == SweBenchImageBuilder.base_image() and commits[1][-1] == IMAGE
    assert "WORKDIR /testbed/" in commits[1]
    removed = [args for args, _ in docker.calls if args[:2] == ["rm", "-f"]]
    assert len(removed) == 4  # before and after each layer
    # A second build reuses the base.
    docker.calls.clear()
    assert SweBenchImageBuilder.build(SQL_METADATA, settings(), tmp_path / "build.log") is None
    assert len([args for args, _ in docker.calls if args[0] == "run"]) == 1


def test_a_failed_step_says_which_and_leaves_no_container(docker, tmp_path):
    docker.present.add(SweBenchImageBuilder.base_image())
    docker.fail["conda create"] = 1
    problem = SweBenchImageBuilder.build(SQL_METADATA, settings(), tmp_path / "build.log")
    assert problem == "the environment step failed: last words of conda create"
    assert docker.calls[-1][0][:2] == ["rm", "-f"] and IMAGE not in docker.present
    assert "conda create broke" in (tmp_path / "build.log").read_text()


def test_build_all_validates_archives_and_removes_each_image(docker, monkeypatch):
    other = dict(SQL_METADATA, instance_id="acme__widget-7", repo="acme/widget")
    write_snapshot([SQL_METADATA, other])
    docker.present.add(SweBenchImageBuilder.base_image())
    validated = []

    def validate(cls, dataset, ids, settings, force=False):
        validated.append((dataset, ids, force, [i for i in ids if SweBenchImages.built_image(i) in docker.present]))
        problem = None if ids[0] == SQL_METADATA["instance_id"] else "a patch that fixes nothing resolves it here"
        SweBenchImages.record_validation(ids[0], SweBenchImages.built_image(ids[0]), "sha256:id", problem, dataset)
        return {ids[0]: problem}

    monkeypatch.setattr(SweBenchEvaluator, "validate", classmethod(validate))
    monkeypatch.setattr(SweBenchEvaluator, "disk_problem", classmethod(lambda cls, settings: None))
    outcome = SweBenchImageBuilder.build_all(DATASET, [SQL_METADATA["instance_id"], "acme__widget-7"], settings())
    assert outcome == {SQL_METADATA["instance_id"]: None, "acme__widget-7": "a patch that fixes nothing resolves it here"}
    # Each was validated while its image was there, and neither is left.
    assert [v[3] for v in validated] == [[SQL_METADATA["instance_id"]], ["acme__widget-7"]]
    assert not any(SweBenchImages.is_built(image) for image in docker.present)
    record = SweBenchImages.validated(DATASET)[SQL_METADATA["instance_id"]]
    assert record["source"] == "built" and record["archive"]["file"] == "macbre-sql-metadata-625_interface.tar.gz"
    archive = SweBenchImages.archive_path(IMAGE)
    assert gzip.decompress(archive.read_bytes()) == b"tar of " + IMAGE.encode()
    assert record["archive"]["sha256"] == SweBenchImages.file_sha256(archive)
    assert not list(archive.parent.glob(".*"))  # no tar or staging file left
    folder = json.loads((swe_bench_settings.ARCHIVE_DIR / "validated.json").read_text())
    assert list(folder["instances"]) == [SQL_METADATA["instance_id"]] and "acme__widget-7" in folder["rejected"]
    # SWE-rebench's list is its own: Verified's is untouched.
    assert SweBenchImages.validated() == {} and SweBenchImages.validated_path(DATASET).name == "validated-arm64-rebench.json"
    # A second pass skips what has a result.
    docker.calls.clear()
    assert SweBenchImageBuilder.build_all(DATASET, [SQL_METADATA["instance_id"], "acme__widget-7"], settings()) == outcome
    assert not [args for args, _ in docker.calls if args[0] == "run"]


def test_build_all_stops_at_the_disk_reserve_and_records_a_failed_build(docker, monkeypatch):
    write_snapshot([SQL_METADATA])
    docker.fail["git clone"] = 128
    docker.present.add(SweBenchImageBuilder.base_image())
    monkeypatch.setattr(SweBenchEvaluator, "disk_problem", classmethod(lambda cls, settings: None))
    outcome = SweBenchImageBuilder.build_all(DATASET, [SQL_METADATA["instance_id"]], settings())
    assert outcome[SQL_METADATA["instance_id"]].startswith("the arm64 image could not be built: the clone step failed")
    assert SQL_METADATA["instance_id"] in SweBenchImages.rejected(DATASET)
    monkeypatch.setattr(SweBenchEvaluator, "disk_problem", classmethod(lambda cls, settings: "3 GiB free"))
    assert SweBenchImageBuilder.build_all(DATASET, [SQL_METADATA["instance_id"]], settings(), force=True) == {}


# -- moving images between machines -----------------------------------------------------------------

def builder_folder(tmp_path, sha=None):
    folder = tmp_path / "from-builder"
    folder.mkdir()
    archive = folder / "macbre-sql-metadata-625_interface.tar.gz"
    archive.write_bytes(gzip.compress(b"tar"))
    entry = {"image": IMAGE, "digest": "sha256:id-x", "source": "built", "harness": "5.0.2", "at": "then",
             "archive": {"file": archive.name, "sha256": sha or SweBenchImages.file_sha256(archive), "bytes": 3},
             "built_on": "builder-1", "validated_on": "builder-1"}
    (folder / "validated.json").write_text(json.dumps(
        {"instances": {SQL_METADATA["instance_id"]: entry}, "rejected": {"acme__widget-7": "it does not build"}}))
    return folder


def test_import_takes_in_archives_that_match_and_loads_nothing(docker, tmp_path):
    taken, problems = SweBenchImages.import_archives(builder_folder(tmp_path), DATASET)
    assert (taken, problems) == (1, [])
    record = SweBenchImages.validated(DATASET)[SQL_METADATA["instance_id"]]
    assert record["built_on"] == "builder-1" and record["source"] == "built" and "imported_at" in record
    assert record["at"] == "then" and record["harness"] == "5.0.2"
    assert SweBenchImages.archive_path(IMAGE).exists()
    assert SweBenchImages.rejected(DATASET) == {"acme__widget-7": "it does not build"}
    assert SweBenchImages.validated() == {}
    assert not [args for args, _ in docker.calls if args[0] in ("load", "pull")]


def test_import_refuses_an_archive_that_does_not_match(docker, tmp_path):
    taken, problems = SweBenchImages.import_archives(builder_folder(tmp_path, sha="0" * 64), DATASET)
    assert taken == 0 and "does not match its SHA-256" in problems[0]
    assert SweBenchImages.validated(DATASET) == {}


def test_a_built_image_is_loaded_from_its_archive_and_never_pulled(docker, tmp_path):
    assert SweBenchImages.ensure(IMAGE) is None  # no archive: nothing to load, and no pull
    SweBenchImages.import_archives(builder_folder(tmp_path), DATASET)
    assert SweBenchImages.ensure(IMAGE) == "sha256:id-macbre-sql-metadata-625_interface"
    assert [args[0] for args, _ in docker.calls if args[0] in ("load", "pull")] == ["load"]
    assert SweBenchImages.ensure("greynewell/swe-bench-arm64:acme-widget-1") is None
    assert [args[0] for args, _ in docker.calls if args[0] in ("load", "pull")] == ["load", "pull"]


def test_validation_names_what_is_missing_without_rejecting_it(docker, tmp_path, monkeypatch):
    write_snapshot([SQL_METADATA])
    monkeypatch.setattr(SweBenchEvaluator, "disk_problem", classmethod(lambda cls, settings: None))
    outcome = SweBenchEvaluator.validate(DATASET, [SQL_METADATA["instance_id"]], SweBenchSettings({}))
    assert "no arm64 image built yet" in outcome[SQL_METADATA["instance_id"]]
    assert SweBenchImages.rejected(DATASET) == {}
    # Imported, then its archive lost: validated, but not usable here.
    SweBenchImages.import_archives(builder_folder(tmp_path), DATASET)
    assert SweBenchEvaluator.validate(DATASET, [SQL_METADATA["instance_id"]], SweBenchSettings({})) == \
        {SQL_METADATA["instance_id"]: None}
    SweBenchImages.archive_path(IMAGE).unlink()
    outcome = SweBenchEvaluator.validate(DATASET, [SQL_METADATA["instance_id"]], SweBenchSettings({}))
    assert outcome[SQL_METADATA["instance_id"]].startswith("validated, but its image is not here")


def test_the_manifest_names_the_pool_and_where_the_images_were_built():
    records = [{"built_on": "builder-1"}, {"built_on": "builder-2"}, {}]
    assert SweBenchRunner.image_source(DATASET, records) == \
        "built from the SWE-rebench fork's recipes for arm64 on builder-1, builder-2"
    assert SweBenchRunner.image_source("verified", records) == "community (greynewell/swe-bench-arm64)"


# -- the commands -----------------------------------------------------------------------------------

def test_build_refuses_verified_and_a_dry_run_prints_the_commands(docker, monkeypatch, capsys):
    assert SweBenchCommand.build("verified") == 2
    monkeypatch.setattr(SweBenchHarness, "install", classmethod(lambda cls: True))
    monkeypatch.setattr(SweBenchHarness, "download_dataset", classmethod(lambda cls, dataset, force=False: {"dataset": "x"}))
    write_snapshot([SQL_METADATA])
    assert SweBenchCommand.build(DATASET, dry_run=True) == 0
    out = capsys.readouterr().out
    assert "git clone -o origin https://github.com/macbre/sql-metadata /testbed" in out
    assert ">>>>> Start Test Output" in out and "not contamination-free" in out
    assert not [args for args, _ in docker.calls if args[0] in ("run", "info")]


def test_the_parser_takes_build_and_import():
    import argparse
    parser = argparse.ArgumentParser()
    SweBenchCommand.add_parser(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["swe-bench", "build", "--dataset", DATASET, "--limit", "5", "--memory", "8G", "--cpus", "2"])
    assert (args.swe_bench_command, args.dataset, args.limit, args.memory, args.cpus) == ("build", DATASET, 5, "8G", 2)
    args = parser.parse_args(["swe-bench", "import", "/tmp/x", "--dataset", DATASET])
    assert (args.swe_bench_command, args.folder) == ("import", "/tmp/x")
    args = parser.parse_args(["swe-bench", "run", "--dataset", DATASET])
    assert args.dataset == DATASET
