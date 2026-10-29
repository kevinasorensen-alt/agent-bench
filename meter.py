"""Metering proxy: an OpenAI-compatible pass-through that counts every token a contestant uses.

Contestants call http://host.docker.internal:<port>/m/<contestant id>/v1/... with a dummy key. The proxy
adds the real provider key (which never enters a container), forwards the call, and appends each
response's token usage to <meter dir>/<contestant id>.jsonl. The bench turns those lines into cost and
pane lines. Listens on 127.0.0.1 only.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOP_HEADERS = {"host", "authorization", "content-length", "connection", "accept-encoding", "transfer-encoding"}


def load_dotenv(path):
    """KEY=value lines from a .env file, without touching os.environ."""
    out = {}
    if Path(path).is_file():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def usage_from(obj):
    """Token usage from a chat-completions response/chunk, or from a Responses API response/event."""
    obj = obj or {}
    if isinstance(obj.get("response"), dict):  # Responses API stream event, e.g. response.completed
        obj = obj["response"]
    u = obj.get("usage") or {}
    if not u:
        return None
    if "input_tokens" in u:  # Responses API
        cached = (u.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0
        return {"input": u.get("input_tokens", 0) or 0, "cached_input": cached, "output": u.get("output_tokens", 0) or 0}
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
    return {"input": u.get("prompt_tokens", 0) or 0, "cached_input": cached, "output": u.get("completion_tokens", 0) or 0}


class Meter:
    def __init__(self, upstream_base, api_key, meter_dir):
        self.upstream = upstream_base.rstrip("/")
        self.key = api_key
        self.dir = Path(meter_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        meter = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                meter.forward(self, None)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                meter.forward(self, body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]

    def start(self):
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.server.shutdown()

    def url_for(self, contestant_id):
        return f"http://host.docker.internal:{self.port}/m/{contestant_id}/v1"

    def record(self, cid, model, usage, status, started=None):
        line = {"t": round(time.time(), 2), "t_start": round(started, 2) if started else None,
                "model": model, "status": status, **(usage or {})}
        with self.lock, open(self.dir / f"{cid}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

    def forward(self, h, body):
        parts = h.path.split("/", 3)  # ['', 'm', '<id>', 'v1/...']
        if len(parts) < 4 or parts[1] != "m" or not parts[3].startswith("v1"):
            h.send_error(404, "use /m/<contestant id>/v1/...")
            return
        cid, rest = parts[2], parts[3][2:]
        started = time.time()
        req_json, model, stream = None, "", False
        if body:
            try:
                req_json = json.loads(body)
                model, stream = req_json.get("model", ""), bool(req_json.get("stream"))
                if stream and rest.rstrip("/").endswith("chat/completions"):  # ask for the usage chunk at the end
                    req_json.setdefault("stream_options", {})["include_usage"] = True
                    body = json.dumps(req_json).encode()
            except ValueError:
                pass
        headers = {k: v for k, v in h.headers.items() if k.lower() not in HOP_HEADERS}
        headers["Authorization"] = f"Bearer {self.key}"
        req = urllib.request.Request(self.upstream + rest, data=body, headers=headers, method=h.command)
        try:
            resp = urllib.request.urlopen(req, timeout=600)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.record(cid, model, None, e.code)
            h.send_response(e.code)
            h.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data)
            return
        except OSError as e:
            self.record(cid, model, None, "network error")
            h.send_error(502, str(e)[:200])
            return
        h.send_response(resp.status)
        ctype = resp.headers.get("Content-Type", "")
        h.send_header("Content-Type", ctype)
        if "text/event-stream" in ctype:
            h.send_header("Connection", "close")
            h.end_headers()
            usage = None
            for raw in resp:
                h.wfile.write(raw)
                h.wfile.flush()
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
                    try:
                        usage = usage_from(json.loads(line[5:])) or usage
                    except ValueError:
                        pass
            self.record(cid, model, usage, resp.status, started)
            h.close_connection = True
        else:
            data = resp.read()
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data)
            try:
                self.record(cid, model, usage_from(json.loads(data)), resp.status, started)
            except ValueError:
                pass
