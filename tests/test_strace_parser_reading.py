"""The strace parser on calls that each could hide a lookup or a connection from the verdict.

Where a local resolver's socket may live, every git command that reaches a network, git's options
that take a value, and lines strace prints without a pid or cut short.
"""

import pytest

from dreamference.audit.strace_parser import StraceParser


@pytest.mark.parametrize("path", [
    "/var/run/avahi-daemon/socket",
    "/var/run/systemd/resolve/io.systemd.Resolve",
])
def test_a_local_resolver_reached_through_var_run_is_a_lookup(path):
    # `/var/run` links to `/run`: a program that names the old path asks the same resolver.
    trace = StraceParser.parse(f'5 connect(7<UNIX-STREAM:[500]>, {{sa_family=AF_UNIX, sun_path="{path}"}}, 110) = 0\n')
    assert trace.local_resolvers == {path: 1}


def test_an_abstract_socket_named_like_a_resolver_is_not_one():
    # avahi's socket is a file; an abstract socket of the same name belongs to someone else.
    trace = StraceParser.parse(
        '5 connect(7<UNIX-STREAM:[500]>, {sa_family=AF_UNIX, sun_path=@"/run/avahi-daemon/socket"}, 27) = 0\n'
    )
    assert trace.local_resolvers == {}
    assert trace.unix_sockets == {"@/run/avahi-daemon/socket": 1}


def execve(program: str, *argv: str) -> str:
    args = ", ".join(f'"{arg}"' for arg in argv)
    return f'4 execve("{program}", [{args}], 0x0 /* 1 var */) = 0\n'


@pytest.mark.parametrize("line, command", [
    (execve("/usr/bin/git", "git", "push", "origin"), "git push origin"),
    (execve("/usr/bin/git", "git", "pull"), "git pull"),
    (execve("/usr/bin/git", "git", "clone", "https://example.com/r.git"), "git clone https://example.com/r.git"),
    (execve("/usr/lib/git-core/git-remote-http", "git-remote-http", "origin", "http://example.com/r.git"),
     "git-remote-http origin http://example.com/r.git"),
])
def test_every_git_command_that_reaches_a_network_is_recorded(line, command):
    assert StraceParser.parse(line).networked_git == [command]


@pytest.mark.parametrize("option", ["--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"])
def test_an_option_value_before_the_subcommand_does_not_hide_a_fetch(option):
    # Each of these takes the next word as its value; read as the subcommand, it would hide the fetch.
    trace = StraceParser.parse(execve("/usr/bin/git", "git", option, "value", "fetch", "origin"))
    assert trace.networked_git == [f"git {option} value fetch origin"]
