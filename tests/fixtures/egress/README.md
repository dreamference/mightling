# Egress audit fixtures

- `exec_pass.strace`: a real trace, recorded on 2026-10-01 from
  `strace -f -qq -e trace=connect,sendto,sendmsg,sendmmsg,execve -s 256 -o … ling exec --skip-git-repo-check "Reply with exactly: pong"`
  with the 16-patch build of Codex `rust-v0.158.0` that was installed at the time, in a throwaway repository and `CODEX_HOME`.
  Only the home and scratch paths were rewritten.
- `exec_leaks.strace`: the same trace with **synthetic** lines appended for the channels patches
  `0013` and `0015` closed (Statsig metrics at `ab.chatgpt.com`, the featured-plugins call to
  `chatgpt.com`, `git ls-remote https://github.com/openai/plugins.git`, the announcement tip from
  `raw.githubusercontent.com`) and for a call to the launcher's `chatgpt_base_url` redirect. The
  traces of 2026-09-29 that found those channels were not kept, so these lines are written by
  hand in strace's format, with the addresses made up; they are not a recording.
- `curl_example.strace`: a real trace of `curl https://example.com` outside any sandbox, recorded
  the same day: what a lookup and an outbound connect look like on this machine (glibc's
  `sendmmsg` to systemd-resolved's stub, the address-sorting `connect`s, an fd reused from the
  resolver to the web server). The TLS payload lines were dropped.
- `tui_pass.strace`: a real trace of the full-screen interface, recorded on 2026-10-02 by
  `ling-admin audit egress --tui`'s own session (the same strace around `ling` on a
  pseudo-terminal: the prompt typed, the reply awaited, `/quit`), with the 17-patch build of Codex
  `rust-v0.158.0` installed on 2026-10-01 21:41. Only the home and scratch paths were rewritten.
