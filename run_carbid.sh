#!/bin/bash
# =====================================================================
# Carbid 자동 수집 래퍼 — launchd(com.bryan.carbid.daily)에서 호출.
#  · 하루 1회만 실제 실행(중복 방지).
#  · RunAtLoad로 부팅·로그인 시에도 뜨지만 '오늘 이미 실행'이면 즉시 건너뜀
#    → 컴퓨터를 늦게 켜도 그날 첫 기회에 1회 수집됨(캐치업).
#  · 진행물건 변동 또는 새 낙찰이 있을 때만 Mac 알림.
#  강제 실행(테스트): bash run_carbid.sh force
# =====================================================================
export LANG="ko_KR.UTF-8"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

REPO="$HOME/Claude/Projects/Carbid"
LOG="$REPO/carbid.log"
STAMP="$REPO/data/.last_run_date"
cd "$REPO" || { echo "repo not found"; exit 1; }
mkdir -p "$REPO/data"
TODAY=$(date '+%Y-%m-%d')

# --- 하루 1회 가드 ('force' 인자로 우회) ---
if [ "$1" != "force" ] && [ "$(cat "$STAMP" 2>/dev/null)" = "$TODAY" ]; then
  echo "$(date '+%F %T') 오늘 이미 실행됨 — 건너뜀" >> "$LOG"
  exit 0
fi

echo "==== $(date '+%F %T') 수집 시작 ====" >> "$LOG"
git pull --rebase origin main >> "$LOG" 2>&1 || true

OUT=$(CARBID_ROOT="$REPO" python3 carbid_auction.py run 2>>"$LOG"); RC=$?
echo "$OUT" >> "$LOG"

# 실패 시: stamp 미기록 → 다음 부팅/로그인/스케줄 때 재시도
if [ $RC -ne 0 ]; then
  echo "python 실패(rc=$RC) — 재시도 예정" >> "$LOG"
  osascript -e 'display notification "수집 실패 — 다음 실행 때 재시도합니다" with title "Carbid 법원경매 수집"' 2>>"$LOG" || true
  exit 1
fi
echo "$TODAY" > "$STAMP"   # 성공 표시(오늘 1회 완료)

SUMMARY=$(echo "$OUT" | grep '^\[OK\] 수집' | head -1 | sed 's/^\[OK\] //')
[ -z "$SUMMARY" ] && SUMMARY="수집 완료(요약 파싱 실패 — carbid.log 확인)"
if echo "$OUT" | grep -q '### CHANGES'; then CHANGED=1; else CHANGED=0; fi

# 커밋·푸시(변경분 있을 때만)
git add -A >> "$LOG" 2>&1
if ! git diff --cached --quiet; then
  git commit -m "자동 수집 $TODAY" >> "$LOG" 2>&1
  if git push origin main >> "$LOG" 2>&1; then PUSH="GitHub 갱신"; else PUSH="⚠️ 푸시 실패"; fi
else
  PUSH="변경 없음"
fi

# 알림: 진행물건 변동 또는 새 낙찰이 있을 때만
if [ "$CHANGED" = "1" ]; then
  osascript -e "display notification \"$SUMMARY · $PUSH\" with title \"Carbid 법원경매 수집\" sound name \"Glass\"" 2>>"$LOG" || true
fi
echo "==== 종료 ($PUSH) ====" >> "$LOG"
