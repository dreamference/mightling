"""
Default Configuration Generator.

Handles the creation of the sparse default config template during initialization.
"""
from pathlib import Path

def generate_default_init_config(target_path: Path) -> Path:
    """
    Generates the default sparse dreamference.toml file for the 'init' command.
    
    Args:
        target_path (Path): The file path where the config should be written.
        
    Returns:
        Path: The path to the written configuration file.
    """
    default_content = (
        'vllm_host = "http://localhost:8000"\n'
        'agent_runner = "codex"\n'
    )
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(default_content)
        
    return target_path
