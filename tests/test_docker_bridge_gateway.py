"""The container's way to the host when docker answers but names no gateway."""

import subprocess
from unittest.mock import patch

from dreamference.chat.docker_bridge import DEFAULT_BRIDGE_GATEWAY, DockerBridge


def test_an_empty_or_failed_gateway_answer_falls_back_to_dockers_default():
    for answer in (
        subprocess.CompletedProcess([], 0, stdout="\n", stderr=""),
        subprocess.CompletedProcess([], 1, stdout="", stderr="Error: No such network: bridge"),
    ):
        with patch("subprocess.run", return_value=answer):
            assert DockerBridge.gateway() == DEFAULT_BRIDGE_GATEWAY == "172.17.0.1"
            assert DockerBridge.container_vllm_url("http://localhost:8000") == (
                "http://172.17.0.1:8000/v1"
            )
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 15)):
        assert DockerBridge.gateway() == DEFAULT_BRIDGE_GATEWAY


def test_a_gateway_docker_names_is_used_as_given():
    answer = subprocess.CompletedProcess([], 0, stdout=" 10.88.0.1\n", stderr="")
    with patch("subprocess.run", return_value=answer):
        assert DockerBridge.gateway() == "10.88.0.1"
