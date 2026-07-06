#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Carbid v5 — 법원경매(courtauction.go.kr) 승용차 모니터링 시스템
================================================================
실제 API 구조 반영본 (2026-07 DevTools 캡처 기준).

검색 조건 (사용자 지정, 고정):
  * 법원: 전체(전국) / 용도: 자동차·중기(30000) > 차량(30100) > 승용차(30101)
  * 연식: 2020년 이상 / 진행·예정 물건 / 차명: bmw, 그랜저, a7

핵심 엔드포인트 (검증됨):
  POST /pgj/pgjsearch/searchControllerMain.on
    body: {dma_pageInfo{...}, dma_srchGdsDtlSrchInfo{...}}
    resp: data.dlt_srchResult[], data.dma_pageInfo.totalCnt

사용법:
  python3 carbid_auction.py run         # 수집→이력diff→대시보드
  python3 carbid_auction.py daily       # 매일 경량 체크(변경 마커 출력)
  python3 carbid_auction.py dashboard   # 기존 data/latest.json으로 대시보드만
  python3 carbid_auction.py probe        # 접속/건수만 빠르게 확인

환경변수 CARBID_ROOT 로 저장 루트 지정(기본: 현재 폴더).
  data/history.json  누적 이력   data/latest.json  최신 스냅샷
  docs/index.html    최신 대시보드(GitHub Pages용)
  carbid_dashboard_YYYY-MM-DD.html  날짜별 사본
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import time

import requests

BASE = "https://www.courtauction.go.kr"
SEARCH_URL = BASE + "/pgj/pgjsearch/searchControllerMain.on"
REFERER = BASE + "/pgj/index.on?w2xPath=/pgj/ui/pgj100/PGJ151F00.xml"

KEYWORDS = ["bmw", "그랜저", "a7"]
YEAR_MIN = 2020
PAGE_SIZE = 40
# 진행물건 검색조건 코드(캡처값). 매각결과(낙찰) 수집은 별도 코드 필요 — RESULTS 캡처 후 활성화.
SRCH_COND_CD = "0004603"

USG_LCL, USG_MCL, USG_SCL = "30000", "30100", "30101"   # 자동차·중기 > 차량 > 승용차
PGM_ID = "PGJ154M01"

FUEL = {"0001001": "가솔린", "0001002": "디젤", "0001003": "LPG",
        "0001004": "하이브리드", "0001005": "전기", "0001006": "수소"}

ROOT = os.environ.get("CARBID_ROOT") or os.getcwd()
DATA_DIR = os.path.join(ROOT, "data")
DOCS_DIR = os.path.join(ROOT, "docs")
KST = dt.timezone(dt.timedelta(hours=9))

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": BASE,
    "Referer": REFERER,
    "X-Requested-With": "XMLHttpRequest",
}


def today_kst():
    return dt.datetime.now(KST).date()


def new_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    # 메인 진입으로 WMONID/JSESSIONID 등 쿠키 확보
    s.get(BASE + "/pgj/index.on", timeout=30)
    return s


# ---------------------------------------------------------------------------
# 요청 페이로드 (실제 캡처 구조)
# ---------------------------------------------------------------------------
def build_payload(keyword, page_no):
    return {
        "dma_pageInfo": {
            "pageNo": page_no, "pageSize": PAGE_SIZE, "bfPageNo": "",
            "startRowNo": "", "totalCnt": "", "totalYn": "Y", "groupTotalCount": "",
        },
        "dma_srchGdsDtlSrchInfo": {
            "cortAuctnSrchCondCd": SRCH_COND_CD, "cortStDvs": 1,
            "rprsAdongSdCd": "", "rprsAdongSggCd": "", "rprsAdongEmdCd": "",
            "rdnmSdCd": "", "rdnmSggCd": "", "rdnmNo": "",
            "cortOfcCd": "", "jdbnCd": "",                      # 전체 법원
            "aeeEvlAmtMin": "", "aeeEvlAmtMax": "",
            "rletLwsDspslPrcMin": "", "rletLwsDspslPrcMax": "",
            "lclDspslGdsLstUsgCd": USG_LCL, "mclDspslGdsLstUsgCd": USG_MCL,
            "sclDspslGdsLstUsgCd": USG_SCL,
            "execrOfcDvsCd": "", "flbdNcntMin": "", "flbdNcntMax": "",
            "lafjOrderBy": "", "pgmId": PGM_ID, "cortAuctnMbrsId": "",
            "csNo": "", "statNum": 1, "gdsVendNm": "", "grbxTypCd": "",
            "carMdlNm": keyword,                                # ★ 차명 검색
            "carMdyrMin": YEAR_MIN, "carMdyrMax": "",           # 연식 하한
            "fuelKndCd": "", "dspslDxdyYmd": "", "sideDvsCd": "",
        },
    }


