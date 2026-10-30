"""ModelMatrixRegistry: an empty key names no model and no pin."""

import pytest

from dreamference.hardware.model_matrix_registry import ModelMatrixRegistry


@pytest.mark.parametrize("key", ["", None])
def test_an_empty_key_has_no_spec_and_no_pin(key):
    assert ModelMatrixRegistry.get_spec(key) is None
    assert ModelMatrixRegistry.pinned_revision(key) is None


def test_an_empty_key_resolves_to_itself_not_to_a_repository():
    assert ModelMatrixRegistry.resolve_hf_repo("") == ""
