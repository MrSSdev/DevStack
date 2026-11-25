"""Shared error boundary for installer adapters."""

class AdapterError(Exception):
    """Fixed, non-sensitive adapter diagnostic; subprocess output stays private."""