# ---------------------------------------------------------------------------
# 필드 파싱
# ---------------------------------------------------------------------------
def to_int(v):
    if v is None:
        return None
    try:
        n = int(re.sub(r"[^\d-]", "", str(v)) or "0")
        return n
    except ValueError:
        return None


def fmt_date(v):
    d = re.sub(r"[^\d]", "", str(v or ""))[:8]
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 else None


def fmt_hm(v):
    d = re.sub(r"[^\d]", "", str(v or ""))
    return f"{d[:2]}:{d[2:4]}" if len(d) >= 4 else None


def parse_buld(text):
    """buldList 자유텍스트에서 유용 정보 추출."""
    text = text or ""
    out = {}
    m = re.search(r"차대번호\s*[:：]\s*([A-Za-z0-9]+)", text)
    if m:
        out["vin"] = m.group(1)
    m = re.search(r"보관장소\s*[:：]\s*(.+)", text)
    if m:
        out["storage"] = m.group(1).strip()
    m = re.search(r"차종\s*[:：]\s*(.+)", text)
    if m:
        out["body"] = m.group(1).strip()
    m = re.search(r"(?:년\s*식|연\s*식|모델연도)\s*[:：]\s*(\d{4})", text)
    if m:
        out["year_txt"] = to_int(m.group(1))
    return out


def normalize(r):
    buld = parse_buld(r.get("buldList"))
    year = to_int(r.get("carYrtype")) or buld.get("year_txt")
    appraisal = to_int(r.get("gamevalAmt"))
    # 최저매각가/최저가율: 다가오는 회차 기준 notifyMinmaePrice1 / Rate1 사용
    min_price = to_int(r.get("notifyMinmaePrice1")) or to_int(r.get("minmaePrice"))
    rate = to_int(r.get("notifyMinmaePriceRate1"))
    yuchal = to_int(r.get("yuchalCnt")) or 0
    prev_bid = to_int(r.get("maeAmt")) or 0          # 직전 낙찰가(재매각 시 >0)
    remae = fmt_date(r.get("remaeordDay"))
    bigo = (r.get("mulBigo") or "").strip()

    flags = []
    if prev_bid > 0 or remae:
        flags.append("재매각")
    if "특별매각" in bigo or "특별 매각" in bigo:
        flags.append("특별조건")

    it = {
        "court": r.get("jiwonNm"),
        "court_cd": r.get("boCd"),
        "dept": r.get("jpDeptNm"),
        "tel": (r.get("tel") or "").split("(")[0].strip() or None,
        "case_no": r.get("srnSaNo"),
        "sa_no": r.get("saNo"),
        "item_no": str(r.get("maemulSer") or "1"),
        "name": r.get("carNm") or r.get("buldNm"),
        "maker": r.get("jejosaNm"),
        "year": year,
        "appraisal": appraisal,
        "min_price": min_price,
        "ratio": rate if rate is not None else (
            round(min_price / appraisal * 100, 1) if appraisal and min_price else None),
        "fail_count": yuchal,
        "prev_bid": prev_bid or None,
        "sale_date": fmt_date(r.get("maeGiil")),
        "sale_time": fmt_hm(r.get("maeHh1")),
        "sale_place": r.get("maePlace"),
        "fuel": FUEL.get(r.get("fuelKindcd"), None),
        "vin": buld.get("vin"),
        "storage": buld.get("storage"),
        "body_type": buld.get("body"),
        "region": r.get("printSt", "").replace("사용본거지 : ", "").strip() or None,
        "bigo": bigo or None,
        "flags": flags,
        "reg_cnt": to_int(r.get("gwansMulRegCnt")),   # 관심물건 등록수(경쟁 관심도 힌트)
    }
    it["key"] = f"{it['court_cd']}|{it['sa_no']}|{it['item_no']}"
    return it


def which_keyword(it):
    nm = (it.get("name") or "").lower()
    if "bmw" in nm:
        return "bmw"
    if "a7" in nm or "a 7" in nm:
        return "a7"
    if "그랜저" in nm or "grandeur" in nm:
        return "그랜저"
    return None


