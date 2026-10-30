"""Decision branches of the patch filter that keep the evaluation honest."""

from dreamference.swe_bench.swe_bench_patch_filter import SweBenchPatchFilter


def test_empty_path_is_not_a_test_path():
    assert SweBenchPatchFilter.is_test_path("") is False


def test_copy_into_a_test_file_is_dropped():
    patch = (
        "diff --git a/src/a.py b/tests/test_a.py\n"
        "similarity index 100%\n"
        "copy from src/a.py\n"
        "copy to tests/test_a.py\n"
    )
    kept, dropped = SweBenchPatchFilter.drop_test_hunks(patch)
    assert kept == ""
    assert dropped == ["tests/test_a.py"]


def test_quoted_test_path_is_unquoted_before_the_check():
    patch = (
        'diff --git "a/tests/t\\"x.py" "b/tests/t\\"x.py"\n'
        '--- "a/tests/t\\"x.py"\n+++ "b/tests/t\\"x.py"\n'
        "@@ -1 +1 @@\n-a\n+b\n"
    )
    kept, dropped = SweBenchPatchFilter.drop_test_hunks(patch)
    assert kept == ""
    assert dropped == ['tests/t"x.py']


def test_mode_change_header_with_unreadable_names_is_kept():
    # No ---/+++ lines and a header whose halves do not match: no path, so the section stays.
    patch = "diff --git a/src/one.py b/src/other.py\nold mode 100644\nnew mode 100755\n"
    assert SweBenchPatchFilter.paths(patch) == []
    kept, dropped = SweBenchPatchFilter.drop_test_hunks(patch)
    assert kept == patch
    assert dropped == []


def test_mode_change_of_a_test_file_is_dropped_from_the_header():
    patch = "diff --git a/tests/test_x.py b/tests/test_x.py\nold mode 100644\nnew mode 100755\n"
    kept, dropped = SweBenchPatchFilter.drop_test_hunks(patch)
    assert (kept, dropped) == ("", ["tests/test_x.py"])


def test_test_edit_without_weakening_is_not_reported():
    patch = (
        "diff --git a/tests/test_x.py b/tests/test_x.py\n"
        "--- a/tests/test_x.py\n+++ b/tests/test_x.py\n"
        "@@ -1,2 +1,2 @@\n x = 1\n-y = 2\n+y = 3\n"
    )
    assert SweBenchPatchFilter.weakened_tests(patch) == {}
