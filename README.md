# NOUZ — MCP Server for local knowledge management
[![NOUZ-MCP MCP server](https://glama.ai/mcp/servers/KVANTRA-dev/NOUZ-MCP/badges/score.svg)](https://glama.ai/mcp/servers/KVANTRA-dev/NOUZ-MCP)
**v2.1.0**

## Install

```bash
pip install -r requirements.txt
```

## Run

```bash
export OBSIDIAN_ROOT=./obsidian
export EMBED_PROVIDER=ollama  # openai, gigachat
python server.py
```

## Config

Edit `config.yaml`:

| Field | Type | Description |
|-------|------|-------------|
| `mode` | string | luca / prizma / sloi |
| `levels` | dict | core=1, pattern=2, module=3, quant=4, artifact=5 |
| `thresholds.semantic_bridge_threshold` | float | 0.55 default |
| `etalons` | list | Core signs with text for embedding |
| `prizma_modes` | dict | Keywords per thinking style |

## Tools

### Basic
- `read_file(path)` — read note with YAML frontmatter
- `write_file(path, content, metadata)` — write note with YAML
- `list_files(level, sign, tags, subfolder)` — filter files
- `index_all(with_embeddings)` — index to DB
- `embed(text)` — get embedding

### Navigation
- `get_parents(path)` — files linking to this file
- `get_children(path)` — files this file links to

### Semantics (prizma / sloi)
- `calibrate_cores()` — recalculate etalon embeddings
- `recalc_signs()` — recalculate sign_auto
- `recalc_core_mix()` — recalculate core_mix
- `suggest_metadata(path)` — suggest level/sign
- `suggest_parents(path)` — suggest links by embeddings
- `format_entity_compact(path)` — entity formula

## Modes

| Mode | Level Strict | Semantics | Description |
|------|--------------|-----------|-------------|
| luca | ❌ | ❌ | Simple graph |
| prizma | ❌ | ✅ | Semantic bridges + core_mix |
| sloi | ✅ | ✅ | Strict 5-level hierarchy |

## File Format

```yaml
---
level: 2
sign: Ψ
tags: [systems]
---
# Content here
```

## Env Vars

| Variable | Default | Description |
|----------|---------|-------------|
| `OBSIDIAN_ROOT` | ./obsidian | Vault path |
| `EMBED_PROVIDER` | openai | openai / gigachat / ollama |
| `EMBED_API_URL` | http://127.0.0.1:1234/v1 | API endpoint |
| `EMBED_MODEL` | — | Model name |
| `EMBED_API_KEY` | — | API key |

---
[![NOUZ-MCP MCP server](https://glama.ai/mcp/servers/KVANTRA-dev/NOUZ-MCP/badges/card.svg)](https://glama.ai/mcp/servers/KVANTRA-dev/NOUZ-MCP)
---
*Copyright (c) 2026 KVANTRA. MIT License.*
https://kvantra-dev.github.io/nouz
