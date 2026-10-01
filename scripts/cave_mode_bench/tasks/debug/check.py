import sys,pathlib,re
a=pathlib.Path(sys.argv[2]).read_text().lower()
cause=re.search(r"(during iteration|while iterating|iterat|modif|chang|delet|remov)",a)
fix=re.search(r"(list\(prices|prices\.copy\(\)|list\(prices\.items|\{[^}]*for [^}]* in prices\.items\(\)[^}]*\}|dict comprehension|comprehension|tuple\(prices)",a)
sys.exit(0 if cause and fix else 1)
