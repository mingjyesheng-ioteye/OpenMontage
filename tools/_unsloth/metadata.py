"""Shared metadata for the Unsloth Studio provider tools."""

from __future__ import annotations

import os
from typing import Any

UNSLOTH_SETUP_OFFER: dict[str, Any] = {
    "kind": "local_server",
    "fix_complexity": (
        "1-minute env-var if Unsloth Studio is already running; otherwise install "
        "Unsloth Studio and load an image/video model once"
    ),
    "env_var": "UNSLOTH_SERVER_URL",
    "default_url": "http://127.0.0.1:8888",
    "health_check": "GET /api/auth/status",
    "auth_env_vars": ["UNSLOTH_API_KEY", "UNSLOTH_USERNAME", "UNSLOTH_PASSWORD"],
    "auth_note": (
        "Studio blocks the API until the seeded bootstrap password is changed once. "
        "After that, create an API key (Settings -> API keys) and set UNSLOTH_API_KEY, "
        "or set UNSLOTH_USERNAME / UNSLOTH_PASSWORD."
    ),
    "what_it_unlocks": [
        "local image generation through Unsloth Studio (Qwen-Image and friends)",
        "local video generation with native audio (MiniMax H3 and friends)",
        "the same GGUF checkpoints ComfyUI uses, without a second model copy",
    ],
    "per_capability_env_var_overrides": {
        "unsloth_image": "UNSLOTH_IMAGE_SERVER_URL",
        "unsloth_video": "UNSLOTH_VIDEO_SERVER_URL",
    },
}


def capability_server_url(capability: str, default_url: str = "http://127.0.0.1:8888") -> str:
    """Resolve the Studio URL for one capability (override wins over shared)."""
    override = os.environ.get(f"UNSLOTH_{capability.upper()}_SERVER_URL")
    return (override or os.environ.get("UNSLOTH_SERVER_URL") or default_url).rstrip("/")
