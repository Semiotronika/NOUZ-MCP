# NOUZ — MCP Server for local knowledge management
**v2.1.0**

[![NOUZ-MCP MCP server](https://glama.ai/mcp/servers/KVANTRA-dev/NOUZ-MCP/badges/score.svg)](https://glama.ai/mcp/servers/KVANTRA-dev/NOUZ-MCP)

Unified MCP server for semantic knowledge management in Obsidian. Works with local embedding models.

## Quick Start

```bash
git clone https://github.com/KVANTRA-dev/NOUZ-MCP
cd NOUZ-MCP
pip install -r requirements.txt

export OBSIDIAN_ROOT=./vault
export EMBED_PROVIDER=ollama  # openai, gigachat, ollama
export EMBED_API_URL=http://127.0.0.1:1234/v1

python server.py
```

## Modes

| Mode | Description |
|------|-------------|
| **luca** | Graph-based, level for display only |
| **prizma** | Graph-based with semantic classification |
| **sloi** | Strict 5-level hierarchy |

Set mode in config.yaml or via `MODE` env var.

## Minimal Setup

You can start with just YAML frontmatter in your notes:

```yaml
---
level: 2
type: pattern
---
# Your note
```

## Semantic Etalons (Optional)

For better semantic classification (Prizma/Sloi modes), copy `config.template.yaml` to `config.yaml` and define your domains:

```yaml
etalons:
  - sign: T
    name: Technology
    text: "programming software architecture infrastructure machine learning"
  - sign: S
    name: Science
    text: "physics chemistry biology mathematics formal logic theorems"
  - sign: H
    name: Humanities
    text: "philosophy psychology sociology history literature art culture"
```

**Recommendation:** Use 3-4 distinct domains with cosine similarity < 0.55 between them for best results. See `guide.md` for details.

## Tools

- `read_file(path)` — Read note with YAML
- `write_file(path, content, metadata)` — Write note
- `list_files(level, sign, tags)` — Filter files
- `index_all(with_embeddings)` — Index to DB
- `embed(text)` — Get embedding
- `get_parents(path)` — Files linking to this file
- `get_children(path)` — Files this file links to

### Prizma/Sloi only
- `calibrate_cores()` — Recalculate etalon embeddings
- `recalc_signs()` — Recalculate auto-signatures
- `recalc_core_mix()` — Recalculate core_mix
- `suggest_metadata(path)` — Suggest level/sign
- `suggest_parents(path)` — Suggest links by embeddings

## Links

- [Guide](https://github.com/KVANTRA-dev/NOUZ-MCP/blob/main/guide.md)
- [KVANTRA](https://kvantra-dev.github.io/)

MIT License © 2026 KVANTRA
