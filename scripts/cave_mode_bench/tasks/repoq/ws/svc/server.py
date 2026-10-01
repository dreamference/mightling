from svc.settings import listen_address


def serve():
    host, port = listen_address()
    print(f"listening on {host}:{port}")
