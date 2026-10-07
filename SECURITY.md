# Security

Mightling runs an agent with a shell on your machine, close to your code and, if you connect them,
your mail, files and calendar. Reports about anything that weakens that are welcome.

## Reporting a vulnerability

Please report privately, through GitHub: the repository's **Security** tab → **Report a
vulnerability**. Do not open a public issue for a vulnerability.

Include what you ran, what you expected and what happened, and the output of `mling --version`
and `mling-admin status`. You will get an answer within a week.

## What is in scope

- **The air gap.** Any way for a command in a session at `/airgapped on` to reach a network other
  than the model server, or to lower the level without you typing `/airgapped off`.
- **Phone-home channels.** Any network connection `mling` makes that the
  [privacy page](docs/privacy.md) does not list. `mling-admin audit egress` traces one session and
  is a good way to show one.
- **The sandbox.** A sandboxed command writing outside the folders it was given, or starting
  outside the sandbox without an approval.
- **Untrusted input.** Email, Drive or Calendar content, a web page, a skill or a repository that
  gets the agent to act without your approval in a way the sandbox should have stopped.
- **Node pairing and jobs.** Anything a paired node can make another node do beyond the operations
  `mling-admin node serve-job` lists.
- **Host safety.** A model load that freezes the host despite `mling-admin server start`'s checks.

## Known and accepted

- **The model server listens on every interface, without a key** (port 8000), because the web chat
  and other agents reach it from Docker containers. Mightling assumes a trusted local network; see
  [Privacy & security](docs/privacy.md) for how to restrict it.
- **The web chat's default administrator password is published.** It is bound to `127.0.0.1` unless
  you advertise the machine as a node; create the account with your own password.
- **Full Access has no sandbox.** That is what it means, and why it cannot be combined with
  `/airgapped on`.
