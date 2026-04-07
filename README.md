# NOUZ — MCP Server for Obsidian

Unified MCP server for semantic knowledge management in Obsidian. Works with local embedding models — no cloud required.

[![NOUZ-MCP MCP server](https://glama.ai/mcp/servers/KVANTRA-dev/NOUZ-MCP/badges/score.svg)](https://glama.ai/mcp/servers/KVANTRA-dev/NOUZ-MCP)

## Quick Start

```bash
git clone https://github.com/KVANTRA-dev/NOUZ-MCP
cd NOUZ-MCP
pip install -r requirements.txt

export OBSIDIAN_ROOT=./vault
export EMBED_API_URL=http://127.0.0.1:1234/v1  # LM Studio or Ollama

python server.py
```

Connect via any MCP-compatible client (Claude Desktop, etc.) using stdio transport.

## Modes

NOUZ has three operating modes. Set via `MODE` env var or `config.yaml`.

| Mode | Description | Embeddings required |
|------|-------------|-------------------|
| **luca** | Pure graph — YAML + links, no semantic classification | No |
| **prizma** | Graph + semantics — sign classification, core_mix, semantic bridges | Yes |
| **sloi** | Strict 5-level hierarchy — cycle detection, violation warnings | Yes |

**Start with LUCA** if you just want graph navigation. Add PRIZMA/SLOI when you're ready to define your semantic cores.

## Note Format

Each note uses YAML frontmatter:

```yaml
---
type: module
level: 3
sign: T
status: active
tags:
  - research
parents:
  - Parent Note Name
parents_meta:
  - entity: Parent Note Name
    link_type: hierarchy
---

Your note content here.
```

### Five Levels

| Level | Type | Description |
|-------|------|-------------|
| 1 | core | Top-level domain (e.g. "Mathematics") |
| 2 | pattern | Knowledge area within domain |
| 3 | module | Grouping within field |
| 4 | quant | Concrete atomic note |
| 5 | artifact | Leaf note, reference, log |

The `parents` field is a list of note names (used by Obsidian/ExcaliBrain for wikilinks). The `parents_meta` field carries structured link metadata for NOUZ graph logic. Keep them in sync — NOUZ handles this automatically on write.

## Semantic Etalons (Prizma / Sloi)

Etalons define the "axes" of your semantic space — reference texts that represent each knowledge domain. NOUZ classifies notes by cosine similarity to these vectors.

Copy `config.template.yaml` to `config.yaml`:

```yaml
mode: prizma

etalons:
  - sign: T
    name: Technology
    text: "programming software architecture infrastructure machine learning neural networks algorithms"
  - sign: S
    name: Science
    text: "physics chemistry biology mathematics formal logic theorems cosmology quantum mechanics"
  - sign: H
    name: Humanities
    text: "philosophy psychology sociology history literature art culture ethics cognitive science"
```

**Best practices:**
- Use 3–5 domains — more makes classification noisy
- Each etalon should be a dense list of keywords, not a sentence
- Run `calibrate_cores` after changing etalons, then check pairwise cosine similarity
- Aim for similarity < 0.55 between any two etalons for clean separation

## Tools

### All modes

| Tool | Description |
|------|-------------|
| `read_file(path)` | Read note with parsed YAML metadata |
| `write_file(path, content, metadata)` | Write note with YAML frontmatter |
| `list_files(level, sign, tags)` | Filter and list notes from vault |
| `index_all(with_embeddings)` | Index vault to SQLite DB |
| `embed(text)` | Get embedding vector from configured provider |
| `get_parents(path)` | Get parent nodes (files linking to this) |
| `get_children(path)` | Get child nodes (files this links to) |
| `format_entity_compact(path)` | Return entity formula: `(children)[entity]{parents}` |

### Prizma / Sloi only

| Tool | Description |
|------|-------------|
| `calibrate_cores()` | Recompute etalon embeddings from config |
| `recalc_signs()` | Auto-assign signs to all indexed notes |
| `recalc_core_mix()` | Aggregate core_mix bottom-up through DAG |
| `suggest_metadata(path)` | Suggest sign, level, core_mix for a note |
| `suggest_parents(path)` | Suggest parent links by vector similarity |

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OBSIDIAN_ROOT` | `./obsidian` | Path to your Obsidian vault |
| `MODE` | `luca` | `luca` / `prizma` / `sloi` — overridden by config.yaml |
| `EMBED_PROVIDER` | `openai` | `openai` / `gigachat` / `ollama` |
| `EMBED_API_URL` | `http://127.0.0.1:1234/v1` | Embedding API endpoint |
| `EMBED_MODEL` | — | Model name (optional, uses provider default) |
| `EMBED_API_KEY` | — | API key (leave empty for local models) |
| `EMBED_ENABLED` | `true` | Set to `false` to disable embeddings entirely |

## Example Workflow

```bash
# 1. Start server
python server.py

# 2. Index your vault
index_all(with_embeddings=true)

# 3. Calibrate etalons (Prizma/Sloi)
calibrate_cores()

# 4. Classify all notes
recalc_signs()

# 5. Query
suggest_parents("path/to/note.md")
list_files(sign="T", level=4)
```

## Links

- [Website](https://kvantra-dev.github.io/nouz/)
- [KVANTRA](https://kvantra-dev.github.io/)
- [Glama registry](https://glama.ai/mcp/servers/KVANTRA-dev/NOUZ-MCP)

---

MIT License © 2026 KVANTRA

## Changelog

### v2.1.1
- Minor fixes and refactoring

### v2.1.0
- Initial public release
