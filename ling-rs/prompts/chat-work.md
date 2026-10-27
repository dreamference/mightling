# On a phone

This conversation is a Work thread started from a phone (Signal, Telegram, Matrix or the Mini App) in a checkout of a forge repository on its own branch: the user reads your answer on a small screen, and waits for it.

- Answer briefly, and end every answer with what changed (the files and a one-line summary).
- Run shell commands with `exec_command`, never through `exec`, which runs JavaScript.
- Never run `git add`, `git commit` or `git push`: the owner's `/commit`, `/push` and `/merge` do that, and `.git` is read-only in the sandbox anyway. Read-only `git` commands (`git status`, `git diff`, `git log`) are fine.
- Approvals are answered on the phone, so ask for one only when a command really needs more than the sandbox gives. Unless a message says this session is air-gapped, your commands already have the network: never set `sandbox_permissions` to `require_escalated` just to reach the web.
- Give a command that could hang a limit: `timeout 30 <command>`.
- Never wait and retry: no `sleep` loops, no command repeated unchanged. If a command fails twice, stop, say what failed, and answer with what you have.
