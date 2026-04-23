#!/bin/bash
# cc-toolkit installer
set -e

TOOLKIT_DIR="$HOME/.cc-toolkit"
PYTHON="${PYTHON:-python3}"

echo "cc-toolkit 설치 중..."

# 디렉토리 생성
mkdir -p "$TOOLKIT_DIR"/{bin,hooks,cache}

# 파일 복사
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SCRIPT_DIR"/bin/*.py "$TOOLKIT_DIR/bin/"
cp "$SCRIPT_DIR"/bin/cc-link "$SCRIPT_DIR"/bin/cc-resume "$SCRIPT_DIR"/bin/cc-restore "$TOOLKIT_DIR/bin/"
cp "$SCRIPT_DIR"/hooks/workspace_tracker.py "$TOOLKIT_DIR/hooks/"
chmod +x "$TOOLKIT_DIR/bin/cc-link" "$TOOLKIT_DIR/bin/cc-resume" "$TOOLKIT_DIR/bin/cc-restore"

# PATH 등록 확인
if ! echo "$PATH" | grep -q "$TOOLKIT_DIR/bin"; then
    SHELL_RC="$HOME/.zshrc"
    [ -f "$HOME/.bashrc" ] && SHELL_RC="$HOME/.bashrc"
    echo '' >> "$SHELL_RC"
    echo '# cc-toolkit' >> "$SHELL_RC"
    echo "export PATH=\"\$HOME/.cc-toolkit/bin:\$PATH\"" >> "$SHELL_RC"
    echo "PATH 등록 완료: $SHELL_RC (새 터미널에서 적용)"
fi

# shim 스크립트의 python 경로 업데이트
PYTHON_PATH=$(which "$PYTHON")
for shim in cc-link cc-resume cc-restore; do
    sed -i.bak "s|exec .*/python[0-9.]* |exec $PYTHON_PATH |" "$TOOLKIT_DIR/bin/$shim" 2>/dev/null || true
    rm -f "$TOOLKIT_DIR/bin/$shim.bak"
done

echo ""
echo "✅ 설치 완료!"
echo ""
echo "다음 단계:"
echo "  1. Claude Code 전역 settings.json에 Hook 등록:"
echo '     "hooks": {'
echo '       "SessionStart": [{"hooks": [{"type": "command",'
echo '         "command": "CC_HOOK_EVENT=start '$PYTHON_PATH' $HOME/.cc-toolkit/hooks/workspace_tracker.py",'
echo '         "async": true}]}],'
echo '       "Stop": [{"hooks": [{"type": "command",'
echo '         "command": "CC_HOOK_EVENT=stop '$PYTHON_PATH' $HOME/.cc-toolkit/hooks/workspace_tracker.py",'
echo '         "async": true}]}]'
echo '     }'
echo ""
echo "  2. (선택) Dropbox 설치 후 프로젝트 등록:"
echo "     cd ~/Code/myproject && cc-link"
echo ""
echo "  3. cmux에서 사용 시작:"
echo "     cc-restore    # 세션 복원"
echo "     cc-resume     # 히스토리 + resume"
