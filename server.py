"""
Mastery Loop - local server
---------------------------
Serves the web app and forwards AI requests to Groq or Google Gemini,
so the API key stays on this computer and never appears in the browser.

Run:   python server.py        (Python 3.8+, no packages to install)
Then:  http://localhost:8000   (opens automatically)

Configure in a .env file next to this script:
    LLM_API_KEY=gsk_...        # Groq key (gsk_...) or Gemini key (AIza...)
    LLM_MODEL=                 # optional, e.g. llama-3.3-70b-versatile or gemini-2.5-flash
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8000"))
UA = "MasteryLoop/1.0 (+student project)"  # Groq's CDN rejects the default Python user agent


def load_env():
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
KEY = os.environ.get("LLM_API_KEY", "").strip()
PROVIDER = os.environ.get("LLM_PROVIDER", "").strip().lower() or (
    "groq" if KEY.startswith("gsk_") else "gemini" if KEY.startswith("AIza") else "")
MODEL = os.environ.get("LLM_MODEL", "").strip()

GROQ = "https://api.groq.com/openai/v1"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
GROQ_PREFERRED = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.1-8b-instant"]
GEMINI_PREFERRED = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-flash-latest", "gemini-2.5-pro"]

state = {"checked": False, "ok": False, "error": "", "model": MODEL}
lock = threading.Lock()


def http(method, url, body=None, headers=None, timeout=90):
    h = {"User-Agent": UA, "Content-Type": "application/json"}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode() or "{}")
        except Exception:
            payload = {}
        return e.code, payload


def err_text(payload, status):
    e = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(e, dict):
        return e.get("message") or json.dumps(e)[:300]
    return str(e or f"HTTP {status}")


def list_models():
    if PROVIDER == "groq":
        s, p = http("GET", f"{GROQ}/models", headers={"Authorization": f"Bearer {KEY}"}, timeout=20)
        if s != 200:
            raise RuntimeError(f"Groq rejected the key or request: {err_text(p, s)}")
        return [m["id"] for m in p.get("data", [])]
    s, p = http("GET", f"{GEMINI}/models?key={KEY}&pageSize=200", timeout=20)
    if s != 200:
        raise RuntimeError(f"Gemini rejected the key or request: {err_text(p, s)}")
    return [m["name"].split("/", 1)[-1] for m in p.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])]


def pick_model(models):
    if MODEL and MODEL in models:
        return MODEL
    for m in (GROQ_PREFERRED if PROVIDER == "groq" else GEMINI_PREFERRED):
        if m in models:
            return m
    skip = ("whisper", "guard", "tts", "embed", "vision", "image", "audio", "compound")
    usable = [m for m in models if not any(x in m for x in skip)]
    return usable[0] if usable else (models[0] if models else "")


def check():
    with lock:
        if state["checked"]:
            return state
        state["checked"] = True
        if not KEY:
            state["error"] = "No LLM_API_KEY in .env"
        elif PROVIDER not in ("groq", "gemini"):
            state["error"] = "Unrecognised key. Use a Groq key (gsk_...) or a Gemini key (AIza...), or set LLM_PROVIDER."
        else:
            try:
                state["model"] = pick_model(list_models())
                state["ok"] = bool(state["model"])
                if not state["ok"]:
                    state["error"] = "No usable text model found for this key"
            except Exception as e:
                state["error"] = str(e)
        return state


def label():
    m = state["model"]
    if PROVIDER == "gemini":
        return "Gemini"
    if "llama-3.3" in m:
        return "Llama 3.3 (Groq)"
    if "gpt-oss" in m:
        return "GPT-OSS (Groq)"
    return "Groq AI"


def complete(prompt, want_json):
    model = state["model"]
    if PROVIDER == "groq":
        body = {"model": model, "temperature": 0.4, "max_tokens": 3000,
                "messages": [{"role": "user", "content": prompt}]}
        if want_json:
            body["response_format"] = {"type": "json_object"}
        s, p = http("POST", f"{GROQ}/chat/completions", body, {"Authorization": f"Bearer {KEY}"})
        if s == 400 and want_json:  # some models reject json mode; retry without it
            body.pop("response_format", None)
            s, p = http("POST", f"{GROQ}/chat/completions", body, {"Authorization": f"Bearer {KEY}"})
        if s != 200:
            return s, {"error": err_text(p, s)}
        return 200, {"text": p["choices"][0]["message"].get("content") or ""}
    cfg = {"temperature": 0.4, "maxOutputTokens": 3000}
    if want_json:
        cfg["responseMimeType"] = "application/json"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": cfg}
    s, p = http("POST", f"{GEMINI}/models/{model}:generateContent?key={KEY}", body)
    if s != 200:
        return s, {"error": err_text(p, s)}
    parts = (p.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    return 200, {"text": "".join(x.get("text", "") for x in parts if not x.get("thought"))}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, obj=None, raw=None, ctype="application/json"):
        data = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                html = f.read()
            if not html.lstrip().lower().startswith(b"<!doctype"):
                html = (b'<!doctype html><html lang="en"><head><meta charset="utf-8">'
                        b'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
                        b'<style>body{margin:0}[hidden]{display:none!important}img{max-width:100%}</style>'
                        b'</head><body>' + html + b'</body></html>')
            return self.send(200, raw=html, ctype="text/html; charset=utf-8")
        if path == "/api/health":
            st = check()
            return self.send(200, {"ok": st["ok"], "provider": PROVIDER, "model": st["model"],
                                   "label": label(), "error": st["error"]})
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/llm":
            return self.send(404, {"error": "not found"})
        if not check()["ok"]:
            return self.send(503, {"error": state["error"]})
        try:
            n = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(min(n, 200_000)) or b"{}")
            prompt = str(req.get("prompt", ""))[:60_000]
            if not prompt.strip():
                return self.send(400, {"error": "empty prompt"})
            code, out = complete(prompt, bool(req.get("json")))
            return self.send(code if code in (200, 429) else 502, out)
        except Exception as e:
            return self.send(502, {"error": str(e)})

    def log_message(self, fmt, *args):
        if "/api/" in (args[0] if args else ""):
            sys.stderr.write("  " + (fmt % args) + "\n")


if __name__ == "__main__":
    st = check()
    print("\n  Mastery Loop")
    print(f"  AI provider : {PROVIDER or '-'}   model: {st['model'] or '-'}")
    print(f"  AI status   : {'connected' if st['ok'] else 'OFFLINE - ' + st['error']}")
    if not st["ok"]:
        print("  (The app still works using its built-in question bank.)")
    url = f"http://localhost:{PORT}"
    print(f"  Open        : {url}   (Ctrl+C to stop)\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
