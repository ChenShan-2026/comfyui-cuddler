"""Where the plugin keeps private state inside ComfyUI.

`user/__cuddler` is a ComfyUI "system user" directory: ComfyUI's HTTP routes
(including /userdata) refuse to serve anything under a "__" prefix.
"""
import os
from pathlib import Path


def private_directory():
    import folder_paths  # ComfyUI's module; imported on use so tests can stand in for it

    getter = getattr(folder_paths, "get_system_user_directory", None)
    if getter is not None:
        return Path(getter("cuddler"))
    return Path(os.path.join(folder_paths.get_user_directory(), "__cuddler"))


def comfy_settings_file():
    import folder_paths

    return Path(folder_paths.get_user_directory()) / "default" / "comfy.settings.json"
