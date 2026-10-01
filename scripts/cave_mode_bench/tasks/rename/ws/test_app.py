from app.api import handle
from app.users import get_usr


def test_get():
    assert get_usr(1) == "ada"


def test_handle_missing():
    assert handle(3) == {"error": "not found"}