# ---------------------------------------------------------------------------
# 수집
# ---------------------------------------------------------------------------
def search_keyword(s, keyword, errors):
    items, total = [], None
    page = 1
    while True:
        r = s.post(SEARCH_URL, json=build_payload(keyword, page), timeout=40)
        r.raise_for_status()
        j = r.json()
        if str(j.get("status")) not in ("200", "success", "SUCCESS"):
            raise RuntimeError(f"status={j.get('status')} msg={j.get('message')}")
        data = j.get("data") or {}
        rows = data.get("dlt_srchResult") or []
        if total is None:
            total = to_int((data.get("dma_pageInfo") or {}).get("totalCnt")) or 0
        for row in rows:
            it = normalize(row)
            it["keyword"] = keyword
            if it["year"] and it["year"] < YEAR_MIN:   # 안전망(사이트가 이미 필터)
                continue
            items.append(it)
        if len(rows) < PAGE_SIZE or len(items) >= (total or 0) or page > 50:
            break
        page += 1
        time.sleep(0.6)
    return items, total


def fetch_all(s, errors):
    merged = {}
    totals = {}
    for kw in KEYWORDS:
        try:
            items, total = search_keyword(s, kw, errors)
            totals[kw] = total
            for it in items:
                merged.setdefault(it["key"], it)   # 사건 중복 제거(키워드 겹침 대비)
        except Exception as e:
            errors.append(f"kw={kw}: {str(e)[:200]}")
            totals[kw] = None
        time.sleep(0.6)
    return list(merged.values()), totals


# ---------------------------------------------------------------------------
# 이력 diff (유찰/가격인하/기일변경/신규/소멸)
# ---------------------------------------------------------------------------
SNAP = ["min_price", "fail_count", "sale_date"]


def load_history():
    p = os.path.join(DATA_DIR, "history.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"items": {}, "updated": None}


def save_history(h):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "history.json"), "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False, indent=1)


def apply_diff(hist, items, run_date):
    ch = {"new": [], "price_drop": [], "date_changed": [], "gone": []}
    seen = set()
    first_run = not hist["items"]
    for it in items:
        k = it["key"]
        seen.add(k)
        h = hist["items"].get(k)
        snap = {f: it.get(f) for f in SNAP}
        snap["date"] = run_date
        if h is None:
            hist["items"][k] = {"snapshots": [snap], "first_seen": run_date,
                                "last_seen": run_date, "active": True,
                                "name": it.get("name"), "court": it.get("court")}
            it["is_new"] = not first_run     # 최초 수집에선 NEW 배지 생략(노이즈 방지)
            if not first_run:
                ch["new"].append(k)
            continue
        it["is_new"] = False
        last = h["snapshots"][-1]
        if it.get("min_price") and last.get("min_price") and it["min_price"] < last["min_price"]:
            it["prev_min_price"] = last["min_price"]
            it["drop_pct"] = round((1 - it["min_price"] / last["min_price"]) * 100, 1)
            ch["price_drop"].append({"key": k, "old": last["min_price"],
                                     "new": it["min_price"], "pct": it["drop_pct"]})
        if it.get("sale_date") and last.get("sale_date") and it["sale_date"] != last["sale_date"]:
            ch["date_changed"].append({"key": k, "old": last["sale_date"], "new": it["sale_date"]})
        if any(snap.get(f) != last.get(f) for f in SNAP):
            h["snapshots"].append(snap)
        h["last_seen"] = run_date
        h["active"] = True
    for k, h in hist["items"].items():
        if h.get("active") and k not in seen:
            h["active"] = False
            h["gone_date"] = run_date
            ch["gone"].append(k)
    hist["updated"] = run_date
    return ch, first_run


def summarize(ch, items_by_key, hist):
    def nm(k):
        it = items_by_key.get(k) or hist["items"].get(k, {})
        return f"{it.get('name')}({it.get('court')})"
    out = []
    if ch["new"]:
        out.append(f"신규 {len(ch['new'])}건: " + ", ".join(nm(k) for k in ch["new"][:6]))
    if ch["price_drop"]:
        out.append("가격 인하 {}건: ".format(len(ch["price_drop"])) + ", ".join(
            f"{nm(d['key'])} {d['old']//10000:,}만→{d['new']//10000:,}만(-{d['pct']}%)"
            for d in ch["price_drop"][:6]))
    if ch["date_changed"]:
        out.append(f"기일 변경 {len(ch['date_changed'])}건")
    if ch["gone"]:
        out.append(f"목록 제외(매각/취하 추정) {len(ch['gone'])}건")
    return out


