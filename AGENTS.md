# AGENTS.md - NOUZ-MCP

This is the clean public package repo for NOUZ. It should stay publishable and
free of local tokens, private paths, private Obsidian details, and experimental
workflow language.

## First Read

- Root workspace `AGENTS.md` and `WORKSPACE_MAP.md`
- `README.md` and `README_EN.md`
- `CHANGELOG.md` before release notes
- `NOUZ_ARCH.md` from the workspace root when syncing behavior from local
  `server_NOUZ.py`

## Source Of Truth

- Package entry: `nouz_mcp/server.py`
- Root compatibility entry: `server.py`
- Public config template: `config.template.yaml`
- Tests: `test_server.py`, `pytest_smoke.py`

Local-only behavior belongs in workspace `server_NOUZ.py` until it is reviewed
as a portable engine primitive.

## Commands

```powershell
python -m compileall -q nouz_mcp pytest_smoke.py scripts
python -m pytest -q
python test_server.py
```

For docs/release packaging, also run the build and twine checks used in the
release notes before publishing.

## Boundaries

- Do not push, tag, release, or publish to PyPI without explicit maintainer approval.
- Do not introduce local absolute paths, private symbols, or private vault
  assumptions.
- Keep public terminology stable: NOUZ is the semantic knowledge engine; LINZA
  is a separate agent workspace product.
- Prefer small tested engine primitives over high-level operator workflows.
