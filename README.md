# NOUZ — Семантический MCP-сервер для вашей базы знаний

Работает с Obsidian, Logseq и любыми директориями Markdown-файлов.

> *Структура появляется из содержания.*

Семантические инструменты для баз знаний, исследований и AI-агентов.

[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://python.org)
[![MCP](https://img.shields.io/badge/protocol-MCP_stdio-lightgrey.svg)](https://modelcontextprotocol.io)
[![PyPI](https://img.shields.io/badge/pypi-nouz--mcp-orange.svg)](https://pypi.org/project/nouz-mcp/)

🇬🇧 [English version](README_EN.md)

---

## Зачем нужен Nouz

Когда база знаний растёт, папок уже мало. Агент видит файлы, но не понимает, как связаны ваши документы, идеи и материалы.

NOUZ даёт агенту семантические координаты. Каждая заметка получает знак домена, уровень в иерархии и связи с другими заметками. Домен присваивается именно из содержания файла, или же вами вручную, если вы хотите строгую иерархию.

---

## Что делает

NOUZ выступает прослойкой между вашей базой заметок и AI-агентом. Он помогает превратить разрозненные Markdown-файлы в граф, с которым можно работать через MCP:

1. **Автоматическая классификация (Семантика)**
   Вы задаете "Ядра" — базовые домены вашей базы (например: Systems Analysis, Data & Science, Engineering). Когда вы добавляете новую заметку, NOUZ читает ее текст, сравнивает векторы и предлагает доменный знак или комбинацию доменов.

2. **Поиск мостов между доменами**
   Сервер строит направленный граф (DAG) и находит неочевидные пересечения между дисциплинами:
   - *Семантические мосты:* две заметки из разных доменов говорят об одном и том же.
   - *Теговые мосты:* у заметок есть общие скрытые концепты на уровне тегов.
   - *Аналогии:* заметки играют одинаковую структурную роль в разных науках (например, "фреймворк" в IT и "таксономия" в биологии).

3. **Отслеживание эволюции базы (Дрифт)**
   NOUZ агрегирует данные снизу вверх. Если модуль начинался как один домен, а новые заметки постепенно уводят его в другой, сервер покажет расхождение (`core_drift`).

В зависимости от ваших задач NOUZ работает в трех режимах: от простого графа (**LUCA**) до строгой 5-уровневой иерархии (**SLOI**).

---

## Как это работает

1. Вы описываете домены в `config.yaml` — чем каждый занимается, на каком языке говорит.
2. Сервер превращает описания в векторы-эталоны (локально, через LM Studio или Ollama).
3. Каждая новая заметка проецируется на эти оси. Знак определяется содержанием, или вами.
4. L4 получает доменный профиль из классификации текста, а L3/L2 собирают `core_mix` из дочерних узлов. Если `sign` модуля расходится с `core_mix`, сервер сообщает о `core_drift`.

**Три типа мостов** находят связи между заметками из разных доменов: семантические (тексты близки), теговые (концепты пересекаются), аналогические (похожая роль в графе).

---

## Быстрый старт

```bash
pip install nouz-mcp
OBSIDIAN_ROOT=/path/to/vault nouz-mcp
```

Без `config.yaml` сервер стартует в режиме **LUCA** — граф без семантики, работает сразу.

Чтобы включить семантический режим, создайте локальный конфиг из шаблона:

```bash
cp config.template.yaml config.yaml
```

В Windows PowerShell:

```powershell
Copy-Item config.template.yaml config.yaml
```

Или из исходников:

```bash
git clone https://github.com/Semiotronika/NOUZ-MCP
cd NOUZ-MCP
pip install -r requirements.txt
cp config.template.yaml config.yaml
OBSIDIAN_ROOT=./vault python server.py
```

Подключение к Claude Desktop, Cursor, Opencode или любому MCP-клиенту:

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

## Инструменты MCP

| Инструмент | Зачем |
|------------|-------|
| `suggest_metadata` | Знак, уровень, мосты, drift-предупреждения |
| `write_file` | Записать заметку с YAML-разметкой |
| `read_file` | Прочитать заметку + метаданные |
| `calibrate_cores` | Обновить векторы-эталоны ядер |
| `recalc_signs` | Пересчитать знаки всех заметок |
| `recalc_core_mix` | Пересчитать агрегацию снизу вверх |
| `index_all` | Переиндексировать всю базу |
| `format_entity_compact` | Формула `(дети)[знак]{родители}` |
| `embed` | Получить вектор для текста |
| `list_files` | Список с фильтрами по уровню, знаку |
| `get_children` / `get_parents` | Траверс графа |
| `suggest_parents` | Найти родителей для сироты |
| `add_entity` | Создать сущность в один шаг (авто sign, tags, parents) |
| `process_orphans` | Автозаполнение файлов без разметки |

---

## Конфигурация

Минимальный `config.yaml`:

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

После настройки запустите `calibrate_cores` — сервер создаст эталонные векторы.
Проверьте попарные косинусы: mean-centered между разными доменами должен быть
заметно ниже сырого. Если все пары примерно одинаковые — усильте различия в текстах.

`etalons` — это смысловые домены, которые сравниваются через эмбеддинги.
`artifact_signs` — тип материала для артефактов L5: заметка, концепт, ссылка, лог, новость, гипотеза или спецификация. Это эвристическая метка, а не отдельный embedding-эталон.

### Реальный пример расчёта

Вот фактические результаты для эталонов S/D/E с моделью `text-embedding-granite-embedding-278m-multilingual`:

```text
=== Pairwise Cosine (raw) ===
S↔D: 0.5894    S↔E: 0.5862    D↔E: 0.6022

=== Pairwise Cosine (mean-centered) ===
S↔D: -0.5059   S↔E: -0.5117   D↔E: -0.4822
```

Отрицательные mean-centered значения здесь хороший результат: после вычитания среднего вектора домены хорошо расходятся. Самоклассификация: S→99.4%, D→97.5%, E→96.9%.

| Переменная | По умолчанию | Описание |
| --- | --- | --- |
| `OBSIDIAN_ROOT` | `./obsidian` | Путь к хранилищу |
| `NOUZ_CONFIG` | *(пусто)* | Абсолютный путь к `config.yaml`; если не задан, сервер ищет конфиг в текущей директории |
| `EMBED_PROVIDER` | `openai` | `openai`, `lmstudio`, `ollama` |
| `EMBED_API_URL` | `http://127.0.0.1:1234/v1` | Эндпоинт для эмбеддингов |
| `EMBED_API_KEY` | *(пусто)* | API-ключ, если нужен |
| `EMBED_MODEL` | *(пусто)* | Имя модели |

---

## Приватность

| Компонент | Локально? |
|-----------|-----------|
| Эмбеддинги (LM Studio / Ollama) | ✅ Да |
| Ваши заметки | ✅ Да |
| Сервер NOUZ | ✅ Да |
| Контекст AI-агента (Claude, ChatGPT) | ❌ Уходит в облако |

Всё критичное остаётся на вашей машине.

---

## Разработка

```bash
git clone https://github.com/Semiotronika/NOUZ-MCP
cd NOUZ-MCP
pip install -e .
python test_server.py
```

---

## Ссылки

- 🌐 [semiotronika.ru](https://semiotronika.ru)
- 📦 [PyPI](https://pypi.org/project/nouz-mcp/)
- 🗂️ [Glama Registry](https://glama.ai/mcp/servers/Semiotronika/NOUZ-MCP)
- 💬 [Telegram](https://t.me/volnaya_sreda)
- 🐙 [GitHub](https://github.com/Semiotronika/NOUZ-MCP)

## Теоретический контекст

NOUZ — инженерный MCP-сервер. Его можно использовать без знакомства с теоретическими материалами. Для тех, кому интересна исследовательская рамка проекта: [«Рекурсивная самоорганизация как универсальный принцип»](https://doi.org/10.5281/zenodo.19595850).

---

MIT License © 2026 Semiotronika

*Косинусы считаются. Синтаксис меняется. Семантика остаётся.*

<!-- mcp-name: io.github.Semiotronika/NOUZ-MCP -->
