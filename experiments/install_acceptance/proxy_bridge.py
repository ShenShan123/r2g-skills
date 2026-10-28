"""Forward an existing loopback download proxy to one isolated test container.

No credentials or request data are logged. Bind to the Docker gateway only,
and reject every source address except the explicitly selected container.
Stop this process when the acceptance run ends.
"""
import argparse
import select
import socket
import socketserver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--client", required=True)
    parser.add_argument("--port", type=int, default=20880)
    parser.add_argument("--upstream-port", type=int, default=2080)
    args = parser.parse_args()

    class Relay(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                with socket.create_connection(("127.0.0.1", args.upstream_port), timeout=30) as upstream:
                    upstream.settimeout(60)
                    self.request.settimeout(60)
                    peers = {upstream: self.request, self.request: upstream}
                    while True:
                        ready, _, _ = select.select(list(peers), [], [], 120)
                        if not ready:
                            return
                        for source in ready:
                            data = source.recv(65536)
                            if not data:
                                return
                            peers[source].sendall(data)
            except OSError:
                return

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

        def verify_request(self, request, client_address):
            return client_address[0] == args.client

    with Server((args.gateway, args.port), Relay) as server:
        print("Download proxy bridge ready for the selected container", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
