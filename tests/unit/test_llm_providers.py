import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from arcanum.llm import OllamaProvider, OpenCodeProvider, get_provider


class FakeOllama(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        body = json.dumps({"response": f"echo:{payload.get('prompt')}"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_ollama_provider():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = OllamaProvider(f"http://127.0.0.1:{server.server_port}")
        assert provider.complete("hello", model="test") == "echo:hello"
    finally:
        server.shutdown()
        server.server_close()


def test_opencode_provider_uses_cli(monkeypatch):
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("arcanum.llm.opencode.shutil.which", lambda path: None)
    monkeypatch.setattr("arcanum.llm.opencode.os.path.isfile", lambda path: True)
    provider = OpenCodeProvider(cli="/fake/opencode")
    assert provider.complete("hello", model="hy4-flash-free") == "ok"
    assert captured["args"] == [
        "/fake/opencode", "run", "--model", "hy4-flash-free", "hello"
    ]


def test_registry_selection(monkeypatch):
    monkeypatch.setenv("ARCANUM_LLM_PROVIDER", "ollama")
    assert isinstance(get_provider(), OllamaProvider)
    assert isinstance(get_provider("opencode"), OpenCodeProvider)
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        get_provider("nope")
