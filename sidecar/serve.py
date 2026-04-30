"""sidecar.serve - convenience entry: python -m sidecar.serve [--port PORT]"""

import argparse
import os

import uvicorn


def main():
    parser = argparse.ArgumentParser(prog="python -m sidecar.serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("SIDECAR_PORT", "8765")))
    parser.add_argument("--reload", action="store_true", help="dev mode")
    args = parser.parse_args()

    uvicorn.run(
        "sidecar.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
