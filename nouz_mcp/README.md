# Nouz MCP Server

Unified MCP Server for Obsidian — graph-based note management with semantic classification.

## Features

- **Three modes**: `luca`, `prizma`, `sloi`
- Graph-based note organization with parent-child relationships
- Semantic classification using embedding similarity
- Auto mode selection based on query content
- Full-text search and path suggestions

## Installation

```bash
pip install nouz-mcp
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OBSIDIAN_ROOT` | `./obsidian` | Path to Obsidian vault |
| `MODE` | `prizma` | Server mode: `luca`, `prizma`, or `sloi` |
| `EMBED_ENABLED` | `true` | Enable embedding-based features |
| `EMBED_PROVIDER` | `openai` | Embedding provider: `openai`, `gigachat`, `lmstudio` |
| `EMBED_MODEL` | - | Embedding model name |
| `EMBED_API_URL` | `http://127.0.0.1:1234/v1` | Embedding API endpoint |
| `LLM_API_URL` | `http://127.0.0.1:1234/v1` | LLM API endpoint |

### Running

```bash
# Basic usage
nouz-mcp

# With custom vault path
OBSIDIAN_ROOT=/path/to/vault nouz-mcp

# With specific mode
MODE=sloi nouz-mcp
```

## MCP Tools

- `list_notes` — List notes in vault
- `read_note` — Read note content with metadata
- `write_note` — Create/update note
- `search_notes` — Full-text search
- `get_parents` — Get parent notes
- `suggest_parents` — Suggest similar parent notes (prizma/sloi)
- `suggest_metadata` — Auto-suggest metadata
- `index_all` — Reindex all notes
- `embed` — Get text embedding

## Development

```bash
pip install -e .
```

---

<!-- mcp-name: io.github.KVANTRA-dev/nouz -->
