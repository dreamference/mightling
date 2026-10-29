# Away from home

By default your GB10 is reachable only on your own network. Remote access lets your laptop, your
phone and machines at another site reach it from anywhere, without opening a port on your router.
It builds a private network (an *overlay*, run with [NetBird](https://netbird.io)) that the
GB10 itself controls. One small rented server, the *relay box*, helps devices find each other and
carries traffic only when two devices cannot connect directly. The relay box never holds a key of
the overlay and cannot read what passes through it: traffic between your devices is encrypted
end to end.

!!! note "What you need"
    - A GB10 already set up as a [node](ling.md#using-a-gb10-from-another-computer).
    - A small rented Linux server with a fixed public address, and a DNS name pointing at it
      (an "A" record, with any proxy of the DNS provider switched **off**). You must be able to
      log in to it with SSH by a key.
    - For the public certificates below: the domain's DNS at Cloudflare, and an API token that
      may edit that zone's DNS.

## How do I set it up?

On the GB10, once:

```bash
ling-admin remote setup <the relay box's DNS name>
```

If `ssh <the DNS name>` does not log in by itself, add `--ssh <your SSH Host alias for the box>`.
The command sets up the control plane on the GB10, installs and configures the relay on the box
over SSH, opens a tunnel from the GB10 to the box and joins the GB10 to the overlay. It needs
`sudo` once on the GB10 (for the trust store and NetBird's client), so run it at a terminal where
it can ask for your password; with `--yes` it never waits for one and stops at that step, saying
so. Run it again at any time to update. It never touches anything on the box that is not the
relay's.

Check it:

```bash
ling-admin remote status     # the box's services, the certificates, the tunnel and the peers
ling-admin remote peers      # the devices that have joined
```

`ling-admin remote revoke <peer>` removes a device: its key is forgotten at once and it can no
longer reach the GB10. `ling-admin remote remove` takes the relay and the tunnel down but keeps your
enrolments; add `--purge` to delete everything.

Nothing in remote access stores an address for a device: `ling` and `ling-admin` find the GB10 by
its name in the overlay every time.

## How do I get certificates my phone trusts?

Devices must trust the GB10's control plane. By default it uses a certificate from its own
authority, which `ling` installs on your computers for you. An Android phone cannot use such
an authority (the NetBird app reads only the phone's system store), so give the box's name a public certificate
from Let's Encrypt instead:

1. Create an API token at Cloudflare that can edit DNS for your domain and save it, on the GB10,
   in the file `~/.config/dreamference/cloudflare-token`, readable by you alone
   (`chmod 600`; a file others may read is refused). It is read from that file and never printed.
2. Try it against Let's Encrypt's test service first, then for real:

    ```bash
    ling-admin remote certificate issue --staging     # a trial; nothing is served
    ling-admin remote certificate issue
    ```

A daily timer renews it; nothing has to be restarted by hand.
The command also writes a DNS rule that lets only this GB10 obtain certificates for that name.
The name of the box then appears in public certificate logs, as every public certificate's name does.

Phones also need the *relay* to be trusted, because a phone behind a mobile network often can
only reach the GB10 through it. Give the relay its own name and certificate:

```bash
ling-admin remote certificate relay --staging     # a trial; nothing changes on the box
ling-admin remote certificate relay
```

The relay's name is `relay.` plus your domain's parent; `--name <name>` chooses another. To go
back to the GB10's own certificates, run `ling-admin remote certificate relay-off` and
`ling-admin remote certificate off`. `ling-admin remote status` shows each certificate's issuer
and the days it has left. If a renewal fails, the GB10 sends the owner a notice instead of failing
silently.

## How do I join a laptop on the same network?

Join is for computers that run `ling`. On the GB10, on your home network:

```bash
ling-admin remote code          # prints an eight-digit code and waits for one computer
```

On the computer, on the same network:

```bash
ling node remote join --code <the eight digits>
```

It finds the GB10, proves the code both ways without sending it, installs NetBird and the
certificate it needs, and joins. The code works for ten minutes and for one computer. From then
on `ling` reaches the GB10 by its overlay name from anywhere. If several nodes answer, add
`--node <name>`.

## How do I add a phone?

Install NetBird's app on the phone and add a self-hosted server in it. It asks for the server's address and a
one-use setup key. On the GB10, make them:

```bash
ling-admin remote code --bundle-file ~/phone-join.json --ttl 2h
```

The file holds the server address (`management_url`) and the key (`setup_key`). Copy those two
values into the app, then **delete the file**: the key works once and expires after the time you
gave (at most 24 hours). On Android the public certificates above are all that is needed, and
nothing of ours has to be installed on the phone. An iPhone would need the node's own authority
installed as a profile and fully trusted; that has not been confirmed on a real iPhone yet.

Once the phone is in the overlay, the [web UI](web.md) can be opened at the GB10's overlay name,
paired as usual.

## How do I join a machine at another site with one line?

For a computer or a second GB10 that is not on your network and has no `ling` yet (a Linux machine
with `apt`), the GB10 writes a single command to a new file:

```bash
ling-admin remote code --bundle-file ~/join-new-gx10.txt --join --ttl 12h \
    --name new-gx10 --ssh-user <the account on that machine>
```

`--name` is the machine's name in the overlay (one lowercase word), and `--ssh-user` is the
account to put in its `ssh` shortcut. Without `--ttl`, the line works for two hours; at most 24.
The line works **once** and then stops, spent or not, when the time is up. Send it to
yourself over an encrypted channel, such as Signal's Note to Self, and delete the file. On the new
machine, open a terminal, paste the line and give `sudo` its password. It installs NetBird
(after checking its checksum), joins the overlay and adds an `ssh` shortcut named `gx10-remote` to
reach your GB10.

Anyone who reads the line before it is used can join, so treat it like a password and paste it
only into your own terminals.

A new GB10 joined this way can then be set up from your GB10 over the overlay, as in
[Setting up a node from far away](ling.md#setting-up-a-node-from-far-away).

## How do I restart a hung GB10 from a distance?

A smart plug that your GB10's neighbour can reach on its network lets you cut power and restore
it remotely. This needs a plug with a local interface and no cloud (Shelly Plug S Gen3 or Plus Plug S have one) on the
GB10's mains lead. Set it up first:

- Switch off the plug's cloud and outside access, keep it on your home network behind its router,
  and set its "power on default" to **on**.
- Check once, in person, that the GB10 starts by itself when power returns (its firmware's
  power-restore setting). If it does not, the plug alone cannot revive it.

On the computer that can reach the plug (on a machine at another site this is the *other* GB10 there; it
acts for it), name the plug and say which node it powers:

```bash
ling-admin node power-config plug new-gx10-plug --host <the plug's host name>
ling-admin node power-config node new-gx10 --plug new-gx10-plug
ling-admin node power-config show
```

On a computer that cannot reach the plug itself, add `--via <the node that can>` to the second
command. Then:

```bash
ling-admin node power-cycle new-gx10       # off for 20 seconds, on, then waits up to 10 minutes
```

`--off-seconds` and `--wait-minutes` change the times. It refuses to cut the power while the node
still answers; `--force` overrides that. It checks the plug's state after each step and, if it
cannot switch the plug back on, says so loudly so that you can do it by hand. The plug and node names
are kept in `~/.config/dreamference/node-power.json`.

## What does this change about privacy?

The relay box sees that devices connect and, when it carries traffic, how much, but not the
contents. The control plane's public certificate puts the box's name in public certificate logs.
The control plane runs on your GB10, not on NetBird's servers. See
[Privacy & security](privacy.md).
