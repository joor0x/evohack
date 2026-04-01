# EvoHack-LLM

Evolutionary Algorithms + LLM for Automated Pentesting.

EvoHack-LLM is an experimental framework that combines a **Genetic Algorithm (GA)** with **LLM reasoning** (Ollama, OpenAI, Anthropic) to autonomously discover and exploit web vulnerabilities. Built for OWASP Juice Shop and similar targets.

<img width="1324" height="520" alt="image" src="https://github.com/user-attachments/assets/a4b077be-9837-4106-a1cb-0d9106cc721a" />

## Table of Contents

- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [LLM Providers](#llm-providers)
- [Profiles](#profiles)
- [Seeds & Categories](#seeds)
- [Context Scraping & Auto-Retarget](#context-scraping)
- [DOM XSS Detection](#dom-xss)
- [Spider Modes](#spider)
- [Path Bruteforce](#path-bruteforce)
- [Multi-Step Scenarios](#scenarios)
- [Fitness Scoring](#fitness)
- [Memory (ChromaDB)](#memory)
- [Upload Handling](#upload)
- [Extending](#extending)
- [Recipes](#recipes)
- [Legal](#legal)

---

<a name="quick-start"></a>
## Quick Start

### Requirements

- Python 3.10+
- Docker (for Juice Shop)
- Optional: [Ollama](https://ollama.ai) for local LLM, or API keys for OpenAI / Anthropic
- Optional: [Playwright](https://playwright.dev/python/) for SPA rendering and DOM XSS

### Setup

```bash
# Install dependencies
pip install -r requirements.txt

# (Optional) Install Playwright for DOM XSS and SPA spider
pip install playwright
python -m playwright install chromium

# Start Juice Shop
docker compose up -d
# or: docker run --rm -p 3000:3000 bkimminich/juice-shop

# (Optional) Pull a local model
ollama pull llama3.2
```

### Your First Run

```bash
# Basic GA against Juice Shop login with LLM assistance
python evohack.py --profile juice-login --url http://localhost:3000 \
  --gen 15 --pop 24 --llm-fitness --use-llm-mutation --model llama3.2
```

### Instruction-Guided Attack with Context Scraping

Use `--instruction` to guide the GA toward a specific goal, and `--llm-context-scrape` to let the tool discover the best attack surface automatically:

```bash
python evohack.py --url http://localhost:3000 --gen 15 --pop 20 \
  --llm-provider openai --model gpt-4o-mini \
  --llm-fitness --use-llm-mutation --llm-context-scrape \
  --llm-seed-ratio 0.1 --llm-offspring-per-gen 6 \
  --instruction "Gain access as administrator"
```

This will scrape the target, discover endpoints and SPA routes, ask the LLM to pick the best attack surface for the instruction, and evolve payloads against it.

---

<a name="architecture"></a>
## Architecture

```
Population → Evaluate → Select → Crossover/Mutate → New Generation
           ↑                              |               |
           +------ Heuristics + LLM ------+---------------+
```

- **Genotype**: a string payload (SQLi, XSS, JWT, etc.)
- **Population**: set of payloads competing by fitness
- **Mutation/Crossover**: category-aware mutators + optional LLM-guided operators
- **Fitness**: heuristic signals from HTTP responses + optional LLM scoring (70% heuristic + 30% LLM)
- **Target**: HTTP client inserting `{payload}` in body/query/header/path, or `BrowserTarget` for DOM XSS, or `ScenarioRunner` for multi-step flows

### Key Modules

| Module | Purpose |
|---|---|
| `evohack.py` | CLI entry point |
| `evohack/ga.py` | Genetic algorithm (population, selection, crossover, mutation) |
| `evohack/fitness.py` | Fitness evaluator (heuristic + LLM scoring) |
| `evohack/mutators.py` | Category-aware mutation operators |
| `evohack/targets.py` | `TargetClient` (HTTP) and `BrowserTarget` (Playwright/DOM XSS) |
| `evohack/seeds.py` | Seed payloads by vulnerability category |
| `evohack/llm.py` | LLM clients (Ollama, OpenAI, Anthropic via aisuite) |
| `evohack/context.py` | HTML/JS scraping and SPA route discovery |
| `evohack/scenario.py` | Multi-step scenario runner |
| `evohack/classifier.py` | Auto-classifies target categories from URL/method |
| `evohack/memory.py` | ChromaDB-backed payload memory |

---

<a name="llm-providers"></a>
## LLM Providers

Three providers are supported (unified via `aisuite`):

| Provider | Flag | Model Example | Auth |
|---|---|---|---|
| Ollama (local) | `--llm-provider ollama` | `llama3.2` | None (local) |
| OpenAI | `--llm-provider openai` | `gpt-4o-mini` | `OPENAI_API_KEY` |
| Anthropic | `--llm-provider anthropic` | `claude-3-haiku-20240307` | `ANTHROPIC_API_KEY` |

Configure via `.env` file (copy from `.env.example`):

```bash
cp .env.example .env
# Fill in your API keys
```

Environment variables: `LLM_PROVIDER`, `OLLAMA_HOST`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`.

Seeds and scenarios can use different providers/models:

```bash
python evohack.py --profile juice-login \
  --llm-provider openai --model gpt-4o-mini \
  --seed-provider anthropic --seed-model claude-3-haiku-20240307 \
  --scenario-provider ollama --scenario-model llama3.2
```

---

<a name="profiles"></a>
## Profiles

Preconfigured Juice Shop targets:

| Profile | Endpoint | Injection Point |
|---|---|---|
| `juice-login` | `POST /rest/user/login` | `email` field in JSON body |
| `juice-search` | `GET /rest/products/search` | `q` query parameter |
| `juice-feedback` | `POST /api/Feedbacks` | `comment` field in JSON body |
| `juice-upload` | `POST /file-upload` | multipart file content |

```bash
# Custom target
python evohack.py --profile custom \
  --url http://host/endpoint --method POST \
  --headers '{"Content-Type":"application/json"}' \
  --body-template '{"email":"{payload}","password":"test"}'
```

Use `--param-name` for query/form injection, `--header-name` for header injection, `--path-template` for path injection.

---

<a name="seeds"></a>
## Seeds & Categories

Seeds are grouped by vulnerability type. Select with `--seed-categories` (comma-separated) or `all`:

`inj_sql`, `inj_nosql`, `xss`, `xss_polyglot`, `ssti`, `lfi`, `xxe`, `redir`, `ssrf`, `jwt`, `headers`, `upload`, `chatbot`, `common`, `osint`, `backups`, `logs`, `secrets`, `anti_automation`, `spam`, `bruteforce_user`, `bruteforce_pass`, `param_injection`, `hash_exfil`

The classifier auto-infers relevant categories from the target (e.g., search → `xss`; login → `inj_sql`/`jwt`).

---

<a name="context-scraping"></a>
## Context Scraping & Auto-Retarget

`--llm-context-scrape` fetches the target's HTML and JS to build a context summary for LLM prompts. It discovers:

- Forms, inputs, buttons, and links
- REST API endpoints from network requests and JS bundles
- **SPA hash routes** (e.g., `/#/search`, `/#/login`) from rendered DOM and JS route definitions

When combined with `--instruction`, the tool **auto-retargets**: it asks the LLM which discovered endpoint best matches the attack goal and switches the GA target automatically.

For example, if the instruction is "Perform a DOM XSS attack" and the scrape discovers `/#/search?q=`, the tool will switch to a browser-based target instead of hitting the REST API.

---

<a name="dom-xss"></a>
## DOM XSS Detection

When `--llm-context-scrape` discovers SPA hash routes with query parameters and the instruction mentions XSS, EvoHack automatically:

1. Switches to a **BrowserTarget** (headless Chromium via Playwright)
2. Navigates to `http://target/#/search?q={payload}` for each candidate payload
3. Listens for **`dialog` events** (alert/confirm/prompt) — confirmed XSS execution
4. Checks **DOM reflection** of the payload in the rendered page
5. Scores: `dialog_fired` = **+500 fitness**, `dom_reflected` = **+80 fitness**

This means the GA can discover working DOM XSS payloads like:

```
http://localhost:3000/#/search?q=<iframe src="javascript:alert(`xss`)">
```

Requirements: Playwright must be installed (`pip install playwright && python -m playwright install chromium`).

---

<a name="spider"></a>
## Spider Modes

Crawl the target and test discovered endpoints:

```bash
# Basic spider: follow links, test GET params
python evohack.py --spider --url http://localhost:3000 --gen 6 --pop 16 --seed-categories all

# Dynamic SPA spider (Playwright): captures XHR/fetch
python evohack.py --spider-render --url http://localhost:3000 --gen 4 --pop 16

# Static JS analysis: extract /api and /rest endpoints from JS bundles
python evohack.py --spider-static-js --url http://localhost:3000 --gen 4 --pop 16
```

Filter and export results:

```bash
python evohack.py --spider --url http://localhost:3000 --gen 4 --pop 12 \
  --top 10 --min-fitness 150 --out results.json
```

---

<a name="path-bruteforce"></a>
## Path Bruteforce

Inject payloads into URL paths to find sensitive files and exposed routes:

```bash
python evohack.py --path-bruteforce --url http://localhost:3000 \
  --gen 6 --pop 24 --seed-categories osint,backups,logs,secrets,lfi --out paths.json

# With path prefixes
python evohack.py --path-bruteforce --url http://localhost:3000 \
  --path-prefixes assets,public,static,uploads --gen 6 --pop 24 \
  --seed-categories osint,backups,logs,secrets --top 10
```

---

<a name="scenarios"></a>
## Multi-Step Scenarios

Define multi-request flows with value captures (e.g., JWT tokens) reused across steps:

```bash
python evohack.py --scenario scenarios/juice_login_admin.json \
  --gen 8 --pop 24 --seed-categories inj_sql,jwt
```

Scenario JSON format:
- `steps[]`: each with `method`, `url`, `headers`, `body_template`/`param_name`/`path_template`
- `captures[]`: extract values from responses:
  - JSON path: `{"name": "token", "type": "json", "path": "authentication.token"}`
  - Regex: `{"name": "csrf", "type": "regex", "pattern": "name=\"_csrf\" value=\"([^\"]+)\""}`

Available scenarios:
- `juice_login_admin.json` — Login + use JWT for authenticated requests
- `juice_feedback_captcha_bypass.json` — Fetch CAPTCHA answer, submit feedback
- `juice_feedback_spam.json` — Multiple feedback submissions with varied headers
- `juice_login_bruteforce.json` — Brute-force admin password

---

<a name="fitness"></a>
## Fitness Scoring

### Heuristic Signals

| Signal | Score |
|---|---|
| 5xx status + stack traces | +200 / +80 |
| 2xx status | +50 |
| 401/403 | +30 |
| 3xx redirect | +40 |
| Payload reflection in response | +60 |
| XSS markers (`<script`, `onerror=`, `alert(1)`) | +100 |
| DOM XSS dialog fired (BrowserTarget) | +500 |
| DOM reflection (BrowserTarget) | +80 |
| JWT/token/authorization in response | +200 |
| JWT-like string pattern | +160 |
| Email leak | +80 |
| Private key markers | +400 |
| bcrypt/md5/sha1 hash patterns | +50..220 |
| `/etc/passwd` content | +350 |
| Auth cookies set | +120 |
| External redirect with payload | +120 |
| High latency (>1.5s) | +60..140 |

### LLM Scoring

With `--llm-fitness`, the LLM scores each response 0..500 with an explanation. Final score: **70% heuristic + 30% LLM**.

---

<a name="memory"></a>
## Memory (ChromaDB)

Store high-fitness payloads and reuse them in similar contexts:

```bash
python evohack.py --profile juice-login --memory-enable --memory-top-n 8 \
  --store-min-fitness 180 --llm-fitness --use-llm-mutation
```

- Uses ChromaDB with local persistence (`--memory-dir`, default `.evohack_chroma`)
- Supports OpenAI embeddings (`OPENAI_API_KEY`) or Ollama embeddings (`OLLAMA_EMBED_MODEL`) for semantic similarity
- Falls back to text-based similarity if no embedding model is available
- Deduplicates by payload+host+path hash

---

<a name="upload"></a>
## Upload Handling

The `juice-upload` profile sends multipart with file content as payload. Use directives to control file name and size:

```
[[FILENAME=invoice.exe;SIZE=150000]]
<file content here>
```

Combine with `--seed-categories upload,lfi,ssti`.

---

<a name="extending"></a>
## Extending

- Add seeds in `evohack/seeds.py` or via `--seeds-file <path.json>`
- Add target profiles in `evohack/targets.py` or use CLI flags
- Add scenario flows in `scenarios/*.json`
- Tune GA parameters: `--crossover-rate`, `--mutation-rate`, `--llm-offspring-per-gen`

---

<a name="recipes"></a>
## Recipes

```bash
# Instruction-guided attack with context scraping (OpenAI)
python evohack.py --url http://localhost:3000 --gen 15 --pop 20 \
  --llm-provider openai --model gpt-4o-mini \
  --llm-fitness --use-llm-mutation --llm-context-scrape \
  --llm-seed-ratio 0.1 --llm-offspring-per-gen 6 \
  --instruction "Gain access as administrator"

# DOM XSS discovery (auto-detects hash routes and uses browser evaluation)
python evohack.py --url http://localhost:3000 --gen 15 --pop 20 \
  --llm-provider openai --model gpt-4o-mini \
  --llm-fitness --use-llm-mutation --llm-context-scrape \
  --seed-categories xss,xss_polyglot \
  --instruction "Perform a DOM XSS attack"

# Local LLM (Ollama)
python evohack.py --profile juice-login --url http://localhost:3000 \
  --gen 15 --pop 24 --llm-fitness --use-llm-mutation \
  --llm-provider ollama --model llama3.2

# Static JS spider with LLM
python evohack.py --spider-static-js --url http://localhost:3000 \
  --gen 4 --pop 16 --use-llm-mutation --llm-fitness --model llama3.2 \
  --seed-categories all --top 10 --min-fitness 150 --out results.json

# Path bruteforce with prefixes
python evohack.py --path-bruteforce --url http://localhost:3000 \
  --path-prefixes assets,public,static,uploads --gen 4 --pop 16 \
  --seed-categories osint,backups,logs,secrets,lfi --top 10 --out paths.json

# Multi-step scenario (login + authenticated request)
python evohack.py --scenario scenarios/juice_login_admin.json \
  --gen 8 --pop 24 --seed-categories inj_sql,jwt

# CAPTCHA bypass
python evohack.py --scenario scenarios/juice_feedback_captcha_bypass.json \
  --gen 6 --pop 24 --seed-categories anti_automation,spam

# Brute-force admin password
python evohack.py --scenario scenarios/juice_login_bruteforce.json \
  --gen 8 --pop 32 --seed-categories bruteforce_pass,anti_automation

# Auto-start Juice Shop
python evohack.py --profile juice-login --auto-juice --llm-fitness --model llama3.2

# Silent mode (suppress verbose output)
python evohack.py --profile juice-login --url http://localhost:3000 \
  --gen 15 --pop 24 --silent
```

---

<a name="legal"></a>
## Legal

This tool is for **educational research in controlled environments only**. Do not use against targets without explicit authorization. Many attack techniques (XSS, SQLi, RCE, DoS) can cause real harm.
