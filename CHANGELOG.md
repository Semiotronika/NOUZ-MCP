# Changelog

## 3.2.2 - released 2026-05-17

### Changed

- `search_chunks` now supports `score_mode=auto/raw/centered`. Unscoped large
  searches default to mean-centered cosine scoring to reduce anisotropic
  embedding background, while scoped `path` searches keep raw cosine by default.
- `search_chunks` returns diagnostic scoring metadata: active `score`,
  `score_raw`, `score_centered`, `score_mode`, `candidate_count`,
  `centroid_norm`, and `score_gap`.

### Added

- Added `scripts/benchmark_chunk_scoring.py`, an anonymized raw-vs-centered
  benchmark that reports aggregate retrieval geometry without note paths,
  headings, titles, or note text.

### Verification

- `python -m pytest -q`
- `python test_server.py`
- `python -m py_compile scripts\benchmark_chunk_scoring.py`
- `python -m build --sdist --wheel`
- `python -m twine check dist/*`

## 3.2.1 - released 2026-05-14

### Fixed

- Require concrete chunk evidence before returning vocabulary-based
  `tag_candidates` from `suggest_metadata`, preventing loose text matches from
  proposing tags without a supporting chunk/snippet.

### Verification

- Live Obsidian DB rehearsal and live indexing with `index_all with_embeddings=true`.
- `python -m pytest -q`
- `python test_server.py`

## 3.2.0 - released 2026-05-14

### Added

- Added deterministic Markdown chunking as a low-level retrieval primitive:
  `nouz_mcp.chunks.chunk_markdown`, plus read-only MCP tools `chunk_text` and
  `chunk_file`.
- Added SQLite `chunk_embeddings` storage, chunk embedding refresh during
  `index_all with_embeddings=true`, and the read-only `search_chunks` MCP tool.
- Added `NOUZ_READ_ONLY=true` to hide and block mutating MCP tools.
- Added manual `analogy` as an accepted `parents_meta.link_type`; NOUZ does not
  auto-generate analogy links.
- Added read-only `tag_bridges` suggestions from shared canonical YAML tags.

### Changed

- Clarified chunk span metadata: `start_char`/`end_char` now bound the returned
  chunk text including overlap, while `body_start_char`/`body_end_char` mark the
  non-overlap body span.
- Chunk ids now use vault-relative source identifiers, so preview chunks from
  `chunk_file` and indexed chunks from `index_all` share the same id contract.
- Chunking now ignores Markdown headings inside fenced code blocks.
- Graph traversal now uses recursive SQLite queries for descendant/cycle checks,
  and file-summary lookup is batched to avoid oversized SQLite `IN` clauses.
- In `NOUZ_READ_ONLY=true`, read-only tools no longer refresh the SQLite cache
  unless `NOUZ_CACHE_WRITE=true` is set; startup DB init/index/calibration is
  skipped under the same guard.
- Tags are now explicit metadata only: NOUZ no longer calls an LLM to infer
  tags, and `add_entity`/`process_orphans` write tags only when they are passed
  explicitly.
- Explicit YAML tags are canonicalized for storage and tag-bridge matching:
  leading `#` is removed, case is folded, spaces/underscores become `-`,
  optional namespaces like `area/topic` are preserved, and obvious non-tags
  such as hex colors, URLs, numeric-only tokens, and placeholders are rejected.
- `suggest_metadata` now includes `tag_quality`, a read-only diagnostic showing
  which explicit tags were accepted and which raw values were discarded.
- `suggest_metadata` now also proposes read-only `tag_candidates` from the
  existing YAML tag vocabulary and explicit inline hashtags, plus
  `candidate_tag_bridges` that would become valid if those tags are accepted.
- Tag candidates include chunk-based `evidence` with chunk id, heading,
  coordinates, and snippet without requiring stored chunk embeddings.

### Verification

- `python -m compileall -q nouz_mcp pytest_smoke.py scripts`
- `python -m pytest -q`
- `python test_server.py`

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
