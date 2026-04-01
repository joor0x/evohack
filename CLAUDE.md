# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

EvoHack-LLM is an experimental framework combining a Genetic Algorithm (GA) with local LLM reasoning (via Ollama) to autonomously discover and exploit web vulnerabilities. Targets OWASP Juice Shop and similar web apps.

- **Main entry**: `evohack.py` (CLI) or `evohack` as package
- **Core modules**: `evohack/` (ga.py, mutators.py, fitness.py, targets.py, seeds.py, llm.py, spider*.py, classifier.py, scenario.py)
- **Scenarios**: `scenarios/*.json` for multi-step flows

## Common Commands

```bash
# Start Juice Shop
docker compose up -d
# or: ./scripts/run_juice_shop.sh

# Run GA with Juice Shop profile
python evohack.py --profile juice-login --url http://localhost:3000 --gen 15 --pop 24

# With LLM fitness + mutation
python evohack.py --profile juice-login --url http://localhost:3000 --gen 15 --pop 24 --llm-fitness --use-llm-mutation --model llama3.2

# Spider modes
python evohack.py --spider --url http://localhost:3000 --gen 6 --pop 16 --seed-categories all
python evohack.py --spider-render --url http://localhost:3000 --gen 4 --pop 16
python evohack.py --spider-static-js --url http://localhost:3000 --gen 4 --pop 16

# Path bruteforce
python evohack.py --path-bruteforce --url http://localhost:3000 --gen 6 --pop 24 --seed-categories osint,backups,logs,secrets

# Multi-step scenario
python evohack.py --scenario scenarios/juice_login_admin.json --gen 8 --pop 24 --seed-categories inj_sql,jwt
```

## Architecture

- **Genotype**: string payload
- **Population**: set of payload individuals competing by fitness
- **Operators**: category-aware mutators (XSS/SQLi/NoSQL/SSTI/LFI/JWT/Upload) + optional LLM-guided mutation/crossover
- **Fitness**: heuristic signals (status codes, reflection, tokens, latency) + optional LLM scoring (70% heuristics + 30% LLM)
- **Targets**: HTTP client inserts `{payload}` in body/query/header/path or ScenarioRunner for multi-step flows
- **Seeds**: vulnerability categories (inj_sql, inj_nosql, xss, xss_polyglot, ssti, lfi, xxe, redir, ssrf, jwt, upload, etc.)

## Key CLI Options

| Flag | Description |
|------|-------------|
| `--profile` | Preconfigured profile (juice-login, juice-search, juice-feedback, juice-upload, custom) |
| `--url` | Target endpoint |
| `--gen` | Number of generations |
| `--pop` | Population size |
| `--seed-categories` | Seed categories (comma-separated, or "all") |
| `--llm-fitness` | Use LLM for contextual fitness scoring |
| `--use-llm-mutation` | Use LLM for mutation/crossover |
| `--model` | Ollama model for LLM operations |
| `--out` | Output JSON file for spider results |
| `--scenario` | Path to multi-step scenario JSON |
| `--timeout` | HTTP request timeout in seconds |

## Important Files

- `evohack.py`: Main CLI entry point with argparse
- `pyproject.toml`: Project configuration
- `evohack/ga.py`: Genetic algorithm (Population, evaluate, select, crossover, mutate)
- `evohack/fitness.py`: FitnessEvaluator with heuristic + LLM scoring
- `evohack/mutators.py`: Category-aware mutation operators
- `evohack/targets.py`: TargetClient and Juice Shop profiles
- `evohack/seeds.py`: Seed payloads by vulnerability category
- `evohack/scenario.py`: Scenario and ScenarioRunner for multi-step flows
- `scenarios/*.json`: Prebuilt multi-step scenarios
- `docker-compose.yml`: Juice Shop container definition

## Legal

This tool is for educational research in controlled environments only. Do not use against targets without authorization.