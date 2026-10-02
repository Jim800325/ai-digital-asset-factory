from app.providers.animation.shrimp.adapters.base import (
    AssetExecutionAdapter,
    VoiceExecutionAdapter,
)
from app.providers.animation.shrimp.adapters.comfyui import ComfyUIAssetAdapter
from app.providers.animation.shrimp.adapters.gptsovits import GPTSoVITSAdapter

__all__ = [
    "AssetExecutionAdapter",
    "VoiceExecutionAdapter",
    "ComfyUIAssetAdapter",
    "GPTSoVITSAdapter",
]
