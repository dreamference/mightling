import sys,importlib.util,pathlib
p=pathlib.Path(sys.argv[1])/"text.py"
spec=importlib.util.spec_from_file_location("t",p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
cases={"Hello, World!":"hello-world","  Déjà vu -- again ":"d-j-vu-again","___":"","Python 3.12 released":"python-3-12-released","a--b":"a-b","ALLCAPS":"allcaps"}
sys.exit(0 if all(m.slugify(k)==v for k,v in cases.items()) else 1)
