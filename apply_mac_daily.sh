#!/bin/bash
# =====================================================================
# 매일 자동 실행으로 전환 — "클라우드 불가" 판정 시에만 실행하세요.
#   기존 주간 잡(com.bryan.carbid)을 내리고, 매일 잡(com.bryan.carbid.daily) 등록.
#   매일 08:05에 run_carbid.sh 실행(진행물건 변동감지 + 낙찰 DB 누적, 변동 시에만 알림).
# =====================================================================
set -e
REPO="$HOME/Claude/Projects/Carbid"
LA="$HOME/Library/LaunchAgents"
cd "$REPO"
chmod +x run_carbid.sh
mkdir -p "$LA"

# 1) 기존 주간 잡 내리기(있으면)
launchctl unload "$LA/com.bryan.carbid.plist" 2>/dev/null || true
rm -f "$LA/com.bryan.carbid.plist"

# 2) 매일 잡 등록
cp com.bryan.carbid.daily.plist "$LA/"
launchctl unload "$LA/com.bryan.carbid.daily.plist" 2>/dev/null || true
launchctl load  "$LA/com.bryan.carbid.daily.plist"

echo "✅ 매일 08:05 자동 실행으로 전환 완료 (주간 잡 제거됨)."
echo "   확인: launchctl list | grep carbid   → com.bryan.carbid.daily 가 보이면 정상"
echo "   즉시 테스트: bash run_carbid.sh"
