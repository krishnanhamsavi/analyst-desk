"""Start the API server.

    python serve.py              # http://127.0.0.1:8000, docs at /docs
    python serve.py --reload     # auto-restart while developing
"""

from __future__ import annotations

import argparse

# Apply the TLS fix before uvicorn imports anything that opens a connection.
from core import netsetup

netsetup.apply()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Analyst Desk API server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="Restart on code changes")
    args = parser.parse_args()

    import uvicorn

    print(f"\n  Analyst Desk API  →  http://{args.host}:{args.port}")
    print(f"  Interactive docs  →  http://{args.host}:{args.port}/docs\n")

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
