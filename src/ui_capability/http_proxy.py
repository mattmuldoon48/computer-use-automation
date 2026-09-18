"""Fail-closed HTTP proxy for the local fixture, including Chromium downloads.

Playwright routing alone does not see every browser-process download. This
second boundary only forwards explicitly reviewed HTTP requests to one loopback
origin. CONNECT/HTTPS tunnelling is deliberately unsupported. This is not an OS
network sandbox and must not be represented as one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class LoopbackProxy:
    def __init__(
        self, origin: str, permits: Callable[[str, str], bool], deny: Callable[[], None]
    ) -> None:
        endpoint = urlsplit(origin)
        if endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or not endpoint.port:
            raise ValueError("the fixture proxy requires an explicit loopback HTTP origin")
        host, port = endpoint.hostname, endpoint.port
        authority = endpoint.netloc

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, format: str, *args: object) -> None:
                pass  # Never log request targets, headers, or bodies.

            def send_error(
                self, code: int, message: str | None = None, explain: str | None = None
            ) -> None:
                deny()
                self.close_connection = True
                self.send_response(403)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()

            def do_GET(self) -> None:
                self.forward()

            def do_POST(self) -> None:
                self.forward()

            def forward(self) -> None:
                self.close_connection = True
                upstream: HTTPConnection | None = None
                try:
                    if not permits(self.command, self.path):
                        self.send_error(403)
                        return
                    if any(
                        len(self.headers.get_all(name, [])) > 1
                        for name in ("Host", "Content-Length", "Content-Type")
                    ) or any(name in self.headers for name in ("Transfer-Encoding", "Upgrade")):
                        self.send_error(403)
                        return
                    content_type = self.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    length_text = self.headers.get("Content-Length", "0")
                    if (
                        self.headers.get("Host") != authority
                        or not length_text.isascii()
                        or not length_text.isdecimal()
                        or len(length_text) > 5
                    ):
                        self.send_error(403)
                        return
                    length = int(length_text)
                    if (
                        length > 8192
                        or (self.command == "GET" and length != 0)
                        or (
                            self.command == "POST"
                            and content_type != "application/x-www-form-urlencoded"
                        )
                    ):
                        self.send_error(403)
                        return
                    body = self.rfile.read(length)
                    if len(body) != length:
                        self.send_error(403)
                        return
                    headers = {
                        name: value
                        for name, value in self.headers.items()
                        if name.lower()
                        not in {"host", "connection", "proxy-connection", "proxy-authorization"}
                    }
                    headers.update({"Host": authority, "Connection": "close"})
                    upstream = HTTPConnection(host, port, timeout=5)
                    upstream.request(self.command, urlsplit(self.path).path, body, headers)
                    response = upstream.getresponse()
                    response_headers = response.getheaders()
                    if (
                        300 <= response.status < 400
                        or response.getheader("Content-Type", "").split(";", 1)[0].lower()
                        != "text/html"
                        or any(
                            name.lower() in {"refresh", "content-disposition"}
                            for name, _ in response_headers
                        )
                    ):
                        self.send_error(403)
                        return
                    self.send_response(response.status)
                    for name, value in response_headers:
                        # http.client removes chunk framing while reading. Closing
                        # the downstream connection safely delimits that body.
                        if name.lower() not in {
                            "connection",
                            "transfer-encoding",
                            "server",
                            "date",
                        }:
                            self.send_header(name, value)
                    self.send_header("Connection", "close")
                    self.end_headers()
                    while chunk := response.read(65536):
                        self.wfile.write(chunk)
                except (OSError, ValueError):
                    deny()
                finally:
                    if upstream is not None:
                        upstream.close()

        class Server(ThreadingHTTPServer):
            daemon_threads = False

            def handle_error(self, request: object, client_address: object) -> None:
                deny()  # No exception text or raw request data in server stderr.

        self.__server = Server(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.__server.server_port}"
        self.__thread = threading.Thread(
            target=lambda: self.__server.serve_forever(poll_interval=0.05), daemon=True
        )
        self.__thread.start()

    def close(self) -> None:
        self.__server.shutdown()
        self.__server.server_close()
        self.__thread.join(timeout=5)
