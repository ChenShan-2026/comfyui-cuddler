"""Cuddler for ComfyUI: video, images, speech and music with your Cuddler Platform API key."""
from server import PromptServer

from .cuddler_host import routes as _routes
from .cuddler_host.extension import comfy_entrypoint
from .cuddler_host.runtime import get_runtime

WEB_DIRECTORY = "./web"

_routes.install(PromptServer.instance.routes, get_runtime)

__all__ = ["WEB_DIRECTORY", "comfy_entrypoint"]
