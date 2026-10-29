"""The strace parser on input it should not trust.

What counts as this machine, DNS payloads that are not queries, and calls that ran nothing. A wrong
answer here hides a destination from the verdict.
"""

import pytest

from dreamference.audit.strace_parser import StraceParser


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "127.8.9.1", "::1", "::ffff:127.0.0.1", "0.0.0.0", "::"]
)
def test_loopback_and_unspecified_addresses_are_this_machine(address):
    assert StraceParser.is_loopback(address)


@pytest.mark.parametrize(
    "address", ["203.0.113.7", "::ffff:203.0.113.7", "2001:4860:4860::8888", "localhost", ""]
)
def test_any_other_address_or_a_name_is_not_this_machine(address):
    assert not StraceParser.is_loopback(address)


def test_a_connect_to_an_ipv4_mapped_public_address_is_a_destination():
    trace = StraceParser.parse(
        "7 connect(5<TCPv6:[1]>, {sa_family=AF_INET6, sin6_port=htons(443), "
        'sin6_flowinfo=htonl(0), inet_pton(AF_INET6, "::ffff:203.0.113.7", &sin6_addr), '
        "sin6_scope_id=0}, 28) = -1 EINPROGRESS (Operation now in progress)\n"
    )
    assert trace.destinations, "an IPv4 host reached through a mapped address still counts"


def query(name_labels: bytes) -> bytes:
    return b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + name_labels


@pytest.mark.parametrize(
    "payload",
    [
        query(b"\x05ex;mp\x03com\x00"),  # a label with characters no host name has
        query(b"\x07example\x03com"),  # labels run to the end of the payload without a root
        query(b"\x40" + b"a" * 64 + b"\x00"),  # a label longer than 63
        query(b"\x09short\x00"),  # a label longer than what is left
    ],
)
def test_a_payload_that_is_not_a_well_formed_query_names_nothing(payload):
    assert StraceParser.dns_query_name(payload) is None


def test_a_query_for_the_root_names_nothing():
    assert StraceParser.dns_query_name(query(b"\x00")) is None


def test_a_descriptor_without_a_label_is_read_as_it_is():
    assert StraceParser.fd_label("23") == ("23", "", "")
    assert StraceParser.fd_label("23<UDPv6:[10868489]>") == ("23", "UDPv6", "10868489")


def test_a_failed_execve_is_not_a_program_that_ran():
    trace = StraceParser.parse(
        '4 execve("/usr/local/bin/git", ["git", "fetch"], 0x0 /* 1 var */) = -1 ENOENT (No such file or directory)\n'
        '4 execve("/usr/bin/git", ["git", "fetch"], 0x0 /* 1 var */) = 0\n'
    )
    assert trace.processes == {"git": 1} and trace.networked_git == ["git fetch"]


@pytest.mark.parametrize(
    "args, subcommand",
    [
        (["-C", "fetch", "status"], "status"),  # `fetch` is the folder -C names
        (["-c", "a=b", "--git-dir=/x", "fetch"], "fetch"),
        (["--version"], None),
        ([], None),
    ],
)
def test_git_options_and_their_values_are_not_the_subcommand(args, subcommand):
    assert StraceParser.git_subcommand(args) == subcommand


def test_a_udp_payload_on_an_unknown_socket_without_a_peer_counts_nowhere():
    trace = StraceParser.parse('9 write(31<UDP:[555]>, "x", 1) = 1\n')
    assert trace.destinations == {} and trace.lines == 1


def test_only_a_query_for_one_question_names_it_in_lower_case():
    payload = query(b"\x05Audit\x07EXAMPLE\x03com\x00")
    assert StraceParser.dns_query_name(payload) == "audit.example.com"
    assert StraceParser.dns_query_name(payload[:4] + b"\x00\x00" + payload[6:]) is None
    assert StraceParser.dns_query_name(payload[:4] + b"\x00\x02" + payload[6:]) is None
