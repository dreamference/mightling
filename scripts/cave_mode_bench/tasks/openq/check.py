import sys,pathlib
a=pathlib.Path(sys.argv[2]).read_text().lower()
sys.exit(0 if "mcp" in a and ("tool" in a) else 1)
