#!/usr/bin/env bash
# Build the pinned Element static output in a disposable checkout. Does not install or deploy.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo 'Usage: bash element-web-overlay/build.sh /path/to/pinned/element-web' >&2
    exit 2
fi
overlay=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source=$(cd -- "$1" && pwd -P)
cd -- "$source"

node -e 'const [major, minor] = process.versions.node.split(".").map(Number); if (major < 22 || major === 22 && minor < 18) process.exit(1)'
[[ $(pnpm --version) == 12.14.0 ]] || { echo 'Element requires pinned pnpm 12.14.0' >&2; exit 1; }
[[ -d node_modules ]] || { echo 'Run the authorized frozen lockfile install in this release checkout first' >&2; exit 1; }
[[ ! -e apps/web/webapp ]] || { echo 'Use a fresh checkout with no existing app build' >&2; exit 1; }

python3 -B "$overlay/apply.py" "$source" --apply
VERSION=1.12.35 pnpm --dir apps/web build
[[ -s apps/web/webapp/index.html && -d apps/web/webapp/bundles ]]
printf 'Review the static release output: %s/apps/web/webapp\n' "$source"
