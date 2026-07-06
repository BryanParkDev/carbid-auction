#!/bin/bash
# =====================================================================
# Carbid 주간 자동 수집 래퍼 — launchd(com.bryan.carbid)에서 호출됨
#  1) 원격 이력 동기화  2) 수집 실행  3) 변경 시 커밋·푸시  4) Mac 알림
# =====================================================================
export LANG="ko_KR.UTF-8"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

REPO="$HOME/Claude/Projects/Carbid"
LOG="$REPO/carbid.log"
cd "$REPO" || { echo "repo not found"; exit 1; }

echo "==== $(date '+%Y-%m-%d %H:%M:%S') 수집 시작 ====" >> "$LOG"

# 원격 최신 이력 반영(실패해도 진행)
git pull --rebase origin main >> "$LOG" 2>&1 || true

# 수집 실행 — 출력을 로그와 변수에 함께 저장
OUT=$(CARBID_ROOT="$REPO" python3 carbid_auction.py run 2>>"$LOG")
echo "$OUT" >> "$LOG"

# 요약/변경 마커 추출
SUMMARY=$(echo "$OUT" | grep '^\[OK\] 수집' | head -1 | sed 's/^\[OK\] //')
[ -z "$SUMMARY" ] && SUMMARY="수집 완료(요약 파싱 실패 — carbid.log 확인)"
if echo "$OUT" | grep -q '### CHANGES'; then CHANGED=1; else CHANGED=0; fi

# 변경분 커밋·푸시(스테이지에 변화가 있을 때만)
git add -A >> "$LOG" 2>&1
if ! git diff --cached --quiet; then
  git commit -m "주간 수집 $(date '+%Y-%m-%d')" >> "$LOG" 2>&1
  if git push origin main >> "$LOG" 2>&1; then PUSH="GitHub 갱신"; else PUSH="⚠️ 푸시 실패"; fi
else
  PUSH="변경 없음"
fi

# Mac 알림
if [ "$CHANGED" = "1" ]; then MSG="$SUMMARY"; else MSG="변동 없음 · $SUMMARY"; fi
osascript -e "display notification \"$MSG · $PUSH\" with title \"Carbid 법원경매 수집\" sound name \"Glass\"" 2>>"$LOG" || true

echo "==== 종료 ($PUSH) ====" >> "$LOG"
