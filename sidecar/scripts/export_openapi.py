"""Dump current OpenAPI schema to JSON for reproducible TS type generation.

Usage:
    python -m sidecar.scripts.export_openapi > openapi.json
    # Phase 2: openapi-typescript openapi.json -o app/src/api/types.ts
"""

import json
import sys

from sidecar.api import app


def main():
    schema = app.openapi()
    json.dump(schema, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
