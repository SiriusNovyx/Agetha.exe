"""Opt-in HTTP client for a separately hosted Unlimited-OCR service."""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    import requests
except ImportError:  # Agetha still starts; Medic reports missing main requirements.
    requests = None

from .base import OCRResult


DEFAULT_PROMPT = "<image>document parsing."
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def normalize_server_url(value: str) -> str:
    """Validate and normalize an HTTP(S) service URL without resolving DNS."""
    raw = str(value or "").strip()
    if not raw or len(raw) > 2048:
        raise ValueError("invalid_server_url")
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except (TypeError, ValueError):
        raise ValueError("invalid_server_url") from None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid_server_url")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


def is_local_server_url(value: str) -> bool:
    try:
        host = (urlsplit(normalize_server_url(value)).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOCAL_HOSTS


def completion_endpoint(value: str) -> str:
    base = normalize_server_url(value)
    if base.endswith("/v1/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


class UnlimitedOCRBackend:
    """Send one explicitly captured image to an OpenAI-compatible endpoint."""

    name = "unlimited_ocr"

    def __init__(
        self,
        *,
        server_url: str,
        model: str = "Unlimited-OCR",
        timeout_seconds: int = 180,
        allow_remote: bool = False,
        max_output_chars: int = 12000,
        api_key: str = "",
        session=None,
        temp_dir: str | os.PathLike[str] | None = None,
    ):
        self.server_url = str(server_url or "").strip()
        self.model = str(model or "Unlimited-OCR").strip() or "Unlimited-OCR"
        self.timeout_seconds = max(10, min(int(timeout_seconds), 1200))
        self.allow_remote = bool(allow_remote)
        self.max_output_chars = max(1, int(max_output_chars))
        self.api_key = str(api_key or "").strip()
        self._temp_dir = temp_dir
        self._session = session
        if self._session is None and requests is not None:
            self._session = requests.Session()
        if self._session is not None:
            self._session.trust_env = False

    def configuration_error(self) -> tuple[str, str] | None:
        try:
            normalize_server_url(self.server_url)
        except ValueError:
            return (
                "invalid_server_url",
                "Unlimited-OCR server URL is invalid. Standard Tesseract OCR is still available.",
            )
        if not self.allow_remote and not is_local_server_url(self.server_url):
            return (
                "remote_server_blocked",
                "Remote deep OCR is disabled. Standard Tesseract OCR is still available.",
            )
        if self._session is None:
            return (
                "requests_unavailable",
                "Deep OCR is unavailable because the HTTP client is missing. Standard Tesseract OCR is still available.",
            )
        return None

    def _error(self, code: str, message: str) -> OCRResult:
        return OCRResult(
            text=message,
            words=[],
            backend=self.name,
            metadata={"status": "error", "error": code},
        )

    @staticmethod
    def _content_from_object(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return None
        choice = choices[0]
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else choice.get("text")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "".join(parts) if parts else None
        return None

    @classmethod
    def _content_from_response(cls, response) -> str | None:
        try:
            content = cls._content_from_object(response.json())
            if content is not None:
                return content
        except (ValueError, TypeError, AttributeError):
            pass

        # Some compatible servers return SSE even when a non-streaming response
        # was requested. Parsing response text keeps this client dependency-light.
        chunks: list[str] = []
        for line in str(getattr(response, "text", "") or "").splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
                choice = (event.get("choices") or [{}])[0]
                delta = choice.get("delta") if isinstance(choice, dict) else None
                text = delta.get("content") if isinstance(delta, dict) else None
                if isinstance(text, str):
                    chunks.append(text)
            except (ValueError, TypeError, AttributeError, IndexError):
                continue
        return "".join(chunks) if chunks else None

    def analyze(self, image, *, prompt: str = DEFAULT_PROMPT) -> OCRResult:
        problem = self.configuration_error()
        if problem:
            return self._error(*problem)
        if image is None:
            return self._error("capture_failed", "Deep OCR could not capture the screen.")

        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(
                prefix="agetha_deep_ocr_", suffix=".png", dir=self._temp_dir,
            )
            os.close(fd)
            image.save(temp_path, format="PNG")
            encoded = base64.b64encode(Path(temp_path).read_bytes()).decode("ascii")
            payload = {
                "model": self.model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": (str(prompt or DEFAULT_PROMPT)[:2000])},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encoded}"},
                        },
                    ],
                }],
                "temperature": 0,
                "stream": False,
            }
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            response = self._session.post(
                completion_endpoint(self.server_url),
                json=payload,
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            content = self._content_from_response(response)
            if content is None:
                return self._error(
                    "malformed_response",
                    "Unlimited-OCR returned an unreadable response. Standard Tesseract OCR is still working.",
                )
            truncated = len(content) > self.max_output_chars
            content = content[:self.max_output_chars]
            return OCRResult(
                text=content,
                words=[],
                backend=self.name,
                structured_content=content,
                metadata={"status": "ok", "truncated": truncated},
            )
        except Exception as exc:
            if requests is not None and isinstance(exc, requests.exceptions.Timeout):
                return self._error(
                    "timeout",
                    "Unlimited-OCR timed out. Standard Tesseract OCR is still working.",
                )
            if requests is not None and isinstance(exc, requests.exceptions.RequestException):
                return self._error(
                    "connection_failed",
                    "Unlimited-OCR could not be reached. Standard Tesseract OCR is still working. Check the server URL and confirm the OCR service is running.",
                )
            return self._error(
                "request_failed",
                "Deep OCR failed safely. Standard Tesseract OCR is still working.",
            )
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def close(self) -> None:
        try:
            if self._session is not None:
                self._session.close()
        except Exception:
            pass
