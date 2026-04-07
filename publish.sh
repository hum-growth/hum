#!/usr/bin/env bash
set -euo pipefail

SKILL_FILE="$(dirname "$0")/SKILL.md"

if [[ ! -f "$SKILL_FILE" ]]; then
  echo "Error: SKILL.md not found at $SKILL_FILE" >&2
  exit 1
fi

# Parse name and version from SKILL.md frontmatter
NAME=$(awk '/^---/{f++} f==1 && /^name:/{print $2; exit}' "$SKILL_FILE")
VERSION=$(awk '/^---/{f++} f==1 && /^  version:/{print $2; exit}' "$SKILL_FILE")

if [[ -z "$NAME" || -z "$VERSION" ]]; then
  echo "Error: could not parse name or version from SKILL.md frontmatter" >&2
  exit 1
fi

echo "Publishing skill: $NAME @ $VERSION"

clawhub skill publish "$(dirname "$0")" \
  --slug "$NAME" \
  --name "$NAME" \
  --version "$VERSION" \
  --tags latest
