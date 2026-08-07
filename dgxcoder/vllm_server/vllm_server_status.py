from dataclasses import dataclass
from typing import List, Optional

@dataclass
class VLLMServerStatus:
    host: str
    healthy: bool
    models: List[str]
    pid: Optional[int]
