"""The public model catalog (GET /v1/catalog).

The plugin ships a snapshot and refreshes it in the background at start-up;
node definitions use whichever copy was current when ComfyUI started. Anything
this version does not understand is left out instead of breaking the plugin.
"""
import json
import logging
from pathlib import Path
import threading

from .errors import CuddlerError
from .files import write_private

log = logging.getLogger("cuddler")

VIDEO_MODES = {"text", "image", "first-last-frame", "reference", "image-reference",
               "video-reference", "edit", "extend", "motion-control"}
SUPPORTED = {
    "video": {"modes": VIDEO_MODES,
              "fields": {"first_frame", "last_frame", "reference_images", "reference_videos",
                         "reference_audios", "generate_audio", "seed", "camera_fixed",
                         "character_orientation", "fps", "return_last_frame", "task_type"}},
    "image": {"modes": {"text", "reference", "edit"},
              "fields": {"reference_images", "seed", "variant"}},
    "speech": {"modes": None,
               "fields": {"text", "voice_id", "dialogue", "output_format", "max_usd",
                          "metadata", "webhook_url"}},
    "music": {"modes": None,
              "fields": {"prompt", "output_format", "max_usd", "metadata", "webhook_url"}},
}
_MEDIA_LIMITS = ("max_reference_images", "max_reference_videos", "max_reference_audios",
                 "max_references")
CACHE_NAME = "catalog.json"
MAX_CATALOG_BYTES = 4 * 1024 * 1024


class Catalog:
    def __init__(self, models, voices, skipped, dropped, source):
        self.models = models      # id -> entry, with "kind" filled in and unsupported choices removed
        self.voices = voices      # speech model id -> [voice]
        self.skipped = skipped    # [(model id, reason)] left out entirely
        self.dropped = dropped    # ["model id: mode"] usages left out of a kept model
        self.source = source      # "cache", "snapshot" or "empty"

    def of_kind(self, kind):
        return [entry for entry in self.models.values() if entry["kind"] == kind]

    def problems(self):
        """Entries a newer plugin version would be needed for (disabled models are not a problem)."""
        return [f"{model}: {reason}" for model, reason in self.skipped if reason != "disabled"] + [
            f"{item}: unknown_mode" for item in self.dropped]


def _is_count(value):
    return type(value) is int and value >= 0


def _strings(value):
    return isinstance(value, list) and bool(value) and all(isinstance(item, str) for item in value)


def _check(entry):
    """(reason, None, dropped modes) to leave a model out, or (None, cleaned entry, dropped modes).

    Every membership test is on a str: a list or an object in the wrong place is not hashable
    and would raise instead of being left out.
    """
    if entry.get("enabled") is not True:
        return "disabled", None, []
    kind = entry.get("kind", "video")  # the first Seedance entries predate the field
    rule = SUPPORTED.get(kind) if isinstance(kind, str) else None
    if rule is None:
        return "unknown_kind", None, []
    fields = entry.get("request_fields", [])
    if (not isinstance(fields, list)
            or any(not isinstance(field, str) or field not in rule["fields"] for field in fields)):
        return "unknown_field", None, []
    if not isinstance(entry.get("display_name"), str):
        return "invalid_entry", None, []
    cleaned = dict(entry, kind=kind, request_fields=list(fields))
    dropped = []
    if rule["modes"] is not None:
        choices = entry.get("choices")
        if (not isinstance(choices, list) or not _is_count(entry.get("min_duration"))
                or not _is_count(entry.get("max_duration")) or not _strings(entry.get("resolutions"))
                or not _strings(entry.get("aspect_ratios"))
                or not all(_is_count(entry.get(name)) for name in _MEDIA_LIMITS)):
            return "invalid_entry", None, []
        kept = []
        for choice in choices:
            if not isinstance(choice, dict) or not isinstance(choice.get("variant"), str):
                return "invalid_entry", None, []
            mode = choice.get("mode")
            if isinstance(mode, str) and mode in rule["modes"]:
                kept.append(choice)
            else:
                dropped.append(str(mode))
        if not kept:
            return "no_supported_modes", None, dropped
        cleaned["choices"] = kept
    elif kind == "speech":
        if not _is_count(entry.get("max_characters")) or type(entry.get("dialogue")) is not bool:
            return "invalid_entry", None, []
    elif not _is_count(entry.get("max_duration")):
        return "invalid_entry", None, []
    return None, cleaned, dropped


