"""Shared error boundary for installer adapters."""

class AdapterError(Exception):
    """Fixed, non-sensitive adapter diagnostic; subprocess output stays private."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code
