"""Run fingerprints: the same inputs always give the same digest.

Only digests are stored, so the database never holds the prompt or the media a
fingerprint was made from. The digest is not salted, though: whoever has it can
still test a guess at a short prompt.
"""
import hashlib
import json

from .errors import CuddlerError

VERSION = 1
MAX_DEPTH = 32  # far deeper than any API body; fixed, so acceptance is the same on every Python


def _normalized(value, depth=0):
    """A copy of `value` in which numbers that JSON writes alike are the same number.

    JSON has one number type: the API reads 4.0 as 4 and -0.0 as 0, so each pair is one request.
    """
    if isinstance(value, float):
        return int(value) if value.is_integer() else value  # nan and infinity stay for json to refuse
    if isinstance(value, (dict, list, tuple)):
        if depth >= MAX_DEPTH:
            raise CuddlerError("invalid_request")
        if isinstance(value, dict):
            return {key: _normalized(item, depth + 1) for key, item in value.items()}
        return [_normalized(item, depth + 1) for item in value]
    return value


def canonical(value):
    """The bytes that get hashed. Raises CuddlerError("invalid_request") if JSON cannot hold `value`."""
    try:
        return json.dumps(_normalized(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise CuddlerError("invalid_request") from None


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def run_fingerprint(kind, body, media_digests, seed):
    """`media_digests` maps an API field to one sha256, or a list of them for list fields."""
    return digest_bytes(canonical({"v": VERSION, "kind": kind, "body": body,
                                   "media": media_digests, "seed": seed}))
