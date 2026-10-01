import subprocess,sys,pathlib,tempfile
ws=pathlib.Path(sys.argv[1])
f=pathlib.Path(tempfile.mkdtemp())/"in.txt"; f.write_text("one two\nthree\nfour five six\n")
a=subprocess.run([sys.executable,"wc.py","--lines",str(f)],cwd=ws,capture_output=True,text=True).stdout.strip()
b=subprocess.run([sys.executable,"wc.py",str(f)],cwd=ws,capture_output=True,text=True).stdout.strip()
t=(ws/"test_wc.py").read_text()
ok=a=="3" and b=="6" and "lines" in t and t.count("def test")>=2
ok=ok and subprocess.run([sys.executable,"-m","pytest","-q"],cwd=ws,capture_output=True).returncode==0
sys.exit(0 if ok else 1)
