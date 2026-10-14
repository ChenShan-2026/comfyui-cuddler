"""Failures the plugin can explain to a person.

An error carries a stable code plus safe details (amounts, a request id), never
a key, a prompt or a provider's raw response.
"""


class CuddlerError(Exception):
    def __init__(self, code, *, status=None, request_id=None, retry_after=None, detail=None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.request_id = request_id
        self.retry_after = retry_after
        self.detail = dict(detail or {})
