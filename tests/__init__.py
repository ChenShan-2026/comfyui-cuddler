"""Make the repository importable as the package `cuddler_plugin`, the way ComfyUI loads it.

ComfyUI imports this folder as a package, so the code uses relative imports.
Registering the folder under a package name, without running the top-level
__init__.py (which needs ComfyUI), lets tests import every module the same way:
`from cuddler_plugin.cuddler.money import display`.
"""
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]

if "cuddler_plugin" not in sys.modules:
    package = types.ModuleType("cuddler_plugin")
    package.__path__ = [str(ROOT)]
    sys.modules["cuddler_plugin"] = package
