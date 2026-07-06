# Carbid — 법원경매 승용차 모니터

대한민국 법원경매(courtauction.go.kr)에서 승용차 물건을 주기적으로 수집·추적하는 개인용 시스템.

## 검색 조건
- 전국 법원 / 용도: 차량 > 승용차 / 연식 2020년 이상 / 진행·예정 물건
- 차명 키워드: **bmw, 그랜저, a7**

## 구성
| 파일 | 역할 |
|------|------|
| `carbid_auction.py` | 수집기 + 대시보드 생성기 (`run` / `daily` / `probe` / `dashboard`) |
| `run_carbid.sh` | 주간 자동 실행 래퍼 (수집→커밋·푸시→Mac 알림) |
| `com.bryan.carbid.plist` | launchd 스케줄 (매주 월 08:05) |
| `setup_automation.sh` | 최초 세팅(한 번) |
| `data/history.json` | 누적 이력(유찰·가격인하·기일 추적, 낙찰 DB) |
| `docs/index.html` | 최신 대시보드 (GitHub Pages 배포용) |

## 대시보드
- 로컬: `docs/index.html`
- 웹(Pages 활성 시): https://bryanparkdev.github.io/carbid-auction/

## 수동 실행
```bash
python3 carbid_auction.py probe   # 접속·건수 확인
python3 carbid_auction.py run     # 수집 + 대시보드
bash run_carbid.sh                # 자동화와 동일 흐름(커밋·푸시·알림 포함)
```
