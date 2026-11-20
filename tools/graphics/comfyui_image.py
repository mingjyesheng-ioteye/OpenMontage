"""ComfyUI image generation via a local or remote ComfyUI server.

Default workflow: FLUX 2 Dev (NVFP4) with Mistral text encoder.
Supports custom workflows via the ``workflow_json`` input.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

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
from tools._comfyui.client import ComfyUIClient, ComfyUIError
from tools._comfyui.metadata import (
    BUNDLED_MODEL_STACKS,
    COMFYUI_SETUP_OFFER,
    missing_models_payload,
    model_stack,
    workflow_hash,
)

_WORKFLOWS = Path(__file__).resolve().parent.parent / "_comfyui" / "workflows"

# Models required by the bundled flux2-txt2img workflow
_REQUIRED_MODELS = [
    "flux2-dev-nvfp4.safetensors",
    "mistral_3_small_flux2_fp4_mixed.safetensors",
    "flux2-vae.safetensors",
]

# Bundled model families. ``flux2`` keeps the original NVFP4 stack; the Qwen
# family is the unsloth GGUF Qwen-Image-2.1-Turbo denoiser (8-step distilled).
_MODEL_FAMILIES: dict[str, dict[str, Any]] = {
    "flux2": {
        "workflow": "flux2-txt2img.json",
        "workflow_key": "flux2-txt2img",
        "output_node": "13",
        "required_models": _REQUIRED_MODELS,
        "model_name": "flux2-dev-nvfp4",
        "steps": 20,
    },
    "qwen_image_2_1_turbo_gguf": {
        "workflow": "qwen-image-2.1-turbo-gguf-t2i.json",
        "workflow_key": "qwen-image-2.1-turbo-gguf",
        "output_node": "13",
        "required_models": [
            "qwen-image-2.1-turbo-Q8_0.gguf",
            "qwen3vl_8b_int8_convrot.safetensors",
            "qwen_image_2.1_vae_bf16.safetensors",
        ],
        "model_name": "qwen-image-2.1-turbo-q8_0-gguf",
        "steps": 8,
    },
}


def _snap32(value: int) -> int:
    """Round *value* down to a multiple of 32 (Qwen/H3 latent grid)."""
    value = max(32, int(value))
    return max(32, (value // 32) * 32)


class ComfyUIImage(BaseTool):
    name = "comfyui_image"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "image_generation"
    provider = "comfyui"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.SEEDED
    runtime = ToolRuntime.LOCAL_GPU

    dependencies = []  # checked at runtime via server health
    setup_offer = COMFYUI_SETUP_OFFER
    install_instructions = (
        "Start a ComfyUI server and set COMFYUI_SERVER_URL "
        "(default http://localhost:8188).\n"
        "See https://github.com/comfyanonymous/ComfyUI for setup.\n"
        "GGUF families (qwen_image_2_1_turbo_gguf) also need the ComfyUI-GGUF "
        "custom node; see docs/AMD_WINDOWS_COMFYUI.md.\n"
        "Running a separate ComfyUI instance for images? Set COMFYUI_IMAGE_SERVER_URL "
        "instead -- it takes priority over COMFYUI_SERVER_URL for this tool only."
    )
    agent_skills = ["comfyui", "flux-best-practices"]

    capabilities = ["text_to_image"]
    supports = {
        "seed": True,
        "custom_size": True,
        "custom_workflow": True,
        "custom_output_node": True,
        "offline": True,
    }
    best_for = [
        "local GPU generation without API costs",
        "Blackwell / DGX Spark hardware where diffusers is unsupported",
        "full control over sampling via custom ComfyUI workflows",
        "Qwen-Image-2.1-Turbo GGUF (model_family: qwen_image_2_1_turbo_gguf) for 8-step local text-to-image",
    ]
    not_good_for = [
        "setups without a running ComfyUI server",
        "CPU-only machines",
    ]
    fallback = "flux_image"
    fallback_tools = ["flux_image", "local_diffusion", "openai_image"]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "Text prompt for image generation"},
            "model_family": {
                "type": "string",
                "enum": ["flux2", "qwen_image_2_1_turbo_gguf"],
                "default": "flux2",
                "description": (
                    "Bundled local model family. flux2 = FLUX.2 dev NVFP4; "
                    "qwen_image_2_1_turbo_gguf = unsloth Qwen-Image-2.1-Turbo Q8_0 GGUF "
                    "(8-step distilled, needs ComfyUI-GGUF)."
                ),
            },
            "width": {"type": "integer", "default": 1024},
            "height": {"type": "integer", "default": 1024},
            "steps": {"type": "integer", "default": 20},
            "guidance": {"type": "number", "default": 3.5},
            "seed": {"type": "integer", "description": "Random if omitted"},
            "output_path": {"type": "string", "description": "Where to save the image"},
            "workflow_json": {
                "type": "string",
                "description": "Optional full ComfyUI workflow JSON. Requires output_node.",
            },
            "workflow_path": {
                "type": "string",
                "description": "Optional path to a ComfyUI workflow JSON file. Requires output_node.",
            },
            "output_node": {
                "type": "string",
                "description": "ComfyUI output node ID for custom workflow_json/workflow_path.",
            },
            "workflow_name": {
                "type": "string",
                "description": "Optional human-readable provenance label for a custom workflow.",
            },
            "workflow_model": {
                "type": "string",
                "description": "Optional model/provenance label for a custom workflow.",
            },
            "workflow_model_stack": {
                "type": "array",
                "description": (
                    "Optional provenance metadata for custom workflow dependencies. "
                    "Items should include name, role, quantization, and LoRA strengths when known."
                ),
                "items": {"type": "object"},
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=2, ram_mb=8000, vram_mb=8000, disk_mb=500, network_required=False,
    )
    retry_policy = RetryPolicy(max_retries=1, retryable_errors=["timeout"])
    idempotency_key_fields = ["prompt", "width", "height", "steps", "seed"]
    side_effects = ["writes image file to output_path"]
    user_visible_verification = ["Inspect generated image for quality and prompt adherence"]

    def __init__(self) -> None:
        self._client = ComfyUIClient(capability="image")

    def get_status(self) -> ToolStatus:
        if not self._client.is_available():
            return ToolStatus.UNAVAILABLE
        for spec in _MODEL_FAMILIES.values():
            _, missing = self._client.check_models(spec["required_models"])
            if not missing:
                return ToolStatus.AVAILABLE
        return ToolStatus.DEGRADED

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def estimate_runtime(self, inputs: dict[str, Any]) -> float:
        family = str(inputs.get("model_family", "flux2"))
        steps = _MODEL_FAMILIES.get(family, _MODEL_FAMILIES["flux2"])["steps"]
        return float(steps) * 1.5

    def get_info(self) -> dict[str, Any]:
        info = super().get_info()
        info["setup_offer"] = self.setup_offer
        info["bundled_model_stack"] = BUNDLED_MODEL_STACKS["flux2-txt2img"]
        info["bundled_model_stacks"] = {
            "flux2": BUNDLED_MODEL_STACKS["flux2-txt2img"],
            "qwen_image_2_1_turbo_gguf": BUNDLED_MODEL_STACKS[
                "qwen-image-2.1-turbo-gguf"
            ],
        }
        return info

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        custom_workflow = bool(inputs.get("workflow_json") or inputs.get("workflow_path"))
        if custom_workflow and not inputs.get("output_node"):
            return ToolResult(
                success=False,
                error=(
                    "Custom ComfyUI workflows require output_node so OpenMontage "
                    "knows which ComfyUI node to download artifacts from."
                ),
            )

        family = str(inputs.get("model_family", "flux2"))
        spec = _MODEL_FAMILIES.get(family)
        if spec is None and not custom_workflow:
            return ToolResult(
                success=False,
                error=(
                    f"Unknown ComfyUI image model_family {family!r}. "
                    f"Choose one of {sorted(_MODEL_FAMILIES)} or supply a custom workflow."
                ),
            )

        if not self._client.is_available():
            return ToolResult(
                success=False,
                error=self._client.unavailable_reason(),
            )

        if not custom_workflow:
            _, missing = self._client.check_models(spec["required_models"])
            if missing:
                installed = [
                    name
                    for name, candidate in _MODEL_FAMILIES.items()
                    if not self._client.check_models(candidate["required_models"])[1]
                ]
                hint = (
                    f" Families fully installed on this server: {installed}. "
                    f"Pass model_family=<name> to use one."
                    if installed
                    else ""
                )
                return ToolResult(
                    success=False,
                    data=missing_models_payload(
                        missing,
                        workflow_key=spec["workflow_key"],
                        workflow_name=spec["workflow"],
                    ),
                    error=(
                        f"ComfyUI server is running but missing required models for "
                        f"{family}: {', '.join(missing)}.{hint}\n"
                        f"See data.missing_models for destination hints and download URLs."
                    ),
                )

        start = time.time()
        seed = inputs.get("seed") or ComfyUIClient.random_seed()
        width = inputs.get("width", 1024)
        height = inputs.get("height", 1024)
        steps = int(inputs.get("steps", spec["steps"] if spec else 20))
        guidance = float(inputs.get("guidance", 1.0 if family == "qwen_image_2_1_turbo_gguf" else 3.5))
        output_path = Path(inputs.get("output_path", f"comfyui_image_{seed}.png"))

        try:
            if custom_workflow:
                workflow = self._load_custom_workflow(inputs)
                output_node = str(inputs["output_node"])
            elif family == "qwen_image_2_1_turbo_gguf":
                # Qwen-Image-2.1 latents sit on a 32 px grid.
                width = _snap32(width)
                height = _snap32(height)
                workflow = ComfyUIClient.load_workflow(_WORKFLOWS / spec["workflow"])
                workflow = ComfyUIClient.patch_workflow(workflow, {
                    "4": {
                        "prompt": inputs["prompt"],
                        "resolution": _snap32(min(width, height)),
                    },
                    "5": {"width": width, "height": height, "batch_size": 1},
                    "7": {"cfg": guidance},
                    "10": {"noise_seed": seed},
                    "13": {"filename_prefix": output_path.stem},
                })
                # Qwen-Image-2.1-Turbo ships a fixed 8-step sigma schedule
                # (ManualSigmas). `steps` is reported from the schedule, not
                # patched, because overriding it would desynchronise the
                # distilled sampler.
                steps = len(
                    workflow["9"]["inputs"]["sigmas"].rstrip(", ").split(",")
                ) - 1
                output_node = spec["output_node"]
            else:
                workflow = ComfyUIClient.load_workflow(_WORKFLOWS / spec["workflow"])
                workflow = ComfyUIClient.patch_workflow(workflow, {
                    "4": {"text": inputs["prompt"]},
                    "5": {"guidance": guidance},
                    "6": {"width": width, "height": height, "batch_size": 1},
                    "7": {"noise_seed": seed},
                    "10": {"steps": steps, "width": width, "height": height},
                    "13": {"filename_prefix": output_path.stem},
                })
                output_node = spec["output_node"]

            provenance = self._workflow_provenance(
                inputs, custom_workflow, output_node, workflow
            )
            paths = self._client.generate(
                workflow, output_node=output_node, dest=output_path, timeout=600,
            )

        except ComfyUIError as exc:
            return ToolResult(success=False, error=str(exc))
        except Exception as exc:
            return ToolResult(success=False, error=f"ComfyUI image generation failed: {exc}")

        model_name = self._model_name(inputs, custom_workflow)
        return ToolResult(
            success=True,
            data={
                "provider": "comfyui",
                "model": model_name,
                "prompt": inputs["prompt"],
                "width": width,
                "height": height,
                "steps": steps,
                "guidance": guidance,
                "output": str(paths[0]),
                "format": "png",
                "workflow_provenance": provenance,
            },
            artifacts=[str(p) for p in paths],
            cost_usd=0.0,
            duration_seconds=round(time.time() - start, 2),
            seed=seed,
            model=model_name,
        )

    @staticmethod
    def _load_custom_workflow(inputs: dict[str, Any]) -> dict:
        if inputs.get("workflow_json"):
            return json.loads(inputs["workflow_json"])
        return ComfyUIClient.load_workflow(Path(inputs["workflow_path"]))

    @staticmethod
    def _model_name(inputs: dict[str, Any], custom_workflow: bool) -> str:
        if not custom_workflow:
            family = str(inputs.get("model_family", "flux2"))
            return _MODEL_FAMILIES.get(family, _MODEL_FAMILIES["flux2"])["model_name"]
        return (
            inputs.get("workflow_model")
            or inputs.get("model")
            or inputs.get("workflow_name")
            or "custom-comfyui-workflow"
        )

    @staticmethod
    def _workflow_provenance(
        inputs: dict[str, Any],
        custom_workflow: bool,
        output_node: str,
        workflow: dict[str, Any],
    ) -> dict[str, Any]:
        if not custom_workflow:
            family = str(inputs.get("model_family", "flux2"))
            spec = _MODEL_FAMILIES.get(family, _MODEL_FAMILIES["flux2"])
            return {
                "source": "bundled",
                "workflow": spec["workflow"],
                "model_family": family,
                "workflow_hash_sha256": workflow_hash(workflow),
                "model_stack": model_stack(spec["workflow_key"], inputs),
                "output_node": output_node,
            }
        return {
            "source": "user_supplied",
            "workflow_name": inputs.get("workflow_name"),
            "workflow_path": inputs.get("workflow_path"),
            "model": inputs.get("workflow_model") or inputs.get("model"),
            "workflow_hash_sha256": workflow_hash(workflow),
            "model_stack": model_stack(None, inputs),
            "model_stack_source": (
                "caller_supplied"
                if inputs.get("workflow_model_stack")
                else "unknown_custom_workflow"
            ),
            "output_node": output_node,
        }
