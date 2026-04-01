#!/usr/bin/env bash
set -euo pipefail

echo "Starting OWASP Juice Shop on http://localhost:3000 ..."
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker not found in PATH. Please install Docker." >&2
  exit 1
fi

docker run --rm -p 3000:3000 --name evohack-juice bkimminich/juice-shop

