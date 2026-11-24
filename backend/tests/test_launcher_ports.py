"""The launcher must not mistake another program on its port for itself (v0.4.1).

Other local apps (and many dev servers) listen on port 8000. The launcher used to see "port taken",
conclude "the app is already running" and only open the browser: nothing started.
"""

import http.server
import socket
import threading

from app import launcher


def test_free_ports_start_on_the_usual_ones():
    assert launcher.pick_ports(8000, 5173, "main", probe=lambda p: None, free=lambda p: True) == ("start", 8000, 5173)


def test_another_program_on_the_port_moves_the_app_to_the_next_free_one():
    taken = {8000}  # not this app: the probe finds no health answer
    assert launcher.pick_ports(8000, 5173, "main", probe=lambda p: None, free=lambda p: p not in taken) == ("start", 8001, 5173)
    taken = {8000, 8001, 5173}
    assert launcher.pick_ports(8000, 5173, "main", probe=lambda p: None, free=lambda p: p not in taken) == ("start", 8002, 5174)


def test_this_app_already_running_is_found_also_on_a_moved_port():
    def probe(port):
        return {"status": "ok", "profile": "main", "media_dir": "x", "frontend_port": 5174} if port == 8001 else None

    assert launcher.pick_ports(8000, 5173, "main", probe=probe, free=lambda p: p != 8000) == ("running", 8001, 5174)


def test_the_same_app_with_another_profile_is_not_this_one():
    def probe(port):
        return {"status": "ok", "profile": "test", "media_dir": "x", "frontend_port": 5173} if port == 8000 else None

    assert launcher.pick_ports(8000, 5173, "main", probe=probe, free=lambda p: p != 8000) == ("start", 8001, 5173)


def test_a_foreign_web_server_is_not_taken_for_the_app():
    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # answers every path, like a single-page app does
            body = b"<html>some other program</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Other)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert launcher.port_in_use(port) and not launcher.port_free(port)
        assert launcher.app_on(port) is None
    finally:
        server.shutdown()
        server.server_close()


def test_a_closed_port_is_free():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert launcher.port_free(port) and launcher.app_on(port) is None
