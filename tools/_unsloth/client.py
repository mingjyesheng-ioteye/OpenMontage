"""Thin REST client for a local Unsloth Studio server.

Covers the two media paths OpenMontage needs:

  images  POST /v1/images/generations            (OpenAI-compatible, b64 or url)
  video   POST /api/inference/video/generate     (async job)
          GET  /api/inference/video/generate-progress
          GET  /api/inference/video/gallery/{id}/file

Auth is a bearer token. ``UNSLOTH_API_KEY`` (or ``UNSLOTH_TOKEN``) is preferred;
when only a username/password is configured the client exchanges it for a JWT
at ``POST /api/auth/login`` and refreshes it on demand.

Server URL resolution mirrors the ComfyUI client: a capability-specific
``UNSLOTH_<CAPABILITY>_SERVER_URL`` wins over the shared ``UNSLOTH_SERVER_URL``
(default ``http://127.0.0.1:8888``).
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Callable

import requests

DEFAULT_SERVER_URL = "http://127.0.0.1:8888"

# Studio ships a seeded bootstrap password and blocks the API until the owner
# changes it once; surface that instead of a bare 403.
_PASSWORD_CHANGE_HINT = (
    "Unsloth Studio is still using its seeded bootstrap password and rejects every "
    "API call until it is changed once. Open the Studio UI (Settings -> Account), "
    "change the password, then either create an API key (Settings -> API keys) and "
    "set UNSLOTH_API_KEY, or set UNSLOTH_USERNAME / UNSLOTH_PASSWORD."
)


class UnslothError(Exception):
    """Raised when Unsloth Studio returns an error or times out."""


class UnslothAuthError(UnslothError):
    """Raised for missing, rejected, or gated credentials."""


def to_data_url(source: str | Path, *, timeout: int = 60) -> str:
    """Return ``source`` as a ``data:`` URL.

    Accepts an existing data URL, an http(s) URL, or a local file path. The
    video endpoint takes base64/data-URL media (max 33 MB per image).
    """
    text = str(source)
    if text.startswith("data:"):
        return text
    if text.startswith(("http://", "https://")):
        resp = requests.get(text, timeout=timeout)
        resp.raise_for_status()
        mime = resp.headers.get("Content-Type", "").split(";")[0].strip()
        if not mime.startswith("image/"):
            mime = mimetypes.guess_type(text)[0] or "image/png"
        raw = resp.content
    else:
        path = Path(text)
        if not path.is_file():
            raise UnslothError(f"Reference media not found: {path}")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        raw = path.read_bytes()
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def snap_dimension(value: Any, defaults: dict[str, Any] | None) -> int:
    """Round a width/height down to the loaded family's resolution multiple."""
    defaults = defaults or {}
    multiple = int(defaults.get("resolution_multiple") or 16)
    value = max(multiple, int(value))
    return (value // multiple) * multiple


def snap_frames(frames: Any, defaults: dict[str, Any] | None) -> int:
    """Snap a frame count onto the loaded family's temporal lattice.

    Valid counts are ``k * frame_step + frame_offset``; the nearest valid count
    is clamped into ``[min_num_frames, max_num_frames]`` when the server
    reports them.
    """
    defaults = defaults or {}
    step = max(1, int(defaults.get("frame_step") or 1))
    offset = int(defaults.get("frame_offset") or 1)
    frames = max(1, int(frames))
    k = round((frames - offset) / step)
    snapped = k * step + offset
    low = defaults.get("min_num_frames")
    high = defaults.get("max_num_frames")
    if isinstance(low, int) and snapped < low:
        snapped = low
    if isinstance(high, int) and snapped > high:
        snapped = high
    return max(1, int(snapped))


class UnslothClient:
    """Client for one Unsloth Studio server."""

    def __init__(
        self, server_url: str | None = None, capability: str | None = None
    ) -> None:
        self.capability = capability
        self._capability_env_var = (
            f"UNSLOTH_{capability.upper()}_SERVER_URL" if capability else None
        )
        resolved = (
            server_url or self._capability_url() or os.environ.get("UNSLOTH_SERVER_URL")
        )
        self.server_url = (resolved or DEFAULT_SERVER_URL).rstrip("/")
        self._token: str | None = None
        self._token_expiry: float = 0.0
        # Populated by generate_video(): wall time split into submit / poll /
        # download so callers can separate GPU time from client overhead.
        self.last_timing: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def _capability_url(self) -> str | None:
        if self._capability_env_var:
            return os.environ.get(self._capability_env_var)
        return None

    @property
    def is_default_url(self) -> bool:
        return not (self._capability_url() or os.environ.get("UNSLOTH_SERVER_URL"))

    @staticmethod
    def api_key() -> str | None:
        return os.environ.get("UNSLOTH_API_KEY") or os.environ.get("UNSLOTH_TOKEN")

    @staticmethod
    def _username() -> str:
        return os.environ.get("UNSLOTH_USERNAME") or "unsloth"

    @classmethod
    def has_credentials(cls) -> bool:
        return bool(cls.api_key() or os.environ.get("UNSLOTH_PASSWORD"))

    def unavailable_reason(self) -> str:
        """Human-readable explanation of why the server cannot be used."""
        env_var_hint = self._capability_env_var or "UNSLOTH_SERVER_URL"
        if self.is_default_url:
            url_hint = (
                f"No Unsloth Studio server found at {self.server_url} (default — no "
                f"server URL configured).\nSet {env_var_hint} to your Studio address "
                f"(e.g. http://127.0.0.1:8888)."
            )
        else:
            url_hint = (
                f"Unsloth Studio not reachable at {self.server_url}.\n"
                f"Check that Unsloth Studio is running and the URL is correct."
            )
        if not self.has_credentials():
            return (
                f"{url_hint}\nNo credentials configured: set UNSLOTH_API_KEY (or "
                f"UNSLOTH_USERNAME / UNSLOTH_PASSWORD)."
            )
        return url_hint

    # ------------------------------------------------------------------
    # Auth + transport
    # ------------------------------------------------------------------

    def _login(self) -> str:
        password = os.environ.get("UNSLOTH_PASSWORD")
        if not password:
            raise UnslothAuthError(
                "No Unsloth credentials: set UNSLOTH_API_KEY, or UNSLOTH_USERNAME / "
                "UNSLOTH_PASSWORD."
            )
        resp = requests.post(
            f"{self.server_url}/api/auth/login",
            json={"username": self._username(), "password": password},
            timeout=30,
        )
        if resp.status_code >= 400:
            raise UnslothAuthError(
                f"Unsloth Studio login failed ({resp.status_code}): "
                f"{_error_text(resp)}"
            )
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise UnslothAuthError("Unsloth Studio login returned no access_token.")
        self._token = token
        self._token_expiry = _jwt_expiry(token) or (time.time() + 900)
        return token

    def _bearer(self) -> str:
        key = self.api_key()
        if key:
            return key
        if self._token and time.time() < self._token_expiry - 30:
            return self._token
        return self._login()

    def _headers(self, has_body: bool, auth: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if has_body:
            headers["Content-Type"] = "application/json"
        if auth:
            headers["Authorization"] = f"Bearer {self._bearer()}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: int = 60,
        auth: bool = True,
        retry_auth: bool = True,
    ) -> requests.Response:
        payload = (
            json.dumps(json_body).encode("utf-8") if json_body is not None else None
        )
        resp = requests.request(
            method,
            f"{self.server_url}{path}",
            data=payload,
            params=params,
            headers=self._headers(payload is not None, auth),
            timeout=timeout,
        )
        if resp.status_code in (401, 403):
            text = _error_text(resp)
            if "password change required" in text.lower():
                raise UnslothAuthError(_PASSWORD_CHANGE_HINT)
            # A cached JWT may have expired; a configured password can refresh it.
            if (
                retry_auth
                and not self.api_key()
                and os.environ.get("UNSLOTH_PASSWORD")
            ):
                self._token = None
                return self._request(
                    method,
                    path,
                    json_body=json_body,
                    params=params,
                    timeout=timeout,
                    auth=auth,
                    retry_auth=False,
                )
            raise UnslothAuthError(
                f"Unsloth Studio rejected the credentials ({resp.status_code}): {text}"
            )
        if resp.status_code >= 400:
            raise UnslothError(
                f"Unsloth Studio {method} {path} failed ({resp.status_code}): "
                f"{_error_text(resp)}"
            )
        return resp

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = self._request(method, path, **kwargs)
        try:
            return resp.json()
        except ValueError:
            raise UnslothError(
                f"Unsloth Studio returned non-JSON for {method} {path}: "
                f"{resp.text[:300]}"
            )

    # ------------------------------------------------------------------
    # Health + discovery
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """True when the Studio server answers on its public auth-status route."""
        try:
            resp = requests.get(f"{self.server_url}/api/auth/status", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    def auth_status(self) -> dict[str, Any]:
        """Public auth state (initialized, login mode, password-change gate)."""
        return self._json("GET", "/api/auth/status", auth=False, timeout=10)

    def models(self, modality: str | None = None) -> list[dict[str, Any]]:
        """List catalog models, optionally filtered by output modality.

        Studio's ``output_modalities`` query only filters some catalogs; entries
        also carry a ``task`` (``text-to-image`` / ``text-to-video``), so the
        modality filter is applied locally as well. Entries with neither field
        are kept rather than hidden.
        """
        params = {"output_modalities": modality} if modality else None
        data = self._json("GET", "/v1/models", params=params, timeout=30)
        items = data.get("data") if isinstance(data, dict) else data
        items = items if isinstance(items, list) else []
        if modality:
            items = [item for item in items if _matches_modality(item, modality)]
        return items

    def image_status(self) -> dict[str, Any]:
        """Loaded diffusion model, family, and generation defaults."""
        return self._json("GET", "/api/inference/images/status", timeout=30)

    def video_status(self) -> dict[str, Any]:
        """Loaded video model, capability flags, and per-family defaults."""
        return self._json("GET", "/api/inference/video/status", timeout=30)

    @staticmethod
    def loaded_model(status: dict[str, Any]) -> dict[str, Any]:
        """Pick the stable provenance fields out of a status payload."""
        return {
            "loaded": bool(status.get("loaded")),
            "repo_id": status.get("repo_id"),
            "display_repo_id": status.get("display_repo_id"),
            "family": status.get("family"),
            "engine": status.get("engine"),
            "dtype": status.get("dtype"),
        }

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------

    def generate_image(
        self,
        *,
        prompt: str,
        dest: Path,
        width: int | None = None,
        height: int | None = None,
        steps: int | None = None,
        guidance: float | None = None,
        seed: int | None = None,
        negative_prompt: str | None = None,
        n: int = 1,
        model: str | None = None,
        timeout: int = 1800,
    ) -> list[Path]:
        """Generate image(s) and write them to ``dest`` (``dest`` stem + index)."""
        body: dict[str, Any] = {
            "prompt": prompt,
            "n": max(1, int(n)),
            # b64 avoids a second authenticated round-trip to a signed URL.
            "response_format": "b64_json",
        }
        if width and height:
            body["width"] = int(width)
            body["height"] = int(height)
        if steps is not None:
            body["steps"] = int(steps)
        if guidance is not None:
            body["guidance"] = float(guidance)
        if seed is not None:
            body["seed"] = int(seed)
        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        if model:
            body["model"] = model

        started = time.perf_counter()
        data = self._json(
            "POST", "/v1/images/generations", json_body=body, timeout=timeout
        )
        items = data.get("data") or []
        if not items:
            raise UnslothError(
                f"Unsloth Studio returned no images: {json.dumps(data)[:400]}"
            )

        dest.parent.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for index, item in enumerate(items):
            if item.get("b64_json"):
                raw = base64.b64decode(item["b64_json"])
            elif item.get("url"):
                raw = self.download(item["url"])
            else:
                raise UnslothError(
                    f"Unsloth image response item {index} has neither b64_json nor url."
                )
            target = dest if len(items) == 1 else _indexed_path(dest, index)
            target.write_bytes(raw)
            paths.append(target)
        self.last_timing = {"request_s": round(time.perf_counter() - started, 3)}
        return paths

    # ------------------------------------------------------------------
    # Video
    # ------------------------------------------------------------------

    def generate_video(
        self,
        payload: dict[str, Any],
        dest: Path,
        *,
        timeout: int = 3600,
        poll_interval: float = 2.0,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[Path, dict[str, Any]]:
        """Submit a video job, wait for completion, download the MP4.

        Returns ``(path, gallery_record)``. The Studio exposes a single
        generation slot, so video jobs are inherently serialized.
        """
        self.last_timing = None
        started = time.perf_counter()
        payload = dict(payload)
        try:
            self._json(
                "POST", "/api/inference/video/generate", json_body=payload, timeout=120
            )
        except UnslothError as exc:
            # Before a model is resident the client cannot know the family's
            # temporal lattice, so a duration-derived frame count can land off
            # it. Studio's 422 names the nearest valid count -- use it once
            # instead of failing a request the server already knows how to fix.
            suggestion = _nearest_frame_suggestion(str(exc))
            if suggestion and payload.get("num_frames") != suggestion:
                payload["num_frames"] = suggestion
                self._json(
                    "POST",
                    "/api/inference/video/generate",
                    json_body=payload,
                    timeout=120,
                )
            else:
                raise
        submitted = time.perf_counter()

        deadline = time.time() + timeout
        while time.time() < deadline:
            progress = self._json(
                "GET", "/api/inference/video/generate-progress", timeout=30
            )
            if on_progress:
                on_progress(progress)
            phase = progress.get("phase")
            if phase == "completed":
                record = progress.get("video") or {}
                finished = time.perf_counter()
                raw = self._download_video(record)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(raw)
                if not dest.stat().st_size:
                    raise UnslothError(
                        f"Unsloth Studio returned an empty video for {record.get('id')!r}."
                    )
                downloaded = time.perf_counter()
                self.last_timing = {
                    "submit_s": round(submitted - started, 3),
                    "client_wait_s": round(finished - submitted, 3),
                    "download_s": round(downloaded - finished, 3),
                    "total_s": round(downloaded - started, 3),
                    "clip_duration_s": record.get("duration_s"),
                    "video_id": record.get("id"),
                }
                return dest, record
            if phase == "failed":
                raise UnslothError(
                    f"Unsloth video generation failed: "
                    f"{progress.get('error') or 'unknown error'}"
                )
            time.sleep(poll_interval)

        raise UnslothError(
            f"Unsloth video generation did not finish within {timeout}s. The job may "
            f"still be running; check GET {{server_url}}/api/inference/video/generate-progress "
            f"or the Studio video gallery instead of resubmitting."
        )

    def _download_video(self, record: dict[str, Any]) -> bytes:
        video_id = record.get("id")
        if video_id:
            resp = self._request(
                "GET", f"/api/inference/video/gallery/{video_id}/file", timeout=600
            )
            return resp.content
        url = record.get("url")
        if url:
            return self.download(url)
        raise UnslothError(
            "Completed video has neither an id nor a url; cannot download it."
        )

    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------

    def download(self, url: str, *, timeout: int = 600) -> bytes:
        """Fetch an artifact by absolute or server-relative URL (bearer auth)."""
        absolute = url.startswith(("http://", "https://"))
        target = url if absolute else f"{self.server_url}{url}"
        resp = requests.get(
            target,
            headers={"Authorization": f"Bearer {self._bearer()}"},
            timeout=timeout,
        )
        if resp.status_code >= 400:
            raise UnslothError(
                f"Unsloth artifact download failed ({resp.status_code}): {url}"
            )
        return resp.content

    @staticmethod
    def random_seed() -> int:
        """A seed in the range Studio accepts (0 .. 2**31-1)."""
        return random.randint(0, 2**31 - 1)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _error_text(resp: requests.Response) -> str:
    """Pull the most useful message out of a Studio error body."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:500]
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
        error = data.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if detail is not None:
            return json.dumps(detail)[:500]
    return json.dumps(data)[:500]


def _nearest_frame_suggestion(message: str) -> int | None:
    """Extract Studio's "nearest supported count is N" hint from a 422 body."""
    match = re.search(
        r"nearest supported (?:frame )?count is\s+(\d+)", message, re.IGNORECASE
    )
    return int(match.group(1)) if match else None


def _matches_modality(item: dict[str, Any], modality: str) -> bool:
    """Best-effort modality match against a Studio catalog entry.

    Media entries always carry a ``task`` (``text-to-image`` /
    ``text-to-video``); text LLMs in the same catalog do not. An entry with no
    modality signal is therefore treated as non-media rather than offered as an
    image/video model.
    """
    wanted = modality.lower()
    task = str(item.get("task") or "").lower()
    if task:
        return wanted in task
    raw = item.get("output_modalities") or item.get("modalities") or []
    if isinstance(raw, str):
        raw = [raw]
    values = [str(value).lower() for value in raw]
    if values:
        return any(wanted in value for value in values)
    return False


def _jwt_expiry(token: str) -> float | None:
    """Read ``exp`` from a JWT without verifying it (refresh still needs auth)."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
        return float(exp) if exp else None
    except Exception:
        return None


def _indexed_path(dest: Path, index: int) -> Path:
    """``name.png`` -> ``name_001.png`` (keeps the caller's suffix)."""
    return dest.with_name(f"{dest.stem}_{index:03d}{dest.suffix}")
