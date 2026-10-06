"""
Onyx Lite chat subsystem for Dreamference.

Onyx is a *service*, not one of the terminal agents: a browser chat UI in front of the same vLLM
endpoint the agents use, run as a set of long-lived containers rather than a CLI that Dreamference
execs and waits on. That is why it lives here rather than under `runner/`, whose modules are all
one-process-per-invocation agent wrappers.

The package holds the deployment lifecycle (`onyx_installer`, `onyx_runner`), the three kinds of
patch Dreamference applies to a running Onyx -- replaced brand assets (`onyx_brand_assets`),
substituted and appended stylesheets (`onyx_ui_fonts`, `onyx_ui_overrides`), and rewritten strings
in the compiled bundle (`onyx_ui_labels`) -- and the desktop shell that offers the same deployment
in a window of its own (`desktop_installer`, `desktop_runner`). `searxng_sidecar` and
`sidecar_network` start the search container the web UI, `puffin-search` and the MCP server share;
`google_service` starts the Google service without the web UI, and `google_workspace_reader` is its
read-only Drive and Calendar half (Puffin's apps).
"""

from dreamference.chat.desktop_installer import DesktopInstaller
from dreamference.chat.desktop_runner import DesktopRunner
from dreamference.chat.gmail_client import GmailClient
from dreamference.chat.gmail_credentials import GmailCredentials
from dreamference.chat.gmail_search_service import GmailSearchService
from dreamference.chat.google_service import GoogleService
from dreamference.chat.google_workspace_reader import GoogleWorkspaceReader
from dreamference.chat.onyx_brand_assets import OnyxBrandAssets
from dreamference.chat.onyx_installer import OnyxInstaller
from dreamference.chat.onyx_runner import OnyxRunner
from dreamference.chat.onyx_ui_fonts import OnyxUIFonts
from dreamference.chat.onyx_ui_labels import OnyxUILabels
from dreamference.chat.onyx_ui_overrides import OnyxUIOverrides
from dreamference.chat.onyx_ui_scripts import OnyxUIScripts
from dreamference.chat.searxng_sidecar import SearxngSidecar
from dreamference.chat.sidecar_network import SidecarNetwork

__all__ = [
    "DesktopInstaller",
    "DesktopRunner",
    "GmailClient",
    "GmailCredentials",
    "GmailSearchService",
    "GoogleService",
    "GoogleWorkspaceReader",
    "OnyxBrandAssets",
    "OnyxInstaller",
    "OnyxRunner",
    "OnyxUIFonts",
    "OnyxUILabels",
    "OnyxUIOverrides",
    "OnyxUIScripts",
    "SearxngSidecar",
    "SidecarNetwork",
]
