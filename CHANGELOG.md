# Changelog

## Unreleased

### Added

- Added deterministic Markdown chunking as a low-level retrieval primitive:
  `nouz_mcp.chunks.chunk_markdown`, plus read-only MCP tools `chunk_text` and
  `chunk_file`.
- Added SQLite `chunk_embeddings` storage, chunk embedding refresh during
  `index_all with_embeddings=true`, and the read-only `search_chunks` MCP tool.
- Added `NOUZ_READ_ONLY=true` to hide and block mutating MCP tools.
- Added manual `analogy` as an accepted `parents_meta.link_type`; NOUZ does not
  auto-generate analogy links.
- Added `scripts/release_check.py` as a single local verification command for
  compile, pytest, and `test_server.py`.

### Changed

- Clarified chunk span metadata: `start_char`/`end_char` now bound the returned
  chunk text including overlap, while `body_start_char`/`body_end_char` mark the
  non-overlap body span.
- Chunking now ignores Markdown headings inside fenced code blocks.
- In `NOUZ_READ_ONLY=true`, read-only tools no longer refresh the SQLite cache
  unless `NOUZ_CACHE_WRITE=true` is set; startup DB init/index/calibration is
  skipped under the same guard.
- Tags are now explicit metadata only: NOUZ no longer calls an LLM to infer
  tags, `add_entity`/`process_orphans` do not write generated tags, and
  `suggest_metadata` keeps `tag_bridges` empty instead of proposing automatic
  tag-based links.
- Tag handling is centralized through explicit YAML `tags`: blank/duplicate
  values are dropped, leading `#` is stripped for YAML storage, and legacy
  `concepts` values are not promoted into indexed tags.

### Verification

- `python scripts/release_check.py`

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
