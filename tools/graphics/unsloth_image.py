"""Image generation through a local Unsloth Studio server.

Calls Studio's OpenAI-compatible ``POST /v1/images/generations`` and saves the
returned PNG(s). The Studio owns model loading, quantization, and the diffusion
engine; this tool only needs the server URL and a bearer credential.
"""

from __future__ import annotations

import struct
import time
from pathlib import Path
from typing import Any

from tools._unsloth.client import UnslothAuthError, UnslothClient, UnslothError
from tools._unsloth.metadata import UNSLOTH_SETUP_OFFER
from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)

# Health/status is polled by the registry at preflight and by selectors; a short
# cache keeps a slow Studio catalog scan from dominating discovery.
_STATUS_TTL_SECONDS = 30.0


class UnslothImage(BaseTool):
    name = "unsloth_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "unsloth"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.LOCAL_GPU

    dependencies = []  # checked at runtime via the Studio health route
    setup_offer = UNSLOTH_SETUP_OFFER
    install_instructions = (
        "Run Unsloth Studio locally (default http://127.0.0.1:8888) and set "
        "UNSLOTH_SERVER_URL if it is not the default.\n"
        "Authenticate with UNSLOTH_API_KEY (Settings -> API keys) or "
        "UNSLOTH_USERNAME / UNSLOTH_PASSWORD. Studio rejects API calls until its "
        "seeded bootstrap password is changed once.\n"
        "Running a Studio instance dedicated to images? Set UNSLOTH_IMAGE_SERVER_URL "
        "instead -- it takes priority over UNSLOTH_SERVER_URL for this tool only."
    )
    agent_skills = []

    capabilities = ["text_to_image"]
    supports = {
        "seed": True,
        "custom_size": True,
        "negative_prompt": True,
        "batch": True,
        "custom_workflow": False,
        "offline": True,
    }
    best_for = [
        "local image generation on the GPU Unsloth Studio already manages",
        "Qwen-Image-2.1-Turbo and other GGUF/Diffusers checkpoints without a second copy",
        "zero API cost generation with the Studio's own quantization and offload",
    ]
    not_good_for = [
        "setups without a running Unsloth Studio server",
        "Studio instances still on the seeded bootstrap password",
        "CPU-only machines",
    ]
    fallback = "comfyui_image"
    fallback_tools = ["comfyui_image", "flux_image", "local_diffusion"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Text prompt for image generation"},
            "negative_prompt": {
                "type": "string",
                "description": "What to avoid (only for models that support guidance).",
            },
            "width": {
                "type": "integer",
                "minimum": 256,
                "maximum": 2048,
                "description": "Image width (multiple of 16). Omit to use the model default.",
            },
            "height": {
                "type": "integer",
                "minimum": 256,
                "maximum": 2048,
                "description": "Image height (multiple of 16). Omit to use the model default.",
            },
            "steps": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
                "description": "Denoising steps. Omit to use the model's distilled default.",
            },
            "guidance": {
                "type": "number",
                "minimum": 0,
                "maximum": 20,
                "description": "Classifier-free guidance. Omit to use the model default.",
            },
            "n": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "default": 1,
                "description": "Number of images to generate.",
            },
            "seed": {"type": "integer", "minimum": 0, "description": "Random if omitted"},
            "model": {
                "type": "string",
                "description": (
                    "Optional Studio model id to serve on (loaded first when it is not "
                    "the resident model and media auto-switch is on)."
                ),
            },
            "output_path": {
                "type": "string",
                "description": "Where to save the image (extra images get a numeric suffix).",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "How long to wait for the image request (default 1800s).",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=8000, vram_mb=8000, disk_mb=500, network_required=False,
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
    idempotency_key_fields = ["prompt", "width", "height", "steps", "seed", "n"]
    side_effects = ["writes image file(s) to output_path"]
    user_visible_verification = ["Inspect generated image for quality and prompt adherence"]

    def __init__(self) -> None:
        self._client = UnslothClient(capability="image")
        self._status_cache: tuple[float, ToolStatus, str, dict[str, Any]] | None = None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def _probe(self) -> tuple[ToolStatus, str, dict[str, Any]]:
        """Resolve availability plus an actionable reason and status payload."""
        now = time.monotonic()
        if self._status_cache and now - self._status_cache[0] < _STATUS_TTL_SECONDS:
            return self._status_cache[1], self._status_cache[2], self._status_cache[3]

        result = self._probe_uncached()
        self._status_cache = (now, *result)
        return result

    def _probe_uncached(self) -> tuple[ToolStatus, str, dict[str, Any]]:
        if not self._client.is_available():
            return ToolStatus.UNAVAILABLE, self._client.unavailable_reason(), {}
        if not UnslothClient.has_credentials():
            return (
                ToolStatus.DEGRADED,
                "Unsloth Studio is reachable but no credentials are configured: set "
                "UNSLOTH_API_KEY (or UNSLOTH_USERNAME / UNSLOTH_PASSWORD).",
                {},
            )
        try:
            status = self._client.image_status()
        except UnslothAuthError as exc:
            return ToolStatus.DEGRADED, str(exc), {}
        except UnslothError as exc:
            return ToolStatus.DEGRADED, str(exc), {}
        if status.get("loaded"):
            return ToolStatus.AVAILABLE, "", status
        # Unloaded is still usable when the catalog has an image model and media
        # auto-switch is on: the next request loads it.
        try:
            catalog = self._client.models("image")
        except UnslothError as exc:
            return ToolStatus.DEGRADED, str(exc), status
        if catalog:
            return ToolStatus.AVAILABLE, "", status
        return (
            ToolStatus.DEGRADED,
            "Unsloth Studio has no image model loaded or downloaded. Load or download "
            "one in the Studio UI.",
            status,
        )

    def get_status(self) -> ToolStatus:
        return self._probe()[0]

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        return float(inputs.get("steps", 8)) * 2.0

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        info["setup_offer"] = self.setup_offer
        status, reason, payload = self._probe()
        info["provider_status"] = {
            "server_url": self._client.server_url,
            "status": status.value if hasattr(status, "value") else str(status),
            "reason": reason,
            "loaded_model": UnslothClient.loaded_model(payload) if payload else None,
            "auth": "api_key" if UnslothClient.api_key() else (
                "password" if UnslothClient.has_credentials() else "none"
            ),
        }
        return info

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = str(inputs.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(success=False, error="prompt is required")

        status, reason, status_payload = self._probe()
        if status == ToolStatus.UNAVAILABLE:
            return ToolResult(success=False, error=reason)
        if status != ToolStatus.AVAILABLE:
            if not UnslothClient.has_credentials():
                return ToolResult(success=False, error=reason, data={"setup_required": True})
            if "bootstrap password" in reason.lower() or "rejected the credentials" in reason.lower():
                return ToolResult(success=False, error=reason, data={"auth_required": True})
            return ToolResult(success=False, error=reason)

        width = inputs.get("width")
        height = inputs.get("height")
        if (width is None) != (height is None):
            return ToolResult(
                success=False,
                error="Provide both width and height, or neither (model default).",
            )

        seed = inputs.get("seed")
        if seed is None:
            seed = UnslothClient.random_seed()
        output_path = Path(inputs.get("output_path", f"unsloth_image_{seed}.png"))
        started = time.time()

        try:
            paths = self._client.generate_image(
                prompt=prompt,
                dest=output_path,
                width=width,
                height=height,
                steps=inputs.get("steps"),
                guidance=inputs.get("guidance"),
                seed=int(seed),
                negative_prompt=inputs.get("negative_prompt"),
                n=int(inputs.get("n", 1)),
                model=inputs.get("model"),
                timeout=int(inputs.get("timeout_seconds", 1800)),
            )
        except UnslothAuthError as exc:
            return ToolResult(success=False, error=str(exc), data={"auth_required": True})
        except UnslothError as exc:
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - surface transport errors verbatim
            return ToolResult(
                success=False, error=f"Unsloth image generation failed: {exc}"
            )

        model_info = UnslothClient.loaded_model(status_payload) if status_payload else {}
        try:
            # The status read before generation often predates the auto-load;
            # refresh so provenance names the model that actually rendered.
            model_info = UnslothClient.loaded_model(self._client.image_status())
        except UnslothError:
            pass
        measured = _png_size(paths[0])
        data: dict[str, Any] = {
            "provider": self.provider,
            "model": model_info.get("display_repo_id") or model_info.get("repo_id"),
            "prompt": prompt,
            "width": measured[0] if measured else (width or None),
            "height": measured[1] if measured else (height or None),
            "size_source": "png_header" if measured else "requested",
            "steps": inputs.get("steps"),
            "guidance": inputs.get("guidance"),
            "n": len(paths),
            "output": str(paths[0]),
            "outputs": [str(p) for p in paths],
            "format": "png",
            "workflow_provenance": {
                "source": "unsloth_studio",
                "endpoint": "POST /v1/images/generations",
                "server_url": self._client.server_url,
                "model_status": model_info,
            },
            "timing": dict(self._client.last_timing or {}),
        }
        return ToolResult(
            success=True,
            data=data,
            artifacts=[str(p) for p in paths],
            cost_usd=0.0,
            duration_seconds=round(time.time() - started, 2),
            seed=int(seed),
            model=data["model"],
        )


def _png_size(path: Path) -> tuple[int, int] | None:
    """Read width/height from a PNG IHDR header without decoding the image."""
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
        if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        width, height = struct.unpack(">II", header[16:24])
        return int(width), int(height)
    except OSError:
        return None
