FROM vllm/vllm-openai:latest

# Install tensorizer optional dependencies for fast model weight loading
RUN pip install "vllm[tensorizer]"
