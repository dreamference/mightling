"""Which dotted names and named definitions an issue's targets keep, and which they drop and why."""

from dreamference.swe_bench.swe_bench_issue_targets import SweBenchIssueTargets


def listing(files, definitions):
    """What the container's resolve script prints: tracked files, then `path:line:source` hits."""
    hits = [f"{path}:1:{source}" for path, source in definitions]
    return "\n".join(files) + "\n@@definitions@@\n" + "\n".join(hits) + "\n"


def conditions(text, files, definitions):
    return SweBenchIssueTargets.conditions(text, listing(files, definitions))


def test_a_dotted_name_whose_module_does_not_define_the_rest_targets_the_module():
    found = conditions(
        "The failure is in `pkg.mod.render_widget`.",
        ["pkg/mod.py", "pkg/other.py"],
        [("pkg/other.py", "def render_widget():")],
    )
    assert [t["id"] for t in found["targets"]] == ["file:pkg/mod.py"]
    assert found["dropped"] == []


def test_a_method_defined_in_several_files_is_narrowed_to_the_files_that_define_its_class():
    found = conditions(
        "`QuerySet.bulk_create` loses the ids.",
        ["db/query.py", "db/other.py"],
        [
            ("db/query.py", "class QuerySet:"),
            ("db/query.py", "    def bulk_create(self):"),
            ("db/other.py", "    def bulk_create(self):"),
        ],
    )
    [target] = found["targets"]
    assert target["id"] == "def:bulk_create" and target["files"] == ["db/query.py"]


def test_a_dotted_name_defined_nowhere_is_dropped_as_not_defined():
    found = conditions("`Widget.render_thing` is wrong.", ["app/widget.py"], [])
    assert found["targets"] == []
    assert found["dropped"] == [
        {"text": "Widget.render_thing", "why": "not defined in the repository"}
    ]


def test_a_dotted_name_defined_in_too_many_files_is_dropped_with_the_count():
    files = [f"app/m{i}.py" for i in range(4)]
    found = conditions(
        "`Base.save_all` fails.", files, [(path, "def save_all():") for path in files]
    )
    assert found["targets"] == []
    assert found["dropped"] == [{"text": "Base.save_all", "why": "defined in 4 files"}]


def test_a_named_function_already_targeted_by_its_dotted_name_is_neither_repeated_nor_dropped():
    found = conditions(
        "`QuerySet.bulk_create` fails: calling bulk_create() raises.",
        ["db/query.py"],
        [("db/query.py", "class QuerySet:"), ("db/query.py", "    def bulk_create(self):")],
    )
    assert [t["id"] for t in found["targets"]] == ["def:bulk_create"]
    assert found["dropped"] == []


def test_a_named_function_defined_nowhere_or_in_too_many_files_is_dropped():
    files = [f"app/m{i}.py" for i in range(4)]
    found = conditions(
        "Calling missing_helper() and then shared_helper() fails.",
        files,
        [(path, "def shared_helper():") for path in files],
    )
    assert found["targets"] == []
    assert found["dropped"] == [
        {"text": "missing_helper", "why": "not defined in the repository"},
        {"text": "shared_helper", "why": "defined in 4 files"},
    ]


def test_the_names_to_resolve_add_the_last_two_parts_of_a_dotted_name_but_not_short_ones():
    names = SweBenchIssueTargets.names_to_resolve(
        "See `ab.QuerySet.bulk_create` and `xy.ab.render_widget`."
    )
    assert "QuerySet" in names and "bulk_create" in names and "render_widget" in names
    assert "ab" not in names and "xy" not in names
