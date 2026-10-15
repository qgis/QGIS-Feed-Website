#!/usr/bin/env bash
# Format Nix files with nixfmt. Backs `nix fmt`.
#
# nixfmt itself only takes files: given a directory it formats nothing, and
# given no arguments at all it reads stdin and blocks. `nix fmt` passes the
# tree root as a bare '.', which is why it has to be expanded here.
set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

# `nix fmt` with no arguments passes '.'; `nix fmt some/file.nix` passes that
# path. Directories are expanded, files are taken as given, so both work.
args=("$@")
if [ ${#args[@]} -eq 0 ]; then
    args=("${PROJECT_ROOT}")
fi

targets=()
for arg in "${args[@]}"; do
    if [ -d "${arg}" ]; then
        while IFS= read -r -d '' file; do
            targets+=("${file}")
        done < <(find "${arg}" -type f -name '*.nix' -not -path '*/.git/*' -print0)
    elif [ -e "${arg}" ]; then
        targets+=("${arg}")
    else
        echo "No such file or directory: ${arg}" >&2
        exit 1
    fi
done

if [ ${#targets[@]} -eq 0 ]; then
    echo "No .nix files to format"
    exit 0
fi

nixfmt "${targets[@]}"
echo "Formatted ${#targets[@]} Nix file(s)"
