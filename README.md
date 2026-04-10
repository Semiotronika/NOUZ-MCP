<img width="1000" height="120" alt="banner 1" src="https://github.com/user-attachments/assets/7aad8385-fdce-4c3c-8103-97656ed791db" />

# NOUZ — Semantic Knowledge Graph for Obsidian

> One server. Three approaches. Your notes find their own place in the graph.

[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![MCP](https://img.shields.io/badge/protocol-MCP_stdio-lightgrey.svg)](https://modelcontextprotocol.io)
[![PyPI](https://img.shields.io/badge/pypi-nouz--mcp-orange.svg)](https://pypi.org/project/nouz-mcp/)

---

## Why NOUZ?

You write in Obsidian. A lot. Over time your vault turns into a mess — hundreds of notes connected somehow, with no system or logic.

NOUZ fixes this. It reads your notes, analyzes the content, and **builds the knowledge graph itself** — what belongs where, what "sign" each note has, where the semantic connections between different branches are.

No API keys needed. NOUZ works with your own embedding model.

---

## What NOUZ Does

### Builds the Graph

Add `parents:` to a note — and NOUZ automatically places it in the hierarchy. No manual folder sorting.

### Classifies by Content

Using embeddings (local model), NOUZ understands what the note is about and assigns it a "sign" — T (technology), S (science), H (humanities) or any other you define.

### Finds Hidden Connections

Notes from different branches can be semantically close. NOUZ finds these bridges and suggests linking them.

### Tracks Knowledge Drift

Over time the content of a branch changes. NOUZ shows when the actual composition of notes diverges from the declared topic — this is `core_drift`, a signal that the branch has grown beyond its domain.

---

## Three Modes for Different Tasks

| Mode | What It Does | Embeddings? |
|-------|------------|-------------|
| **LUCA** | Pure graph — only links and hierarchy | ❌ |
| **PRIZMA** | Full semantics — classification, bridges, drift | ✅ |
| **SLOI** | Strict 5-level hierarchy with control | ✅ |

Start with **LUCA** — just connect and add links. Move to PRIZMA or SLOI when you want semantics.

---

## Quick Start

```bash
# Install via PyPI (recommended)
pip install nouz-mcp

# Run
OBSIDIAN_ROOT=/path/to/vault nouz-mcp
```

Or from source:

```bash
git clone https://github.com/KVANTRA-dev/NOUZ-MCP
cd NOUZ-MCP
pip install -r requirements.txt
OBSIDIAN_ROOT=./vault python server.py
```

Connect to Claude Desktop, Cursor, Opencode, or any other MCP client:

```json
{
  "mcpServers": {
    "nouz": {
      "command": "nouz-mcp",
      "env": {
        "OBSIDIAN_ROOT": "/path/to/vault",
        "MODE": "prizma",
        "EMBED_API_URL": "http://127.0.0.1:1234/v1"
      }
    }
  }
}
```

---

## How It Works

### 1. You Write a Note

```yaml
---
type: module
level: 3
sign: T
parents:
  - Machine Learning
---

Your note here.
```

### 2. NOUZ Builds the Graph

- Reads YAML → builds DAG (directed acyclic graph)
- Vectorizes text → calculates proximity to your cores
- Proposes sign, level, parents
- Finds bridges to other branches

### 3. Structure Emerges from Content

```
T (core)
 ├── TH (pattern: AI)
 │   ├── TH (module: ML)
 │   │   ├── TH (quant: neural-networks.md) — T
 │   │   └── TS (quant: transformers.md) — T
 │   └── TH (module: Ethics)
 │       └── TH (quant: ai-safety.md) — T
 └── TS (pattern: Physics)
     └── ...
```

The more you write — the smarter your knowledge base becomes, and the agent working with NOUZ.

---

## How Classification Works

### Cores — Coordinate Axes

In `config.yaml` you define 2–5 domains as text descriptions. The server converts them into reference vectors — **coordinate axes** in the multidimensional embedding space.

```yaml
mode: prizma

etalons:
  - sign: T
    name: Technology
    text: "programming software architecture machine learning neural networks"
  - sign: S
    name: Science
    text: "physics chemistry biology mathematics formal logic theorems"
  - sign: H
    name: Humanities
    text: "philosophy psychology sociology history literature ethics"
```

**On core quality:** separation between cores matters more than description accuracy. After writing — run `calibrate_cores` and check `pairwise_cosine`. Values above 0.55 between any two cores means they're too similar and the classifier will confuse domains. This is analogous to orthogonal basis vectors: the more orthogonal — the more accurate the projection.

### Sign Assignment

For each note, its content vector is compared against all reference vectors:

```
scores     = {S: cosine(note, core_S), T: cosine(note, core_T), H: cosine(note, core_H)}
spread     = max(scores) - min(scores)
```

If `spread < 0.05` — the note is equidistant from all cores, sign is undefined. This is not an error: the note genuinely belongs to multiple domains or its content is insufficient for classification.

```
adjusted   = {k: scores[k] - min(scores) for k in scores}
percent    = {k: adjusted[k] / sum(adjusted) * 100 for k in adjusted}
sign       = all domains where percent[k] >= 30%
```

If two domains score ≥ 30% — the sign is composite: `TS`, `SH`. This is not a contradiction, but a spectrum: the note lives on the border between domains.

### Sign Inheritance

The sign flows top-down through hierarchical links:

```
L1 core      ← defined manually, never changes
L2 pattern   ← defined manually + embedding adds second sign (if ≥ 30%)
L3 module    ← inherits sign from parent pattern
L4 quant     ← hybrid: sign from L3 parent + embedding content
L5 artifact  ← inherits sign from parent quant
```

Priority: `sign_manual (YAML) > sign_auto (embedding) > inherited`

Manual sign in YAML is **never overwritten** automatically.

### Sign Confidence: auto vs weak_auto

Spread-normalization answers "which core is *relatively closer*". But it doesn't answer "how close is the note to this core in *absolute terms*".

For this there's `confident_cosine` — a threshold on `max(cosine)`:

```
max_cosine >= confident_cosine → sign_source = "auto"      (confident sign)
max_cosine < confident_cosine  → sign_source = "weak_auto" (relative sign)
```

**What this means in practice:**

`weak_auto` occurs when the note has a relative winner among cores (spread is normal), but in absolute terms all cosines are low. This happens when:
- The note is short or not written in the language of the cores
- The cores don't cover the note's topic well
- The embedding model isn't strong in this domain

**Impact on semantic bridges:**

This is where it matters. Semantic bridges are only proposed between notes with *different* signs — if signs match, they're assumed to be "the same area". But if the sign is `weak_auto` — this assumption is unreliable.

```
sign_source = "auto"      → sign is considered closed. Bridges to the same core are not proposed.
sign_source = "weak_auto" → sign is open. Bridges to notes with the same core are still proposed.
```

This gives you a choice: either set the sign manually (close the domain), or let the system keep proposing connections from all directions.

**Threshold tuning:**

```yaml
thresholds:
  confident_cosine: 0.6  # for most models (e5, BGE, multilingual)
  # confident_cosine: 0.75  # for nomic-embed (high baseline 0.74–0.83)
```

The higher the threshold — the stricter the confidence requirement, the more notes get `weak_auto`. Calibrate for your model.

---

### core_mix — Reality Bottom-Up

The sign flows top-down (intent). `core_mix` flows bottom-up (reality):

```
quant (L4) → updates core_mix of parent module (L3)
module (L3) → aggregates into core_mix of parent pattern (L2)
```

Each module accumulates the averaged domain distribution of all its quants. When the declared sign (intent) diverges from `core_mix` (reality) — the system reports `core_drift`.

This is not an error. It's information about how the knowledge base has evolved. A branch with `sign=T` can gradually accumulate 60% of notes about mathematics — and `core_drift` will show this.

This bidirectional flow — top-down constraints and bottom-up evidence — mirrors the architecture of hierarchical predictive coding systems: upper levels set expectations, lower levels return discrepancies.

---

## Semantic Bridges

`suggest_metadata` returns candidates for cross-domain links. All bridges are marked with `proposed: true` and require explicit confirmation.

**Algorithm:**

```
For note A (sign=S) and note B (sign=T):
  if cosine(embed(A), embed(B)) >= 0.55 → propose link
```

Finds notes that are **semantically similar overall** but belong to different domains. A note about thermodynamic entropy and a note about data compression can have high embedding similarity — both are about efficient information encoding. The bridge detects that they're talking about the same thing from different angles.

The 0.55 threshold is configurable in `config.yaml` via `semantic_bridge_threshold`.

---

## Customize It

```yaml
mode: prizma

etalons:
  - sign: T
    name: Technology & Engineering
    text: "programming software architecture infrastructure machine learning neural networks
           algorithms frameworks database cloud computing"
  - sign: S
    name: Science & Mathematics
    text: "physics chemistry biology mathematics formal logic theorems cosmology quantum
           mechanics research methodology"
  - sign: H
    name: Humanities & Arts
    text: "philosophy psychology sociology history literature art culture ethics
           cognitive science epistemology linguistics"

thresholds:
  sign_spread: 0.05           # minimum spread for classification
  pattern_second_sign_threshold: 30.0  # second sign threshold (%)
  semantic_bridge_threshold: 0.55      # semantic bridge threshold
```

---

## Tools

### All Modes

| Tool | Description |
|-----------|----------|
| `read_file` | Read a note with YAML metadata |
| `write_file` | Create or update a note |
| `list_files` | List with filters by level, sign, subfolder |
| `get_children` | Traverse DAG downwards (all children) |
| `get_parents` | Traverse DAG upwards (parents) |
| `index_all` | Reindex the entire vault |
| `format_entity_compact` | Entity formula: `(children)[entity]{parents}` |

### PRIZMA / SLOI

| Tool | Description |
|-----------|----------|
| `calibrate_cores` | Vectorize core texts → reference vectors |
| `recalc_signs` | Reclassify all notes by embeddings |
| `recalc_core_mix` | Recalculate bottom-up aggregation |
| `suggest_metadata` | Propose sign, level, semantic bridges |
| `suggest_parents` | Find parents by semantic similarity |
| `embed` | Get embedding vector for any text |

---

## Configuration

| Variable | Default | Description |
|-----------|-------------|----------|
| `OBSIDIAN_ROOT` | `./obsidian` | Path to the vault |
| `MODE` | `luca` | Mode: `luca`, `prizma`, or `sloi` |
| `EMBED_ENABLED` | `true` | Enable embeddings |
| `EMBED_PROVIDER` | `openai` | Provider: `openai`, `lmstudio`, `ollama`, `gigachat` |
| `EMBED_API_URL` | `http://127.0.0.1:1234/v1` | Endpoint for embeddings |
| `EMBED_API_KEY` | `` | API key if required |

---

## Privacy: What Stays Local

| Component | Local? |
| -------------------------------------------------- | ----------------------- |
| **Embeddings** (LM Studio / Ollama) | ✅ Yes |
| **Your notes** (raw files) | ✅ Yes |
| **NOUZ server** | ✅ Yes |
| **AI agent context** (what the cloud model sees) | ❌ No — goes to the cloud |

NOUZ runs locally. But when you connect a cloud AI agent (Claude, ChatGPT, etc.), the content it sees — including your notes — goes to that provider's cloud. This is outside NOUZ control.

**Your choice:** use local agents or accept the trade-off.

---

## Who Is It For?

| | |
| ----------------------------- | -------------------------------------------------------------------- |
| **Order lovers** | Want structure without spending time on manual organization |
| **Researchers** | Gather lots of information and want to see connections |
| **AI enthusiasts** | Building knowledge graphs for RAG or agent systems |
| **Everyone with >100 notes** | When folders stop coping |

---

## Development

```bash
pip install -e .
```

---

## Links

- 🌐 [Website](https://kvantra-dev.github.io/nouz/)
- 📦 [PyPI](https://pypi.org/project/nouz-mcp/)
- 🗂️ [Glama Registry](https://glama.ai/mcp/servers/KVANTRA-dev/NOUZ-MCP)
- 💬 [Telegram: Volnaya Sreda](https://t.me/volnaya_sreda)
- 🐙 [GitHub](https://github.com/KVANTRA-dev/NOUZ-MCP)

---

**Structure emerges from content** — you just write.

MIT License © 2026 KVANTRA

---

*Cosines are calculated, syntax changes, semantics remains.*
