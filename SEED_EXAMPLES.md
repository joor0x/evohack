# Seed Examples and LLM Seeding Cheatsheet

This document shows practical command lines to seed the GA with category-focused payloads and to use LLM seeding to diversify the initial population quickly.

## Using LLM Seeding

- Seed 25% of the initial population with a coder model:

```
python evohack.py --profile juice-search --url http://localhost:31337 \
  --gen 6 --pop 24 --use-llm-mutation --llm-fitness --model gemma3:4b \
  --llm-seed-ratio 0.25 --seed-model qwen3-coder:30b --seed-categories xss
```

- Seed exactly 6 individuals with a smaller model:

```
python evohack.py --profile juice-login --url http://localhost:31337 \
  --gen 6 --pop 24 --use-llm-mutation --llm-fitness --model gemma3:4b \
  --llm-seed-count 6 --seed-model qwen3:8b --seed-categories inj_sql,jwt
```

## Category-Focused Runs

### XSS (search)

```
python evohack.py --profile juice-search --url http://localhost:31337 \
  --gen 10 --pop 24 --seed-categories xss,xss_polyglot,headers \
  --llm-seed-count 6 --seed-model gemma3:4b
```

### SQL Injection (login)

```
python evohack.py --profile juice-login --url http://localhost:31337 \
  --gen 10 --pop 24 --seed-categories inj_sql,common \
  --llm-seed-ratio 0.25 --seed-model qwen3:8b
```

### NoSQL (feedback/search JSON)

```
python evohack.py --profile juice-feedback --url http://localhost:31337 \
  --gen 10 --pop 24 --seed-categories inj_nosql,headers \
  --llm-seed-count 5 --seed-model gemma3:4b
```

### Redirect/Header Injection

```
python evohack.py --profile custom --url http://localhost:31337/ \
  --method GET --header-name Referer --gen 8 --pop 24 \
  --seed-categories redir,headers --llm-seed-count 6
```

### Upload Size/Type

```
python evohack.py --profile juice-upload --url http://localhost:31337 \
  --gen 8 --pop 24 --seed-categories upload,lfi,ssti \
  --llm-seed-ratio 0.2
```

### OSINT / Sensitive Files

```
python evohack.py --path-bruteforce --url http://localhost:31337 \
  --path-prefixes assets,public,static,uploads --gen 6 --pop 24 \
  --seed-categories osint,backups,logs,secrets,lfi --llm-seed-count 6
```

### Scenarios (Login + Token then Action)

```
python evohack.py --scenario scenarios/juice_login_admin.json \
  --gen 8 --pop 24 --seed-categories inj_sql,jwt \
  --llm-seed-count 6 --seed-model qwen3-coder:30b
```

## Tips

- Prefer `--llm-seed-count` to guarantee a fixed number of LLM-generated individuals. Otherwise use `--llm-seed-ratio` for proportional seeding.
- Keep the mutation/crossover LLM model lightweight for speed (`gemma3:4b` or `qwen3:8b`) and use a bigger `--seed-model` if you want more diverse initial seeds.
- Narrow `--seed-categories` to the endpoint’s likely class (e.g., `xss` for `/search`, `inj_sql` for `/login`).

