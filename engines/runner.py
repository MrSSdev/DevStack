#!/usr/bin/env python3
"""Entry point for an allowlisted unprivileged framework adapter."""
from __future__ import annotations

import json
import os
import pathlib
import pwd
import sys
import tempfile

# The installed runner and its modules are root-owned. Isolated Python is
# invoked by the coordinator; never import modules from the working project.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from laravel.adapter import AdapterError
from registry import ADAPTERS


def main() -> int:
    if os.geteuid() == 0 or pwd.getpwuid(os.geteuid()).pw_name == "devstackweb":
        print(json.dumps({"ok": False, "error": "Adapter requires the developer account."}))
        return 1
    home = pathlib.Path(pwd.getpwuid(os.geteuid()).pw_dir)
    if pathlib.Path(os.environ.get("HOME", "")) != home:
        print(json.dumps({"ok": False, "error": "Invalid developer home."}))
        return 1
    if len(sys.argv) < 3 or sys.argv[1] not in ADAPTERS:
        print(json.dumps({"ok": False, "error": "Unsupported engine."}))
        return 1
    engine, action, args = sys.argv[1], sys.argv[2], sys.argv[3:]
    log_path = None
    try:
        directory = home / ".cache" / "devstack" / "engines"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=directory, prefix=engine + "-",
                                         suffix=".log", delete=False) as log:
            log_path = pathlib.Path(log.name)
            adapter = ADAPTERS[engine](home, log)
            if action == "status" and not args:
                result = adapter.status()
            elif action == "check" and not args:
                result = adapter.check()
            elif action in ("install-tool", "update-tool") and not args:
                result = adapter.maintain(action)
            elif action == "scaffold" and len(args) == 2:
                result = adapter.scaffold(*args)
            elif action == "configure" and len(args) == 1:
                raw = sys.stdin.read(8193)
                if len(raw) > 8192:
                    raise AdapterError("Configuration exceeds the allowed size.")
                result = adapter.configure(args[0], json.loads(raw))
            elif action == "migrate" and len(args) == 1:
                result = adapter.migrate(args[0])
            else:
                raise AdapterError("Unsupported adapter operation.")
        # Successful local status reads do not accumulate diagnostic logs.
        if action == "status":
            log_path.unlink(missing_ok=True)
        print(json.dumps({"ok": True, "result": result}))
        return 0
    except AdapterError as exc:
        error = str(exc)
    except (OSError, ValueError, UnicodeError):
        error = "Adapter operation failed."
    # The log belongs only to the developer; no command output or secret is
    # returned to the privileged process. The filename is a local hint.
    print(json.dumps({"ok": False, "error": error,
                      "log": log_path.name if log_path is not None else None}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
