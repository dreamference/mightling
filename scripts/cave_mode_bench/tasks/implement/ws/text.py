def slugify(title: str) -> str:
    """Turn a title into a URL slug.

    Lowercase the text, replace every run of characters that are not ASCII
    letters or digits with a single hyphen, and strip hyphens from both ends.
    Return an empty string when nothing is left.

    >>> slugify("Hello, World!")
    'hello-world'
    """
    raise NotImplementedError
