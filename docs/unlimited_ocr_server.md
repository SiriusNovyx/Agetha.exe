# Optional Unlimited-OCR service

Agetha always uses Tesseract for automatic screen monitoring and spatial word
coordinates. Unlimited-OCR is a separate, opt-in service used only by the
`analyze_screen_deep` command after a direct user request.

## Agetha configuration

Add these non-secret settings to `config.txt` (or change them in Dashboard →
Settings → Screen / OCR):

```ini
DEEP_OCR_BACKEND = unlimited_ocr
UNLIMITED_OCR_SERVER_URL = http://127.0.0.1:10000
UNLIMITED_OCR_MODEL = Unlimited-OCR
UNLIMITED_OCR_TIMEOUT_SECONDS = 180
UNLIMITED_OCR_ALLOW_REMOTE = no
DEEP_OCR_MAX_OUTPUT_CHARS = 12000
```

Most local servers need no key. If yours does, put
`UNLIMITED_OCR_API_KEY=...` in `.env`, never in `config.txt`.

Restart Agetha, then ask: "Use deep OCR to analyze the focused window." The AI
command shape is:

```json
{
  "command": "analyze_screen_deep",
  "focused_only": true,
  "prompt": "Extract and explain all visible text and layout."
}
```

Set `focused_only` to `false` only when a full-screen capture is intended.

## Loopback mock smoke test (no GPU)

For an end-to-end HTTP/UI smoke test, save this temporary script outside the
repository as `mock_unlimited_ocr.py`:

```python
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(size))
        assert self.path == "/v1/chat/completions"
        assert request["model"] == "Unlimited-OCR"
        body = json.dumps({
            "choices": [{"message": {"content": "Mock OCR result: table with two columns."}}]
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


ThreadingHTTPServer(("127.0.0.1", 10000), Handler).serve_forever()
```

From the Agetha repository, run it in a separate terminal with the project's
Python (replace the example path with the file you saved):

```powershell
.\venv\Scripts\python.exe C:\Temp\mock_unlimited_ocr.py
```

Use the local configuration above, restart Agetha, and ask: "Use deep OCR to
analyze the focused window." Approve the read-only confirmation. Agetha should
respond using `Mock OCR result: table with two columns.` Stop the mock with
Ctrl+C, repeat the request, and confirm Agetha reports that Unlimited-OCR is
unreachable while normal Tesseract OCR keeps working. Delete the temporary mock
script when finished.

## Running the separate service

Follow the official [Baidu Unlimited-OCR repository](https://github.com/baidu/Unlimited-OCR)
and its linked [vLLM deployment recipe](https://recipes.vllm.ai/baidu/Unlimited-OCR).
The official setup uses a separately managed Python environment and primarily
targets NVIDIA CUDA systems. Do not install its PyTorch, Transformers, vLLM, or
SGLang packages into Agetha's virtual environment.

The service must expose an OpenAI-compatible endpoint at
`/v1/chat/completions`, accept base64 `image_url` content, and serve the model
name configured by `UNLIMITED_OCR_MODEL`. The official SGLang example uses port
`10000` and served model name `Unlimited-OCR`.

## Remote service privacy

Only `localhost`, `127.0.0.1`, and `::1` are accepted by default. To use another
machine, set its explicit HTTPS or HTTP URL and set
`UNLIMITED_OCR_ALLOW_REMOTE = yes`. That machine will receive the captured
screenshot whenever you explicitly request deep OCR. Agetha never sends a deep
capture during ambient polling and never falls back to a different remote OCR
service.

If the service is offline, times out, or returns malformed data, Agetha reports a
controlled error. Tesseract remains available for normal screen reading.
