"""
The services around the agent that are not the agent: the desktop app, the sidecars and the
Google service.

The desktop app is a window onto `ling app-server` (`desktop_installer`, `desktop_runner`).
`searxng_sidecar` and `sidecar_network` start the search container `ling-search` and the MCP server
share; `image_search_sidecar` and `image_search_mcp` are image search and its `image_search` tool,
`speech_sidecar` the speech-to-text behind the microphone (MIGHTLING_ASK §6, §7); `google_service`
starts the Google service, and `google_workspace_reader` is its read-only Drive and Calendar half
(Mightling's apps). `matrix_homeserver` runs the private Matrix homeserver `ling chat` answers on
(MIGHTLING_CHAT §5). The Onyx web chat this package was named for is retired (MIGHTLING_ASK §10);
`retired_web_chat` removes what an older install left of it.
"""

from dreamference.chat.desktop_installer import DesktopInstaller
from dreamference.chat.desktop_runner import DesktopRunner
from dreamference.chat.docker_bridge import DockerBridge
from dreamference.chat.gmail_client import GmailClient
from dreamference.chat.gmail_credentials import GmailCredentials
from dreamference.chat.gmail_search_service import GmailSearchService
from dreamference.chat.google_service import GoogleService
from dreamference.chat.google_workspace_reader import GoogleWorkspaceReader
from dreamference.chat.image_search_mcp import ImageSearchMcp
from dreamference.chat.image_search_sidecar import ImageSearchSidecar
from dreamference.chat.matrix_homeserver import MatrixHomeserver
from dreamference.chat.retired_web_chat import RetiredWebChat
from dreamference.chat.searxng_sidecar import SearxngSidecar
from dreamference.chat.sidecar_network import SidecarNetwork
from dreamference.chat.speech_sidecar import SpeechSidecar

__all__ = [
    "DesktopInstaller",
    "DesktopRunner",
    "DockerBridge",
    "GmailClient",
    "GmailCredentials",
    "GmailSearchService",
    "GoogleService",
    "GoogleWorkspaceReader",
    "ImageSearchMcp",
    "ImageSearchSidecar",
    "MatrixHomeserver",
    "RetiredWebChat",
    "SearxngSidecar",
    "SidecarNetwork",
    "SpeechSidecar",
]
