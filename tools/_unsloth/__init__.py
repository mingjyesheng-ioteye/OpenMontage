"""Unsloth Studio integration — shared REST client for local image/video inference."""

from tools._unsloth.client import (
    DEFAULT_SERVER_URL,
    UnslothAuthError,
    UnslothClient,
    UnslothError,
    snap_dimension,
    snap_frames,
    to_data_url,
)

__all__ = [
    "DEFAULT_SERVER_URL",
    "UnslothAuthError",
    "UnslothClient",
    "UnslothError",
    "snap_dimension",
    "snap_frames",
    "to_data_url",
]
