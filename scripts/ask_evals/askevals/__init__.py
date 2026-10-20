"""`ask-evals`: the phone-chat assistant's own regression suite.

specs/DREAMFERENCE_MIGHTLING_ASSISTANT_EVALS.md §5 and §9. Every module here is standard library
only, except `images` (Pillow) and the optional tokenizer in `metrics`, so the harness runs with
the system Python inside the jail.
"""
