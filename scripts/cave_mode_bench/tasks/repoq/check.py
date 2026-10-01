import sys,pathlib
a=pathlib.Path(sys.argv[2]).read_text()
sys.exit(0 if "SVC_LISTEN_PORT" in a and "8321" in a else 1)
