# Optional tensorizer-enabled variant of the pinned DGXCoder vLLM runtime.
#
# DGXCoder launches nvcr.io/nvidia/vllm:26.07-py3 directly by default, so no build is required.
# Build this image only if that tag does not ship the `tensorizer` package and you want faster
# weight loading. Keep the FROM tag in sync with DEFAULT_VLLM_IMAGE in
# dgxcoder/vllm_server/vllm_server_manager.py.
#
#   docker build -t dgxcoder-vllm-tensorizer:26.07-py3 .
#
# Then pass it via the docker_image argument of VLLMServerManager.build_launch_command().
FROM nvcr.io/nvidia/vllm:26.07-py3


# Install tensorizer optional dependencies for fast model weight loading
RUN pip install "vllm[tensorizer]"
RUN pip install ray
#this should be done as the last step to xgrammar is not overriden
RUN python -m pip install --no-cache-dir --no-deps --force-reinstall "xgrammar==0.2.4"

ENTRYPOINT ["vllm", "serve"]
