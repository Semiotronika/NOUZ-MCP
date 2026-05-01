# NOUZ — Semantic MCP Server for Your Knowledge Base

Works with Obsidian, Logseq, and any directory of Markdown files.

> *Structure emerges from content.*

Semantic tools for knowledge bases, research workflows, and AI agents.

[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![MCP](https://img.shields.io/badge/protocol-MCP_stdio-lightgrey.svg)](https://modelcontextprotocol.io)
[![PyPI](https://img.shields.io/badge/pypi-nouz--mcp-orange.svg)](https://pypi.org/project/nouz-mcp/)

🇷🇺 [Русская версия](README.md)

---

## Why NOUZ

When your knowledge base grows, folders are no longer enough. Your AI agent sees files, but it does not understand how your documents, ideas, and materials connect.

NOUZ gives your agent semantic coordinates. Each note gets a domain sign, a hierarchy level, and connections to other notes. The domain is assigned from the file's content — or manually by you, if you prefer strict hierarchy.

---

## What It Does

NOUZ sits between your note base and your AI agent. It helps turn scattered Markdown files into a graph that can be used through MCP:

1. **Automatic Classification (Semantics)**  
   You define "Cores" — base domains of your knowledge base, such as Systems Analysis, Data & Science, and Engineering. When you add a new note, NOUZ reads its text, compares vectors, and proposes a domain sign or a combination of domains.

2. **Bridge Discovery Between Domains**  
   The server builds a directed graph (DAG) and finds non-obvious intersections between disciplines:
   - *Semantic bridges:* two notes from different domains talk about the same thing.
   - *Tag bridges:* notes share hidden concepts at the tag level.
   - *Analogies:* notes play the same structural role in different sciences (e.g., "framework" in IT and "taxonomy" in biology).

3. **Base Evolution Tracking (Drift)**  
   NOUZ aggregates data bottom-up. If a module started in one domain while new notes gradually pull it into another, the server shows the divergence (`core_drift`).

Depending on your needs, NOUZ works in three modes: from a simple graph (**LUCA**) to a strict 5-level hierarchy (**SLOI**).

---

## How It Works

1. You describe domains in `config.yaml` — what each does, what language it speaks.
2. The server turns descriptions into vector etalons (locally, via LM Studio or Ollama).
3. Each new note is projected onto these axes. Sign is determined by content, or by you.
4. L4 gets a domain profile from text classification, while L3/L2 aggregate `core_mix` from child nodes. If a module's `sign` diverges from `core_mix`, the server reports `core_drift`.

**Three types of bridges** find connections between notes from different domains: semantic (texts are close), tag (concepts overlap), analogy (similar role in the graph).

---

## Quick Start

```bash
pip install nouz-mcp
OBSIDIAN_ROOT=/path/to/vault nouz-mcp
```

Without `config.yaml`, the server starts in **LUCA** mode — graph without semantics, works immediately.

To enable semantic mode, create a local config from the template:

```bash
cp config.template.yaml config.yaml
```

On Windows PowerShell:

```powershell
Copy-Item config.template.yaml config.yaml
```

Or from source:

```bash
git clone https://github.com/Semiotronika/NOUZ-MCP
cd NOUZ-MCP
pip install -r requirements.txt
cp config.template.yaml config.yaml
OBSIDIAN_ROOT=./vault python server.py
```

Connect to Claude Desktop, Cursor, OpenCode, or any MCP client:

```json
{
  "mcpServers": {
    "nouz": {
      "command": "nouz-mcp",
      "env": {
        "OBSIDIAN_ROOT": "/path/to/vault",
        "NOUZ_CONFIG": "/absolute/path/to/config.yaml",
        "EMBED_API_URL": "http://127.0.0.1:1234/v1"
      }
    }
  }
}
```

---

## MCP Tools

| Tool | Purpose |
|------------|-------|
| `suggest_metadata` | Sign, level, bridges, drift warnings |
| `write_file` | Write a note with YAML frontmatter |
| `read_file` | Read a note + metadata |
| `calibrate_cores` | Update core reference vectors |
| `recalc_signs` | Recalculate signs for all notes |
| `recalc_core_mix` | Recalculate bottom-up aggregation |
| `index_all` | Re-index the entire base |
| `format_entity_compact` | Formula `(children)[sign]{parents}` |
| `embed` | Get a vector for text |
| `list_files` | List with filters by level, sign |
| `get_children` / `get_parents` | Graph traversal |
| `suggest_parents` | Find parents for an orphan |
| `add_entity` | Create an entity in one step (auto sign, tags, parents) |
| `process_orphans` | Auto-fill files without markup |

---

## Configuration

Minimal `config.yaml`:

```yaml
mode: prizma

etalons:
  - sign: S
    name: Systems Analysis
    text: >
      Methodology for analysing complex objects: feedback loops,
      emergent properties, self-regulation, bifurcation points.
      Cybernetics, synergetics, dissipative structures, catastrophe
      theory, autopoiesis — tools for understanding how the whole
      exceeds the sum of its parts. Not data and not code — a way
      of thinking about how parts form a whole and why systems
      behave non-linearly.
  - sign: D
    name: Data & Science
    text: >
      Physics and cosmology: from subatomic particles to the large-scale
      structure of the Universe. Lagrangians, curvature tensors, scattering
      cross-sections, quarks, bosons, fermions, plasma, vacuum fluctuations,
      cosmic microwave background, cosmological constant, decoherence.
      Pure science about the nature of matter, energy and spacetime.
  - sign: E
    name: Engineering
    text: >
      Software engineering, machine learning and infrastructure: writing
      and debugging code, deployment, containerisation, neural networks,
      inference, tokenisation, data serialisation, microservices, CI/CD,
      automated testing, refactoring, Git, Docker, Kubernetes, APIs.
      The practical discipline of building computational systems from
      architecture to production.

thresholds:
  sign_spread: 0.05
  confident_spread: 60.0
  pattern_second_sign_threshold: 30.0
  semantic_bridge_threshold: 0.55
  structural_bridge_threshold: 0.55
  parent_link_threshold: 0.55

artifact_signs:
  - sign: β
    name: Note
    text: Short note, observation, fragment.
  - sign: δ
    name: Concept
    text: Definition, concept, entity description.
  - sign: ζ
    name: Reference
    text: External source, documentation, link, citation.
  - sign: σ
    name: Log
    text: Session log, chronology, dialogue record.
  - sign: μ
    name: News
    text: News item, update, release note.
  - sign: λ
    name: Hypothesis
    text: Hypothesis, assumption, speculative idea.
  - sign: 🝕
    name: Specification
    text: Technical specification, instruction, requirements.
```

After setup, run `calibrate_cores` — the server creates reference vectors.
Check pairwise cosines: mean-centered between different domains should be
noticeably lower than raw. If all pairs are roughly equal — strengthen the differences in texts.

`etalons` are semantic domains compared through embeddings.
`artifact_signs` describe the material type of L5 artifacts: note, concept, reference, log, news, hypothesis, or specification. This is a heuristic label, not a separate embedding etalon.

### Real Calculation Example

Here are actual results for the S/D/E etalons using the `text-embedding-granite-embedding-278m-multilingual` model:

```
=== Pairwise Cosine (raw) ===
S↔D: 0.5894    S↔E: 0.5862    D↔E: 0.6022

=== Pairwise Cosine (mean-centered) ===
S↔D: -0.5059   S↔E: -0.5117   D↔E: -0.4822
```

Negative mean-centered values are a good result here: after subtracting the mean vector, domains are well-separated. Self-classification: S→99.4%, D→97.5%, E→96.9%.

| Variable | Default | Description |
| --- | --- | --- |
| `OBSIDIAN_ROOT` | `./obsidian` | Path to vault |
| `NOUZ_CONFIG` | *(empty)* | Absolute path to `config.yaml`; if omitted, the server looks in the current working directory |
| `EMBED_PROVIDER` | `openai` | `openai`, `lmstudio`, `ollama` |
| `EMBED_API_URL` | `http://127.0.0.1:1234/v1` | Embedding endpoint |
| `EMBED_API_KEY` | *(empty)* | API key, if needed |
| `EMBED_MODEL` | *(empty)* | Model name |

---

## Privacy

| Component | Local? |
|-----------|-----------|
| Embeddings (LM Studio / Ollama) | ✅ Yes |
| Your notes | ✅ Yes |
| NOUZ server | ✅ Yes |
| AI agent context (Claude, ChatGPT) | ❌ Goes to cloud |

Everything critical stays on your machine.

---

## Development

```bash
git clone https://github.com/Semiotronika/NOUZ-MCP
cd NOUZ-MCP
pip install -e .
python test_server.py
```

---

## Links

- 🌐 [semiotronika.ru](https://semiotronika.ru)
- 📦 [PyPI](https://pypi.org/project/nouz-mcp/)
- 🗂️ [Glama Registry](https://glama.ai/mcp/servers/Semiotronika/NOUZ-MCP)
- 💬 [Telegram](https://t.me/volnaya_sreda)
- 🐙 [GitHub](https://github.com/Semiotronika/NOUZ-MCP)

## Research Context

NOUZ is an engineering MCP server; it does not require the theoretical material below. For readers interested in the research frame behind the project: [Recursive Self-Organization as a Universal Principle](https://doi.org/10.5281/zenodo.19595850).

---

MIT License © 2026 Semiotronika

*Cosines are computed. Syntax changes. Semantics remains.*

<!-- mcp-name: io.github.Semiotronika/NOUZ-MCP -->
