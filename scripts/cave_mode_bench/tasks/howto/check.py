import pathlib
import sys

answer = pathlib.Path(sys.argv[2]).read_text()
sys.exit(0 if "frozen=True" in answer.replace(" ", "") else 1)
