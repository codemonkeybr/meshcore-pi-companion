---
name: Bug report
about: Report a bug or unexpected behavior
title: "[Bug] "
labels: bug
assignees: ""
---

## Describe the bug
A clear description of what went wrong.

## To reproduce
1. Steps to reproduce (e.g. "Open channels, click room X, send a message")
2. ...

## Expected behavior
What you expected to happen.

## Environment
- **OS:** (e.g. Windows 11, Ubuntu 24.04, Raspberry Pi OS)
- **Transport:** (Serial / TCP / BLE / SPI)
- **Radio/firmware:** (if known)
- **Python:** `uv run python --version`
- **Node:** `node --version` (if frontend-related)

## App logs
Please paste logs from the app (backend/terminal output). If possible, run with debug logging first.

**How to run with debug:**

- **Using the run script:**  
  `./scripts/run_remoterm.sh --debug --host 0.0.0.0 --port 8000`

- **Or with uvicorn directly:**  
  `MESHCORE_LOG_LEVEL=DEBUG uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`

Then paste the relevant log output below (include the lines around when the bug occurred):

```
(paste log output from the app here)
```

## Additional context
Screenshots, config snippets (redact secrets), or other details that might help.
