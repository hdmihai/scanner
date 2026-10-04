"""selfrepair.llm - modele GRATUITE: Gemini (cheie gratuita din AI Studio), apoi Ollama local."""
import json
import os
import urllib.request


def _post(url, body, headers=None, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _gemini(prompt):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY lipseste")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        d = _post(url, {"contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}},
                  headers={"x-goog-api-key": key}, timeout=180)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini HTTP {e.code}") from None      # fara cheia in mesaj
    return d["candidates"][0]["content"]["parts"][0]["text"]


def _ollama(prompt):
    base = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:7b")
    d = _post(f"{base}/api/generate", {"model": model, "prompt": prompt, "stream": False,
                                       "format": "json", "options": {"temperature": 0.1, "num_ctx": 32768}})
    return d["response"]


def complete(prompt):
    """(text, sursa). Incearca Gemini, apoi Ollama; cauzele esecurilor sunt pastrate."""
    errs = []
    for name, fn in (("gemini", _gemini), ("ollama", _ollama)):
        try:
            return fn(prompt), name
        except Exception as e:
            errs.append(f"{name}: {e}")
    raise RuntimeError("niciun model disponibil - " + "; ".join(errs))
