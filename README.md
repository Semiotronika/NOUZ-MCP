<img width="1000" height="120" alt="banner 1" src="https://github.com/user-attachments/assets/7aad8385-fdce-4c3c-8103-97656ed791db" />

# Nouz — Semantic Knowledge Graph for Obsidian

[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![MCP](https://img.shields.io/badge/protocol-MCP_stdio-lightgrey.svg)](https://modelcontextprotocol.io)
[![PyPI](https://img.shields.io/badge/pypi-nouz--mcp-orange.svg)](https://pypi.org/project/nouz-mcp/)

An MCP server that builds a Directed Acyclic Graph (DAG) on top of your Obsidian vault. Notes are classified based on their semantic content.

```bash
pip install nouz-mcp
OBSIDIAN_ROOT=/path/to/vault nouz-mcp
```

---

## Three Modes

<p align="center"><img src="banner_three_modes.svg" alt="Nouz Three Modes"></p>

Start with **luca** if you want to build the structure manually. Switch to **prizma** or **sloi** when you want full Nouz capabilities.

---

## How Classification Works

### Reference Vectors (Cores)

You define 2–5 knowledge domains in `config.yaml` as descriptive texts. The server converts these texts into embeddings and stores them as reference vectors — coordinate axes in the embedding space.

```yaml
mode: prizma

etalons:
  - sign: S
    text: "physics thermodynamics entropy quantum mechanics cosmology topology
           statistical mechanics information theory complexity emergence"
  - sign: T
    text: "software engineering algorithms machine learning neural networks
           distributed systems programming languages language models automation"
  - sign: H
    text: "philosophy epistemology cognitive science linguistics sociology
           ethics phenomenology semiotics history of science"
```

**On the quality of cores:** Separation between cores is more important than their content. Run `calibrate_cores` after writing them and check `pairwise_cosine` — values below 0.55 between any two cores indicate good separation.

This is analogous to choosing orthogonal basis vectors. In a well-calibrated system, each note projects primarily onto one axis.

### Sign Assignment

When a note is indexed with embeddings enabled, its content vector is compared against all reference vectors:

```
scores     = {S: cosine(note, core_S), T: cosine(note, core_T), H: cosine(note, core_H)}
spread     = max(scores) - min(scores)
adjusted   = {k: scores[k] - min(scores) for k in scores}
percent    = {k: adjusted[k] / sum(adjusted) * 100 for k in adjusted}
sign       = all domains where percent[k] >= 30%
```

If `spread < 0.05`, the note doesn't project clearly onto any single axis. The sign remains undefined. The domain identity of the note is genuinely undetermined until more content is added or the cores are recalibrated.

**Priority Rule:** A sign manually set in the YAML frontmatter is never overwritten by the classifier. `sign_manual > sign_auto > inherited`.

### Inheritance

The sign flows top-down through hierarchical links:

```
L1 core      ← defined manually, never changes
L2 pattern   ← defined manually + optional second sign from embedding
L3 module    ← inherits sign from parent pattern via hierarchical link
L4 quant     ← hybrid: inherited sign from module + embedding result
L5 artifact  ← inherits from parent quant (hierarchy) or self-assigned (temporary)
```

This mirrors the cortical hierarchy model (Friston, 2009; Bastos et al., 2012): higher-level representations send top-down predictions that constrain processing at lower levels. A hierarchical link in Nouz is structurally equivalent — the parent node's sign sets the prior probability for its children.

### core_mix — Bottom-Up Aggregation

While the sign flows top-down (intent), `core_mix` flows bottom-up (reality):

```
quant (L4)  → updates core_mix of parent module (L3)
module (L3) → aggregates into core_mix of parent pattern (L2)
```

Each module accumulates the averaged domain distribution of all its quants. When the declared sign (top-down, intent) diverges from the core_mix (bottom-up, reality), the system reports a `core_drift`.

This bidirectional flow — top-down constraints, bottom-up evidence — aligns with the predictive coding framework: upper levels generate predictions, lower levels return prediction errors, and the system minimizes the discrepancy.

---

## Semantic Bridges

`suggest_metadata` returns candidates for cross-domain links, marked with `proposed: true` and requiring explicit confirmation before being added to the graph.

**Semantic Bridges** — full-note similarity across different domains:

```
For note A (sign=S) and note B (sign=T):
  if cosine(embed(A), embed(B)) >= 0.55 → proposed semantic link
```

Finds notes that are semantically similar overall but belong to different knowledge domains. A note about thermodynamic entropy and a note about data compression might have high embedding similarity despite having different domain signs. Bridges detect that two notes potentially talk *about the same thing* from different perspectives.

---

## Example: Scientific Knowledge Base

Three cores: **Physics** (Ψ), **Mathematics** (Δ), **Computing** (Σ).

After calibration and indexing:

```
Ψ Physics
├── Ψ Statistical Mechanics          [pattern, sign=Ψ]
│   ├── ΨΔ Entropy and Information   [module, sign=ΨΔ — physics + math above threshold]
│   │   ├── Ψ Boltzmann Entropy      [quant, sign=Ψ, inherited]
│   │   ├── ΨΔ Shannon Entropy       [quant, sign=ΨΔ, embedding + inherited]
│   │   └── Δ Kolmogorov Complexity  [quant, sign=Δ, embedding dominates]
│   └── Ψ Phase Transitions          [module, sign=Ψ]
│       ├── Ψ Ising Model            [quant]
│       └── ΨΣ Mean Field Theory     [quant, sign=ΨΣ — also computing]
└── ΨΔ Topology                      [pattern, second sign from embedding]
    └── Δ Persistent Homology        [module, sign=Δ]
        └── ΔΣ TDA Pipeline          [quant, sign=ΔΣ — math + computing]

Σ Computing
├── Σ Machine Learning               [pattern]
│   ├── Σ Neural Networks            [module]
│   │   ├── Σ Backpropagation        [quant]
│   │   └── ΣΔ Attention Mechanism   [quant, sign=ΣΔ — computing + math]
│   └── ΣΨ Generative Models         [module, core_mix shows drift towards Ψ]
```

After running `recalc_core_mix`, the `Statistical Mechanics` module shows:

```
sign (intent):    Ψ
core_mix (reality): {Ψ: 52%, Δ: 41%, Σ: 7%}
core_drift: detected — significant Δ component not reflected in sign
```

This signals that the module has grown beyond its declared domain. Drift is not an error; it's information about how the knowledge base has evolved.

Example of a semantic bridge: `Shannon Entropy` (sign=Ψ) and `Attention Mechanism` (sign=ΣΔ) have high embedding similarity despite being in different domains — both involve weighted information selection under constraints. The bridge is proposed automatically.

---

## Quick Start

```bash
# Installation
pip install nouz-mcp

# Configuration
cat > config.yaml << 'EOF'
mode: prizma
etalons:
  - sign: S
    text: "your first domain"
  - sign: T
    text: "your second domain"
EOF

# Run
OBSIDIAN_ROOT=/path/to/vault nouz-mcp
```

Add to Claude Desktop:

```json
{
  "mcpServers": {
    "nouz": {
      "command": "nouz-mcp",
      "env": {
        "OBSIDIAN_ROOT": "/path/to/vault",
        "EMBED_API_URL": "http://127.0.0.1:1234/v1"
      }
    }
  }
}
```

---

## MCP Tools

**All Modes**

| Tool | Description |
|------|-------------|
| `read_file` | Read a note with YAML metadata |
| `write_file` | Create or update a note |
| `list_files` | Filter by level, sign, subfolder |
| `get_children` | Traverse DAG downwards |
| `get_parents` | Traverse DAG upwards |
| `index_all` | Reindex the vault |
| `format_entity_compact` | Compact formula: `(children)[entity]{parents}` |

**prizma / sloi only**

| Tool | Description |
|------|-------------|
| `calibrate_cores` | Embed core texts → reference vectors |
| `recalc_signs` | Reclassify all notes using embeddings |
| `recalc_core_mix` | Propagate bottom-up aggregation |
| `suggest_metadata` | Classify a note, propose bridges |
| `suggest_parents` | Find parent candidates by semantic similarity |
| `embed` | Get the embedding vector for any text |

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OBSIDIAN_ROOT` | `./obsidian` | Path to the vault |
| `MODE` | `luca` | `luca`, `prizma`, or `sloi` |
| `EMBED_ENABLED` | `true` | Enable embeddings |
| `EMBED_PROVIDER` | `openai` | `openai`, `lmstudio`, `gigachat` |
| `EMBED_API_URL` | `http://127.0.0.1:1234/v1` | Endpoint for embeddings |
| `EMBED_API_KEY` | `` | API key if required |

Everything runs locally. Your data does not leave your machine unless you connect a cloud AI agent.

---

## Development

```bash
pip install -e .
pytest
```

---

[Glama](https://glama.ai/mcp/servers/KVANTRA-dev/NOUZ-MCP) · [Website](https://kvantra-dev.github.io/nouz/) · [PyPI](https://pypi.org/project/nouz-mcp/)

*Maria Belkina · KVANTRA · MIT License*