def parse(document, source="snapshot"):
    if (not isinstance(document, dict) or document.get("object") != "catalog"
            or not isinstance(document.get("models"), list)):
        raise CuddlerError("catalog_invalid")
    models, skipped, dropped = {}, [], []
    for entry in document["models"]:
        model_id = entry.get("id") if isinstance(entry, dict) else None
        if not isinstance(model_id, str) or not model_id:
            skipped.append(("?", "invalid_entry"))
            continue
        try:
            reason, cleaned, modes = _check(entry)
        except Exception:  # a shape this version never saw: lose that model, never the whole catalog
            reason, cleaned, modes = "invalid_entry", None, []
        dropped.extend(f"{model_id}: {mode}" for mode in modes)
        if reason:
            skipped.append((model_id, reason))
        else:
            models[model_id] = cleaned
    voices = {}
    raw = document.get("voices")
    if isinstance(raw, dict):
        for model_id, items in raw.items():
            if isinstance(items, list):
                voices[model_id] = [voice for voice in items if isinstance(voice, dict)
                                    and isinstance(voice.get("id"), str) and isinstance(voice.get("name"), str)]
    return Catalog(models, voices, skipped, dropped, source)


def _read_limited(path):
    """The file's bytes. Never holds more than the limit (plus one byte) in memory."""
    with open(path, "rb") as stream:
        data = stream.read(MAX_CATALOG_BYTES + 1)
    if len(data) > MAX_CATALOG_BYTES:
        raise CuddlerError("catalog_invalid")
    return data


class CatalogStore:
    def __init__(self, snapshot_path, cache_dir, fetch):
        self._snapshot_path = Path(snapshot_path)
        self._cache_path = Path(cache_dir) / CACHE_NAME
        self._fetch = fetch
        self._lock = threading.Lock()
        self._current = None

    def current(self):
        """The catalog this process uses, fixed at first use (node definitions depend on it)."""
        with self._lock:
            if self._current is None:
                self._current = self._load()
            return self._current

    def _load(self):
        """Never raises: a copy that cannot be read is skipped, and no copy at all is an empty catalog."""
        for path, source in ((self._cache_path, "cache"), (self._snapshot_path, "snapshot")):
            try:
                # utf-8-sig tolerates a byte-order mark; ValueError covers bad UTF-8 and bad JSON,
                # RecursionError a document nested deeper than the parser allows.
                catalog = parse(json.loads(_read_limited(path).decode("utf-8-sig")), source)
            except FileNotFoundError:
                continue
            except (OSError, ValueError, RecursionError, CuddlerError):
                log.warning("cuddler: ignoring an unreadable %s model catalog", source)
                continue
            if catalog.models:
                return catalog
        return Catalog({}, {}, [], [], "empty")

    def refresh(self):
        """Fetch the live catalog and keep it for the next start. True when it changed."""
        document = self._fetch()
        catalog = parse(document, "cache")
        if not catalog.models:
            raise CuddlerError("catalog_invalid")
        for problem in catalog.problems():
            log.warning("cuddler: left out of the model list until the plugin is updated: %s", problem)
        try:
            # ASCII-only and compact: any string (even a lone surrogate) round-trips, and the copy
            # stays within what `_load` is willing to read.
            data = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")
        except (TypeError, ValueError, RecursionError):
            raise CuddlerError("catalog_invalid") from None
        if len(data) > MAX_CATALOG_BYTES:
            raise CuddlerError("catalog_invalid")
        try:
            if _read_limited(self._cache_path) == data:
                return False
        except (OSError, CuddlerError):
            pass
        write_private(self._cache_path, data)
        return True
