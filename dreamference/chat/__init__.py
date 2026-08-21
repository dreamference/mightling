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
in a window of its own (`desktop_installer`, `desktop_runner`).
"""

from dreamference.chat.desktop_installer import DesktopInstaller
from dreamference.chat.desktop_runner import DesktopRunner
from dreamference.chat.gmail_credentials import GmailCredentials
from dreamference.chat.gmail_search_service import GmailSearchService
from dreamference.chat.onyx_brand_assets import OnyxBrandAssets
from dreamference.chat.onyx_installer import OnyxInstaller
from dreamference.chat.onyx_runner import OnyxRunner
from dreamference.chat.onyx_ui_fonts import OnyxUIFonts
from dreamference.chat.onyx_ui_labels import OnyxUILabels
from dreamference.chat.onyx_ui_overrides import OnyxUIOverrides
from dreamference.chat.onyx_ui_scripts import OnyxUIScripts

__all__ = [
    "DesktopInstaller",
    "DesktopRunner",
    "GmailCredentials",
    "GmailSearchService",
    "OnyxBrandAssets",
    "OnyxInstaller",
    "OnyxRunner",
    "OnyxUIFonts",
    "OnyxUILabels",
    "OnyxUIOverrides",
    "OnyxUIScripts",
]
