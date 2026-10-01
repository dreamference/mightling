import subprocess,sys,hashlib,pathlib
ws=pathlib.Path(sys.argv[1])
ok=subprocess.run([sys.executable,"-m","pytest","-q"],cwd=ws,capture_output=True).returncode==0
t=(ws/"test_stats.py").read_text()
ok=ok and "2.5" in t and "median([4, 1, 3, 2])" in t
sys.exit(0 if ok else 1)
