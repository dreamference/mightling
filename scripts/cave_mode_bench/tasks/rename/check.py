import subprocess,sys,pathlib
ws=pathlib.Path(sys.argv[1])
left=[p for p in ws.rglob("*.py") if "get_usr" in p.read_text()]
ok=not left and "def get_user" in (ws/"app/users.py").read_text()
ok=ok and subprocess.run([sys.executable,"-m","pytest","-q"],cwd=ws,capture_output=True).returncode==0
sys.exit(0 if ok else 1)
