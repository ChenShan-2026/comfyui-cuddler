"""Speech and music requests using the same durable runner as video and image."""
from .errors import CuddlerError
from .jobs import JobSpec
from .money import cap_from_widget


def choices(catalog, kind):
    return {f'{model["display_name"]} ({model["id"]})': model
            for model in catalog.of_kind(kind)}


def _seed(seed):
    if type(seed) is not int or not 0 <= seed <= 2_147_483_647:
        raise CuddlerError("invalid_request")


def prepare_speech(catalog, selection, lines, seed, cap):
    model = choices(catalog, "speech").get(selection)
    _seed(seed)
    if model is None or not isinstance(lines, list) or not 1 <= len(lines) <= 50:
        raise CuddlerError("invalid_request")
    voices = {voice["id"] for voice in catalog.voices.get(model["id"], [])}
    for line in lines:
        if (not isinstance(line, dict) or set(line) != {"voice_id", "text"}
                or not isinstance(line["voice_id"], str) or line["voice_id"] not in voices
                or not isinstance(line["text"], str) or not line["text"].strip()):
            raise CuddlerError("invalid_request")
    if (sum(len(line["text"]) for line in lines) > model["max_characters"]
            or len({line["voice_id"] for line in lines}) > 10
            or len(lines) > 1 and not model["dialogue"]):
        raise CuddlerError("invalid_request")
    content = dict(lines[0]) if len(lines) == 1 else {"dialogue": [dict(line) for line in lines]}
    body = {"model": model["id"], "output_format": "mp3", **content}
    # Speech estimation needs the actual text to meter Unicode/UTF-8 correctly.
    # It is sent only to the same Platform origin, never logged or persisted by this module.
    return JobSpec("speech", body, dict(body), cap_from_widget(cap), seed=seed)


def prepare_music(catalog, selection, prompt, seed, cap):
    model = choices(catalog, "music").get(selection)
    _seed(seed)
    if model is None or not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 2000:
        raise CuddlerError("invalid_request")
    body = {"model": model["id"], "prompt": prompt.strip(), "output_format": "mp3"}
    return JobSpec("music", body, dict(body), cap_from_widget(cap), seed=seed)
