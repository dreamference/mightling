from wc import count_words


def test_count_words():
    assert count_words("a b  c\nd") == 4
