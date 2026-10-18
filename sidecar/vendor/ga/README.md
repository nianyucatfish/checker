# GenericAgent components

Source: https://github.com/lsdefine/GenericAgent (local checkout copied 2026-09-29).
MIT license is retained in LICENSE.

TMWebDriver.py, simphtml.py and the repository-root plugin/browser-bridge/ directory reuse GA's bridge, page simplification and browser extension.
code_runner.py extracts code_run and smart_format from ga.py without importing GA's agent, model keys or memory.
file_ops.py extracts GA's line-oriented file_read, unique-match file_patch and overwrite/append/prepend file_write. The Checker adapter resolves relative paths under temp and limits write/patch targets to temp. GA's response-body fallback and file-reference expansion are not used by MCP.

Checker adaptations: diagnostic output goes to stderr for MCP stdio; disconnected tabs fail instead of falling back to another tab; code execution uses the sidecar interpreter, bounded output, temporary scripts, timeout and process-group cancellation.

Install the bundled extension manually in Chrome if no GA bridge is already connected. Existing GA bridge connections on the default port are reused. No desktop control libraries or screenshot-to-model transport are included.

Checker browser extension adaptations (2026-10-18): see [extension source, provenance and installation](../../../plugin/browser-bridge/README.md). The extension directory contains its own MIT license for standalone distribution.
