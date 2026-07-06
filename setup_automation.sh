#!/bin/bash
# =====================================================================
# Carbid 자동화 최초 세팅 — 딱 한 번만 실행하세요.
#   ① git 저장소 연결  ② 최초 수집  ③ 커밋·푸시  ④ launchd 등록
# =====================================================================
set -e
REPO="$HOME/Claude/Projects/Carbid"
cd "$REPO"
chmod +x run_carbid.sh

echo "① git 저장소 연결…"
if [ ! -d .git ]; then git init -b main; fi
git remote add origin https://github.com/BryanParkDev/carbid-auction.git 2>/dev/null \
  || git remote set-url origin https://github.com/BryanParkDev/carbid-auction.git
git pull origin main --allow-unrelated-histories --no-edit 2>/dev/null || true

echo "② 최초 수집 실행…"
CARBID_ROOT="$REPO" python3 carbid_auction.py run

echo "③ 커밋·푸시…  (GitHub 인증창이 뜨면 로그인/PAT 입력 — 이후 자동 실행은 keychain 재사용)"
git add -A
git commit -m "Carbid 자동화 초기 세팅" || true
git branch -M main
git push -u origin main

echo "④ launchd 등록(매주 월 08:05)…"
mkdir -p "$HOME/Library/LaunchAgents"
cp com.bryan.carbid.plist "$HOME/Library/LaunchAgents/"
launchctl unload "$HOME/Library/LaunchAgents/com.bryan.carbid.plist" 2>/dev/null || true
launchctl load  "$HOME/Library/LaunchAgents/com.bryan.carbid.plist"

echo ""
echo "✅ 완료! 매주 월요일 08:05 자동 수집됩니다."
echo "   지금 바로 한 번 테스트: bash run_carbid.sh"
echo "   대시보드: docs/index.html  (GitHub Pages 켜면 폰에서도 확인)"
