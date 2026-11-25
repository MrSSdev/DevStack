"""Shared error boundary for installer adapters."""

class AdapterError(Exception):
    """Fixed, non-sensitive adapter diagnostic; subprocess output stays private."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code

def resolve_installer_options(kit, fields, selected):
    """Resolve typed option IDs in declared dependency order.

    Root and unprivileged worker independently validate the same declarative
    contract. An empty string in a dependency means that field is inapplicable.
    Never pass through unknown or currently hidden selections.
    """
    if not isinstance(selected, dict) or len(selected) > 12:
        raise ValueError("Invalid installer options.")
    active = {}
    for field in fields:
        if field["kits"] and kit not in field["kits"]:
            continue
        if any(active.get(key, "") not in allowed for key, allowed in field["when"].items()):
            continue
        key = field["id"]
        value = selected.get(key, field["default"])
        if not isinstance(value, str) or value not in {choice["id"] for choice in field["choices"]}:
            raise ValueError("Unavailable installer option.")
        active[key] = value
    if set(selected) - active.keys():
        raise ValueError("Unknown or inapplicable installer option.")
    return active
