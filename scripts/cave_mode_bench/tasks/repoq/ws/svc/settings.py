import os

from svc import defaults


def listen_address():
    host = os.environ.get("SVC_BIND_HOST", defaults.HOST)
    port = int(os.environ.get("SVC_LISTEN_PORT", defaults.PORT))
    return host, port
