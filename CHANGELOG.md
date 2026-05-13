# Changelog

## 3.1.0 - released 2026-05-13

This release is a structural refactor and public documentation pass.

### Changed

- Split the former single large server module into focused package modules:
  `config`, `links`, `markdown`, `modes`, `paths`, `semantics`,
  `serialization`, `signs`, `sqlite_store`, `use_cases`, `vault_io`, and
  `vectors`.
- Kept public MCP tool names and result shapes stable while moving application
  workflows and SQLite operations behind clearer internal layers.
- Refined `README.md` and `README_EN.md` around a compact public introduction,
  safe first run through `luca`, semantic mode through `prizma`, and clearer
  human-facing explanations before internal YAML vocabulary.
- Clarified embedding provider documentation: LM Studio should be used as an
  OpenAI-compatible endpoint with `EMBED_PROVIDER=openai`; `ollama` remains the
  separate provider mode.
- Added focused smoke coverage for `suggest_parents`: adjacent-level ranking,
  same/child-level filtering, and unavailable embeddings behavior.

### Packaging

- Removed an unused Markdown frontmatter parsing dependency.
- Included `docs/*.md` in the source distribution.
- Centralized the runtime/package version in `nouz_mcp._version`.
- Packaged the etalon diagnostic utility as the `nouz-calc-etalons` console command.
- Bumped package, runtime, test, and `server.json` versions to `3.1.0`.

### Verification

- `python -m py_compile nouz_mcp\calc_etalons.py scripts\calc_etalons.py pytest_smoke.py test_server.py`
- `python test_server.py`
- `python -m pytest -q`
- `python scripts\calc_etalons.py --help`
- `python -m build --no-isolation --sdist --wheel --outdir .build-tmp\dist-final-approved-readme-20260513`
- `python -m twine check .build-tmp\dist-final-approved-readme-20260513\*`
- Inspected the built wheel metadata: version `3.1.0` and console commands
  `nouz-mcp` and `nouz-calc-etalons`.

## 3.0.3 - 2026-05-04

- Public cleanup after the formula/runtime split.
- Removed legacy public cleanup remnants.
- Published to GitHub, PyPI, and public registries as the stable 3.0.x baseline.