# ---------------------------------------------------------------------------
# 파이프라인
# ---------------------------------------------------------------------------
def run_pipeline(daily=False):
    os.makedirs(DATA_DIR, exist_ok=True)
    errors = []
    run_date = today_kst().isoformat()
    s = new_session()
    items, totals = fetch_all(s, errors)

    hist = load_history()
    ch, first_run = apply_diff(hist, items, run_date)
    save_history(hist)

    items_by_key = {i["key"]: i for i in items}
    lines = summarize(ch, items_by_key, hist)
    kw_counts = {kw: sum(1 for i in items if i.get("keyword") == kw) for kw in KEYWORDS}

    out = build_output(items, totals, kw_counts, ch, lines, first_run, run_date, errors)
    with open(os.path.join(DATA_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    dash = build_dashboard(out)

    has_change = any(ch[k] for k in ("new", "price_drop", "date_changed", "gone"))
    print(f"[OK] 수집 {len(items)}건 (bmw {kw_counts['bmw']} / 그랜저 {kw_counts['그랜저']} / a7 {kw_counts['a7']}) "
          f"· 신규 {len(ch['new'])} · 인하 {len(ch['price_drop'])}")
    for kw, t in totals.items():
        print(f"   - {kw}: totalCnt={t}")
    for ln in lines:
        print("   ·", ln)
    if errors:
        print("[WARN]", *errors[-4:], sep="\n   ")
    print(f"[OK] 대시보드 → {dash}")
    print("### CHANGES" if has_change else "### NO_CHANGES")
    return out


def build_output(items, totals, kw_counts, ch, lines, first_run, run_date, errors):
    return {
        "generated_at": dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST"),
        "run_date": run_date, "keywords": KEYWORDS, "year_min": YEAR_MIN,
        "count": len(items), "totals": totals, "kw_counts": kw_counts,
        "new_count": len(ch["new"]), "drop_count": len(ch["price_drop"]),
        "first_run": first_run, "summary_lines": lines,
        "items": sorted(items, key=lambda x: (x.get("sale_date") or "9999", x.get("court") or "")),
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# 대시보드
# ---------------------------------------------------------------------------
def build_dashboard(data):
    date_part = data.get("run_date") or today_kst().isoformat()
    payload = json.dumps(data, ensure_ascii=False)
    doc = TEMPLATE.replace("__DATA__", html.escape(payload, quote=False).replace("</", "<\\/"))
    dated = os.path.join(ROOT, f"carbid_dashboard_{date_part}.html")
    with open(dated, "w", encoding="utf-8") as f:
        f.write(doc)
    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(doc)
    return dated


def dashboard_only():
    with open(os.path.join(DATA_DIR, "latest.json"), encoding="utf-8") as f:
        return build_dashboard(json.load(f))


def probe():
    s = new_session()
    errors = []
    for kw in KEYWORDS:
        try:
            _, total = search_keyword(s, kw, errors)
            print(f"{kw}: totalCnt={total}")
        except Exception as e:
            print(f"{kw}: ERROR {str(e)[:160]}")
        time.sleep(0.6)


TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light only">
<title>Carbid — 법원경매 승용차 모니터</title>
<style>
 :root{color-scheme:light}
 .viz-root{--surface-1:#ffffff;--page:#f4f5f7;--ink-1:#0b0b0b;--ink-2:#52514e;--ink-3:#898781;
   --grid:#e6e6e2;--accent:#2a78d6;--good:#006300;--warn:#c98500;--drop:#d83b3a;--purple:#4a3aa7;
   --border:rgba(11,11,11,.12)}
 *{box-sizing:border-box;margin:0;padding:0}
 body{background:var(--page);color:var(--ink-1);
   font:14px/1.5 system-ui,-apple-system,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif}
 .viz-root{max-width:1280px;margin:0 auto;padding:26px 18px 60px}
 h1{font-size:20px;font-weight:700}
 .sub{color:var(--ink-2);margin-top:4px;font-size:13px}
 .note{margin-top:8px;font-size:12.5px;color:var(--warn)}
 .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:11px;margin:20px 0}
 .tile{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
 .tile .l{font-size:12px;color:var(--ink-2)}
 .tile .v{font-size:23px;font-weight:600;margin-top:2px}
 .tile .v .u{font-size:12px;font-weight:400;color:var(--ink-3);margin-left:2px}
 .tile.n .v{color:var(--accent)} .tile.d .v{color:var(--drop)}
 .chips{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 16px}
 .chip{border:1px solid var(--border);background:var(--surface-1);color:var(--ink-2);
   border-radius:999px;padding:5px 14px;font-size:13px;cursor:pointer;user-select:none}
 .chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
 .card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;overflow:auto}
 table{width:100%;border-collapse:collapse;min-width:1020px}
 th,td{padding:9px 11px;text-align:left;white-space:nowrap}
 thead th{font-size:12px;color:var(--ink-3);font-weight:600;border-bottom:1px solid var(--grid);
   position:sticky;top:0;background:var(--surface-1);cursor:pointer}
 thead th .a{font-size:10px;margin-left:3px}
 tbody td{border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums;vertical-align:top}
 tbody tr:hover td{background:color-mix(in srgb,var(--accent) 6%,transparent)}
 .car{font-weight:600;white-space:normal;min-width:230px}
 .muted{color:var(--ink-3);font-size:12px;font-weight:400}
 .b{display:inline-block;font-size:11px;font-weight:700;color:#fff;border-radius:5px;
   padding:1px 7px;margin-left:5px;vertical-align:1px;background:var(--accent)}
 .b.drop{background:var(--drop)} .b.re{background:var(--purple)} .b.sp{background:var(--warn);color:#241a00}
 .num{text-align:right}
 .dday{font-size:12px;color:var(--ink-2)} .dday.soon{color:var(--warn);font-weight:600}
 .rlow{color:var(--good);font-weight:600}
 .dl{color:var(--drop);font-size:12px}
 footer{margin-top:16px;color:var(--ink-3);font-size:12px}
 .empty{padding:36px;text-align:center;color:var(--ink-2)}
 a{color:var(--accent)}
</style></head>
<body><div class="viz-root">
 <h1>법원경매 승용차 모니터</h1>
 <div class="sub" id="sub"></div>
 <div class="note" id="note"></div>
 <div class="tiles" id="tiles"></div>
 <div class="chips" id="chips"></div>
 <div class="card">
  <table id="t"><thead><tr>
   <th data-k="name">차명 / 상세</th>
   <th data-k="year" class="num">연식</th>
   <th data-k="court">법원 / 계</th>
   <th data-k="case_no">사건번호</th>
   <th data-k="appraisal" class="num">감정가</th>
   <th data-k="min_price" class="num">최저매각가</th>
   <th data-k="ratio" class="num">최저가율</th>
   <th data-k="fail_count" class="num">유찰</th>
   <th data-k="sale_date">매각기일</th>
  </tr></thead><tbody id="tb"></tbody></table>
  <div class="empty" id="empty" style="display:none">조건에 맞는 물건이 없습니다.</div>
 </div>
 <footer>조건: 전국 법원 · 차량&gt;승용차 · 연식 <span id="ym"></span>년 이상 · 키워드 <span id="kw"></span> ·
  출처 <a href="https://www.courtauction.go.kr/pgj/index.on?w2xPath=/pgj/ui/pgj100/PGJ151F00.xml"
  target="_blank" rel="noopener">법원경매정보</a></footer>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const items=D.items||[]; let filt='전체',sortK='sale_date',asc=true;
document.getElementById('sub').textContent=
 `수집 ${D.generated_at} · 총 ${D.count}건`+(D.new_count?` · 신규 ${D.new_count}`:'')
 +(D.drop_count?` · 가격인하 ${D.drop_count}`:'');
document.getElementById('ym').textContent=D.year_min;
document.getElementById('kw').textContent=(D.keywords||[]).join(', ');
if(D.note) document.getElementById('note').textContent=D.note;
function won(v){if(v==null)return '—';
 if(v>=1e8){const e=Math.floor(v/1e8),m=Math.floor(v%1e8/1e4);return m?e+'억 '+m.toLocaleString()+'만':e+'억';}
 if(v>=1e4)return Math.floor(v/1e4).toLocaleString()+'만';return v.toLocaleString();}
function dday(s){if(!s)return '';const d=Math.ceil((new Date(s)-new Date().setHours(0,0,0,0))/864e5);
 if(isNaN(d))return '';if(d<0)return '지남';if(d===0)return 'D-DAY';return 'D-'+d;}
const kc=D.kw_counts||{};
let tiles=[{l:'진행 중',v:D.count,u:'건'},{l:'신규',v:D.new_count||0,u:'건',c:'n'},
 {l:'가격 인하',v:D.drop_count||0,u:'건',c:'d'}];
(D.keywords||[]).forEach(k=>tiles.push({l:'“'+k+'”',v:kc[k]||0,u:'건'}));
const cheap=items.filter(i=>i.min_price).sort((a,b)=>a.ratio-b.ratio)[0];
if(cheap)tiles.push({l:'최저가율 최소',v:(cheap.ratio||0)+'%',u:''});
document.getElementById('tiles').innerHTML=tiles.map(t=>
 `<div class="tile ${t.c||''}"><div class="l">${t.l}</div><div class="v">${t.v}<span class="u">${t.u||''}</span></div></div>`).join('');
const chips=['전체',...(D.keywords||[]),'신규만','가격인하','재매각'];
document.getElementById('chips').innerHTML=chips.map(c=>`<span class="chip${c===filt?' on':''}" data-c="${c}">${c}</span>`).join('');
document.getElementById('chips').onclick=e=>{const c=e.target.dataset.c;if(!c)return;filt=c;
 document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x.dataset.c===c));render();};
document.querySelectorAll('#t thead th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;
 if(sortK===k)asc=!asc;else{sortK=k;asc=true;}render();});
