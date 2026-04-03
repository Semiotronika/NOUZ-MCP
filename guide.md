# Nouz — guide

Nouz — MCP server for Obsidian. Turns notes into a semantic graph.

## Start

```bash
pip install -r requirements.txt
export OBSIDIAN_ROOT=./obsidian
export EMBED_PROVIDER=ollama
python server.py
```

## Note format

```yaml
---
level: 2
sign: Ψ
tags: [systems]
---
# Content
```

**level** — 1 (core) to 5 (artifact)
**sign** — any symbol for graph visualization

## First steps

1. Create 3-5 notes with level and sign
2. Run `index_all` — server indexes the base
3. Run `calibrate_cores` and `recalc_signs` — train etalons
4. Ask: "show notes about X", "suggest connections for Y"

## Main queries

- `list_files` — show all notes
- `get_parents(path)` — who links to the note
- `get_children(path)` — who the note links to
- `suggest_metadata(path)` — suggest level/sign
- `suggest_parents(path)` — suggest connections
- `calibrate_cores` — recalculate etalons
- `recalc_signs` — reclassify

## Config

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mode` | prizma | luca / prizma / sloi |
| `semantic_bridge_threshold` | 0.55 | Semantic bridge threshold |

---

> _Cosine calculates, syntax changes, semantics remains._
