set -e
echo 'def bye(name):
    return f"Bye, {name}"' >> app.py
git add -A && git commit -qm "Add bye"
echo "# KEEP_ME: local change" >> app.py