function sub(i){const b=[];
 if(i.year)b.push(i.year+'년');
 if(i.fuel)b.push(i.fuel);
 if(i.body_type)b.push(i.body_type);
 if(i.storage)b.push('📍'+i.storage);
 return b.length?`<div class="muted">${b.join(' · ')}</div>`:'';}
function badges(i){let s='';
 if(i.is_new)s+='<span class="b">NEW</span>';
 if(i.drop_pct)s+='<span class="b drop">↓'+i.drop_pct+'%</span>';
 (i.flags||[]).forEach(f=>{s+=f==='재매각'?'<span class="b re">재매각</span>':'<span class="b sp">'+f+'</span>';});
 return s;}
function render(){let rows=items;
 if(filt==='신규만')rows=rows.filter(i=>i.is_new);
 else if(filt==='가격인하')rows=rows.filter(i=>i.drop_pct);
 else if(filt==='재매각')rows=rows.filter(i=>(i.flags||[]).includes('재매각'));
 else if(filt!=='전체')rows=rows.filter(i=>i.keyword===filt);
 rows=[...rows].sort((a,b)=>{let x=a[sortK],y=b[sortK];
  if(x==null)return 1;if(y==null)return -1;
  if(typeof x==='string'){x=x.toLowerCase();y=(y||'').toLowerCase();}
  return (x<y?-1:x>y?1:0)*(asc?1:-1);});
 document.querySelectorAll('#t thead th').forEach(th=>{const a=th.querySelector('.a');if(a)a.remove();
  if(th.dataset.k===sortK){const s=document.createElement('span');s.className='a';s.textContent=asc?'▲':'▼';th.appendChild(s);}});
 document.getElementById('empty').style.display=rows.length?'none':'block';
 document.getElementById('tb').innerHTML=rows.map(i=>{const dd=dday(i.sale_date);
  const soon=dd&&dd!=='지남'&&(dd==='D-DAY'||parseInt(dd.slice(2))<=7);
  return `<tr>
   <td class="car">${i.name||'—'}${badges(i)}${sub(i)}</td>
   <td class="num">${i.year||'—'}</td>
   <td>${i.court||'—'}${i.dept?`<div class="muted">${i.dept}</div>`:''}</td>
   <td>${i.case_no||'—'}${i.tel?`<div class="muted">${i.tel}</div>`:''}</td>
   <td class="num">${won(i.appraisal)}</td>
   <td class="num">${won(i.min_price)}${i.prev_min_price?`<div class="dl">전 ${won(i.prev_min_price)}</div>`:''}</td>
   <td class="num">${i.ratio!=null?`<span class="${i.ratio<=60?'rlow':''}">${i.ratio}%</span>`:'—'}</td>
   <td class="num">${i.fail_count||0}회</td>
   <td>${i.sale_date||'—'}${i.sale_time?' '+i.sale_time:''} <span class="dday ${soon?'soon':''}">${dd}</span></td>
  </tr>`;}).join('');}
render();
</script></body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "daily", "dashboard", "probe"])
    a = ap.parse_args()
    if a.cmd == "dashboard":
        print(dashboard_only())
    elif a.cmd == "probe":
        probe()
    else:
        run_pipeline(daily=(a.cmd == "daily"))


if __name__ == "__main__":
    main()
