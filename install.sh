#!/usr/bin/env bash
set -euo pipefail

# Session Manager — installer for local Mac or VPS
# Usage:
#   ./install.sh                  # install locally
#   ./install.sh --vps user@host  # deploy to VPS via scp

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "${1:-}" == "--vps" ]]; then
    TARGET="${2:?VPS target required, e.g. claude@1.2.3.4}"
    echo "Deploying to VPS: $TARGET"
    ssh "$TARGET" "mkdir -p ~/.claude/skills/sessions ~/.claude/scripts"
    scp "$SCRIPT_DIR/sessions.md" "$TARGET":~/.claude/skills/sessions/SKILL.md
    scp "$SCRIPT_DIR/sessions.py" "$TARGET":~/.claude/scripts/
    ssh "$TARGET" "python3 -c 'import textual' 2>/dev/null || pip3 install --user textual --quiet && echo '✓ textual OK'"
    echo "✓ Deployed to $TARGET"
    exit 0
fi

echo "Installing locally to ~/.claude/"
mkdir -p ~/.claude/skills/sessions ~/.claude/scripts
cp "$SCRIPT_DIR/sessions.md" ~/.claude/skills/sessions/SKILL.md
cp "$SCRIPT_DIR/sessions.py" ~/.claude/scripts/

if ! python3 -c "import textual" 2>/dev/null; then
    echo "Installing textual..."
    if pip3 install --user textual --quiet 2>/dev/null; then
        echo "  ✓ textual installed (user)"
    elif pip3 install textual --break-system-packages --quiet 2>/dev/null; then
        echo "  ✓ textual installed (system)"
    elif command -v pipx &>/dev/null; then
        pipx install textual --quiet 2>/dev/null || true
        echo "  ✓ textual via pipx"
    else
        echo "  ⚠ Не удалось установить textual автоматически."
        echo "  Запустите вручную: pip3 install textual --break-system-packages"
    fi
fi

echo ""
echo "✓ Установлено:"
echo "  ~/.claude/skills/sessions/SKILL.md"
echo "  ~/.claude/scripts/sessions.py"
echo ""
echo "Скажи Claude: 'покажи мои сессии' или 'sessions'"
