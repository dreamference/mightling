from unittest.mock import patch

from dreamference.chat.docker_bridge import DockerBridge


def test_loopback_vllm_host_is_rewritten_to_the_docker_gateway():
    # A bridged container's localhost is the container itself. Without this rewrite the client is
    # configured successfully and then simply never connects.
    with patch.object(DockerBridge, "gateway", return_value="172.17.0.1"):
        assert DockerBridge.container_vllm_url("http://localhost:8000") == (
            "http://172.17.0.1:8000/v1"
        )
        assert DockerBridge.container_vllm_url("http://127.0.0.1:9001") == (
            "http://172.17.0.1:9001/v1"
        )


def test_routable_vllm_host_is_left_alone():
    with patch.object(DockerBridge, "gateway", return_value="172.17.0.1"):
        assert DockerBridge.container_vllm_url("http://10.0.0.5:8000") == (
            "http://10.0.0.5:8000/v1"
        )


def test_gateway_falls_back_when_docker_cannot_be_queried():
    with patch("subprocess.run", side_effect=OSError("no docker")):
        assert DockerBridge.gateway() == "172.17.0.1"
