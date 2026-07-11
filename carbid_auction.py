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
import base64
import datetime as dt
import html
import json
import os
import re
import shutil
import time

import requests

BASE = "https://www.courtauction.go.kr"
SEARCH_URL = BASE + "/pgj/pgjsearch/searchControllerMain.on"
RESULTS_URL = BASE + "/pgj/pgjsearch/selectDspslSchdRsltSrch.on"   # 매각결과(낙찰)
DETAIL_URL = BASE + "/pgj/pgj15B/selectAuctnCsSrchRslt.on"        # 물건 상세(직접 링크)
REFERER = BASE + "/pgj/index.on?w2xPath=/pgj/ui/pgj100/PGJ151F00.xml"
DETAIL_REFERER = BASE + "/pgj/index.on?w2xPath=/pgj/ui/pgj100/PGJ154M00.xml"

# 관심 차명 키워드는 keywords.txt에서 로드됨(아래 load_keywords). 파일 없으면 기본값.
YEAR_MIN = 2020
PAGE_SIZE = 40
# 각 물건을 상세 링크(POST selectAuctnCsSrchRslt.on)로 직접 열어 주행거리·차량번호·
# 보증금율·회차별 가격이력 등 목록엔 없는 정보를 보강. 0으로 끄면 목록만 수집(빠름).
ENRICH_DETAIL = True
DETAIL_DELAY = 0.4          # 상세 요청 간 대기(초) — 서버 예의
# 진행물건 검색조건 코드(캡처값). 매각결과(낙찰) 수집은 별도 코드 필요 — RESULTS 캡처 후 활성화.
SRCH_COND_CD = "0004603"

USG_LCL, USG_MCL, USG_SCL = "30000", "30100", "30101"   # 자동차·중기 > 차량 > 승용차
PGM_ID = "PGJ154M01"

# 사건 링크 --------------------------------------------------------------------
# 자동차 검색 페이지(항상 열리는 안전 링크). 사건별 직접 상세 링크는 상세 클릭
# 법원 사이트는 SPA라 특정 매물 딥링크가 불가 → 우리가 수집한 상세 데이터로
# 로컬 상세 페이지(carbid_detail.html)를 만들고 사건번호 링크가 그리로 연결됨.
CAR_SEARCH_PAGE = BASE + "/pgj/index.on?w2xPath=/pgj/ui/pgj100/PGJ154M00.xml"
DETAIL_PAGE = "carbid_detail.html"      # 대시보드와 같은 폴더에 생성(상대링크)


def item_anchor(it):
    """URL 해시로 쓸 안전한 키(| → _)."""
    return (it.get("key") or "").replace("|", "_")


def build_link(it):
    a = item_anchor(it)
    return f"{DETAIL_PAGE}#{a}" if a else DETAIL_PAGE

FUEL = {"0001001": "가솔린", "0001002": "디젤", "0001003": "LPG",
        "0001004": "하이브리드", "0001005": "전기", "0001006": "수소"}

ROOT = os.environ.get("CARBID_ROOT") or os.getcwd()
DATA_DIR = os.path.join(ROOT, "data")
DOCS_DIR = os.path.join(ROOT, "docs")
KST = dt.timezone(dt.timedelta(hours=9))

# 사진: 상세 응답의 base64를 디코드해 로컬 파일로 저장.
#   docs/photos → GitHub Pages 배포용(커밋됨),  photos → 로컬 대시보드 보기용(.gitignore)
#   ★ 진행·예정 매물만 저장하고, 종료(낙찰/취하)로 목록에서 빠지면 폴더째 삭제.
SAVE_PHOTOS = True
PHOTO_SUBDIR = "photos"
PHOTO_DIRS = [os.path.join(DOCS_DIR, PHOTO_SUBDIR), os.path.join(ROOT, PHOTO_SUBDIR)]

DEFAULT_KEYWORDS = ["bmw", "그랜저", "a7", "제네시스"]


def load_keywords():
    """관심 차명 키워드를 keywords.txt에서 로드(한 줄에 하나, # 주석 가능).
    ★ 키워드는 계속 추가됨 — 코드 대신 이 파일만 고치면 전부 자동 반영."""
    p = os.path.join(ROOT, "keywords.txt")
    try:
        with open(p, encoding="utf-8") as f:
            kws = [ln.strip() for ln in f
                   if ln.strip() and not ln.strip().startswith("#")]
        if kws:
            return kws
    except FileNotFoundError:
        pass
    return list(DEFAULT_KEYWORDS)


KEYWORDS = load_keywords()

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
    it["docid"] = r.get("docid")
    it["key"] = f"{it['court_cd']}|{it['sa_no']}|{it['item_no']}"
    it["link"] = build_link(it)
    return it


# ---------------------------------------------------------------------------
# 물건 상세 — 직접 링크(POST) 로 열어 목록에 없는 정보 보강
#   csNo=saNo, cortOfcCd=boCd, dspslGdsSeq=maemulSer (목록 응답에서 그대로 매핑)
# ---------------------------------------------------------------------------
def build_detail_payload(cs_no, court_cd, seq):
    return {
        "dma_srchGdsDtlSrch": {
            "csNo": cs_no, "cortOfcCd": court_cd, "dspslGdsSeq": str(seq or "1"),
            "pgmId": PGM_ID,
            "srchInfo": {
                "cortAuctnSrchCondCd": SRCH_COND_CD, "cortStDvs": 1,
                "lclDspslGdsLstUsgCd": USG_LCL, "sideDvsCd": "2",
                "menuNm": "자동차ㆍ중기검색", "pgmId": PGM_ID, "statNum": 1,
            },
        }
    }


# 회차별 매각기일 결과코드 → 사람이 읽는 라벨
DXDY_RSLT = {"001": "진행", "002": "유찰", "003": "낙찰", "004": "매각허가",
             "005": "매각불허", "006": "변경", "007": "취하", "008": "정지",
             "009": "배당종결", "010": "기각"}


def parse_detail(res):
    """dma_result → 목록에 없는 보강 필드 dict."""
    out = {}
    gi = res.get("dspslGdsDxdyInfo") or {}
    if gi:
        dp = to_int(gi.get("prchDposRate"))
        if dp is not None:
            out["deposit_rate"] = dp          # 입찰보증금율(%) — 보통 10, 재매각 20~30
        out["min_price"] = to_int(gi.get("fstPbancLwsDspslPrc")) or out.get("min_price")
        out["appraisal"] = to_int(gi.get("aeeEvlAmt")) or out.get("appraisal")
        out["fail_count"] = to_int(gi.get("flbdNcnt"))
        out["sale_date"] = fmt_date(gi.get("dspslDxdyYmd"))
        out["sale_time"] = fmt_hm(gi.get("fstDspslHm"))
        out["sale_place"] = (gi.get("dspslPlcNm") or "").strip() or None
        if gi.get("tprtyRnkHypthcStngDts"):    # 말소기준권리 등 요약
            out["rights"] = gi["tprtyRnkHypthcStngDts"].strip()

    objs = res.get("gdsDspslObjctLst") or []
    if objs:
        o = objs[0]
        out["mileage"] = to_int(o.get("drvnDistIndctCtt"))     # 주행거리(km)
        out["plate"] = (o.get("objctRegNo") or "").strip() or None   # 차량번호
        out["vin"] = (o.get("carVidCtt") or out.get("vin") or "").strip() or None
        out["engine"] = (o.get("motrFmtDts") or "").strip() or None
        # bldDtlDts 여러 줄 중 '보관장소' 값만 추출(없으면 기존값 유지)
        bld = o.get("bldDtlDts") or ""
        ms = re.search(r"보관장소\s*[:：]\s*(.+?)(?:\n|$)", bld)
        stor = ms.group(1).strip() if ms else None
        # 연락처가 다음 줄에 이어지는 경우 붙이기
        if ms:
            tail = bld[ms.end():].strip()
            mt = re.match(r"\(?\s*연락처[:：]?\s*[\d\-]+\)?", tail)
            if mt:
                stor = (stor + " " + tail[:mt.end()]).strip()
        out["storage"] = stor or out.get("storage")
        yr = to_int(o.get("carDelvYr"))
        if yr:
            out["year"] = yr

    # 회차별 진행 이력(날짜·최저가·결과) — 사이트가 제공하는 공식 가격 이력
    hist = []
    for d in (res.get("gdsDspslDxdyLst") or []):
        row = {
            "date": fmt_date(d.get("dxdyYmd")),
            "price": to_int(d.get("tsLwsDspslPrc")),
            "result": DXDY_RSLT.get(str(d.get("auctnDxdyRsltCd") or ""),
                                    d.get("auctnDxdyRsltCd")),
            "kind": d.get("auctnDxdyKndCd"),
        }
        if row["date"] or row["price"]:
            hist.append(row)
    hist.sort(key=lambda r: r.get("date") or "")
    if hist:
        out["schedule"] = hist

    # 감정평가 요항(색상·관리상태·검사유효기간·특이사항 등) — 목록엔 전혀 없는 정보
    AEE_ITM = {"00083021": "year_km", "00083022": "color", "00083023": "condition",
               "00083024": "fuel_txt", "00083025": "inspection", "00083026": "options",
               "00083028": "notes"}
    aee = {}
    for m in (res.get("aeeWevlMnpntLst") or []):
        key = AEE_ITM.get(str(m.get("aeeWevlMnpntItmCd") or ""))
        # API가 이중 인코딩(&amp;apos;)하는 경우가 있어 두 번 해제
        txt = html.unescape(html.unescape((m.get("aeeWevlMnpntCtt") or "").strip()))
        if not txt:
            continue
        if key:
            aee[key] = txt if key not in aee else aee[key] + " " + txt
        else:
            aee.setdefault("etc", []).append(txt)
    if aee:
        # 한 줄 요약(색상·관리상태) + 특이사항 별도 보관
        color = (aee.get("color") or "").rstrip("임.").strip()
        cond = re.sub(r"^전반적인 관리상태는\s*", "", aee.get("condition") or "").rstrip("임.").strip()
        summ = []
        if color:
            summ.append("색상 " + color)
        if cond:
            summ.append("상태 " + cond)
        if summ:
            out["appraiser_summary"] = " · ".join(summ)
        if aee.get("inspection"):
            mi = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", aee["inspection"])
            if mi:
                out["inspection_until"] = mi.group(2)
        if aee.get("notes"):
            out["appraiser_notes"] = re.sub(r"\s*\n\s*", " ", aee["notes"]).strip()
        # 감정평가 주행거리(계기판) — 목록/제원과 교차검증용
        if aee.get("year_km"):
            mk = re.search(r"([\d,]+)\s*km", aee["year_km"])
            if mk and out.get("mileage") is None:
                out["mileage"] = to_int(mk.group(1))

    cs = res.get("csBaseInfo") or {}
    if cs:
        out["case_name"] = (cs.get("csNm") or "").strip() or None      # 예: 자동차임의경매
        out["claim_amt"] = to_int(cs.get("clmAmt"))                     # 청구금액
        out["receipt_date"] = fmt_date(cs.get("csRcptYmd"))
    return out


def _decode_b64(s):
    s = (s or "").strip()
    if not s:
        return None
    if "base64" in s[:40] and "," in s[:40]:      # data:image/...;base64, 접두 제거
        s = s.split(",", 1)[1]
    try:
        return base64.b64decode(s)
    except Exception:
        return None


def _pic_seq(p):
    try:
        return int(re.sub(r"[^\d]", "", str(p.get("pageSeq") or p.get("cortAuctnPicSeq") or 0)) or 0)
    except Exception:
        return 0


def save_photos(res, anchor):
    """csPicLst의 base64 사진을 <photodir>/<anchor>/NN.jpg 로 저장. 상대경로 리스트 반환.
    기존 폴더는 지우고 새로 써서 오래된 사진이 남지 않게 함."""
    if not (SAVE_PHOTOS and anchor):
        return None
    pics = sorted(res.get("csPicLst") or [], key=_pic_seq)
    for base in PHOTO_DIRS:                        # 이전 사진 제거(중복/변경 대비)
        d = os.path.join(base, anchor)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    rels, idx = [], 0
    for p in pics:
        raw = _decode_b64(p.get("picFile"))
        if not raw:
            continue
        idx += 1
        fname = f"{idx:02d}.jpg"
        for base in PHOTO_DIRS:
            d = os.path.join(base, anchor)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, fname), "wb") as f:
                f.write(raw)
        rels.append(f"{PHOTO_SUBDIR}/{anchor}/{fname}")
    return rels or None


def prune_photos(active_anchors):
    """현재 진행·예정 목록에 없는(=종료된) 매물의 사진 폴더를 삭제.
    git add -A 로 커밋되어 배포본(docs/photos)에서도 제거됨."""
    removed = 0
    for base in PHOTO_DIRS:
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            d = os.path.join(base, name)
            if os.path.isdir(d) and name not in active_anchors:
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    return removed


def fetch_detail(s, it, errors):
    """단일 물건 상세를 직접 링크(POST)로 조회 → parse_detail 결과. 실패 시 None.
    일시적 네트워크 오류는 지수backoff로 재시도."""
    payload = build_detail_payload(it.get("sa_no"), it.get("court_cd"), it.get("item_no"))
    last = None
    for attempt in range(3):
        try:
            r = s.post(DETAIL_URL, json=payload, timeout=40,
                       headers={"Referer": DETAIL_REFERER})
            r.raise_for_status()
            j = r.json()
            if str(j.get("status")) not in ("200", "success", "SUCCESS"):
                raise RuntimeError(f"status={j.get('status')} msg={j.get('message')}")
            res = ((j.get("data") or {}).get("dma_result")) or {}
            parsed = parse_detail(res)
            if SAVE_PHOTOS:                         # 사진 저장(진행·예정 매물)
                photos = save_photos(res, item_anchor(it))
                if photos:
                    parsed["photos"] = photos
                    parsed["photo_count"] = len(photos)
            return parsed
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    errors.append(f"detail {it.get('case_no')}: {str(last)[:120]}")
    return None


def enrich_items(s, items, errors):
    """목록 각 물건을 상세 링크로 열어 보강 필드 병합. 목록값은 상세값으로 갱신."""
    ok = 0
    for it in items:
        d = fetch_detail(s, it, errors)
        if not d:
            time.sleep(DETAIL_DELAY)
            continue
        for k, v in d.items():
            if v not in (None, "", []):
                it[k] = v           # 상세가 더 정확 — 최저가/유찰/기일 등도 갱신
        # 최저가율 재계산(상세 기준)
        if it.get("appraisal") and it.get("min_price"):
            it["ratio"] = round(it["min_price"] / it["appraisal"] * 100, 1)
        it["detailed"] = True
        ok += 1
        time.sleep(DETAIL_DELAY)
    return ok


def which_keyword(it):
    """차명에 어떤 관심 키워드가 포함되는지 판정(keywords.txt 기반 부분일치)."""
    nm = (it.get("name") or "").lower()
    for kw in KEYWORDS:
        if kw.lower() in nm:
            return kw
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
# 매각결과(낙찰 DB) — 차량 전체를 받아 코드에서 키워드+연식+낙찰만 필터
# ---------------------------------------------------------------------------
def build_results_payload(page_no):
    return {
        "dma_pageInfo": {
            "pageNo": page_no, "pageSize": PAGE_SIZE, "bfPageNo": "",
            "startRowNo": "", "totalCnt": "", "totalYn": "Y", "groupTotalCount": "",
        },
        "dma_srchGdsDtlSrchInfo": {
            "statNum": "3", "pgmId": "PGJ158M01", "cortStDvs": "1",
            "cortOfcCd": "", "jdbnCd": "", "csNo": "",
            "rprsAdongSdCd": "", "rprsAdongSggCd": "", "rprsAdongEmdCd": "",
            "rdnmSdCd": "", "rdnmSggCd": "", "rdnmNo": "",
            "auctnGdsStatCd": "",
            "lclDspslGdsLstUsgCd": USG_LCL, "mclDspslGdsLstUsgCd": USG_MCL,
            "sclDspslGdsLstUsgCd": USG_SCL,
            "dspslAmtMin": "", "dspslAmtMax": "", "aeeEvlAmtMin": "", "aeeEvlAmtMax": "",
            "flbdNcntMin": "", "flbdNcntMax": "", "lafjOrderBy": "",
        },
    }


def normalize_result(r):
    it = normalize(r)                      # 공통 필드 재사용
    hammer = to_int(r.get("maeAmt")) or 0  # 매각가(낙찰가). 0이면 유찰.
    it["hammer"] = hammer if hammer > 0 else None
    it["hammer_ratio"] = (round(hammer / it["appraisal"] * 100, 1)
                          if hammer > 0 and it["appraisal"] else None)
    it["sold"] = hammer > 0
    return it


def fetch_results(s, errors):
    """차량>승용 매각결과 전 페이지 → (bmw/그랜저/a7 · 2020↑ · 낙찰)만 반환."""
    out, total, page = [], None, 1
    while True:
        r = s.post(RESULTS_URL, json=build_results_payload(page), timeout=40)
        r.raise_for_status()
        j = r.json()
        if str(j.get("status")) not in ("200", "success", "SUCCESS"):
            raise RuntimeError(f"status={j.get('status')} msg={j.get('message')}")
        data = j.get("data") or {}
        rows = data.get("dlt_srchResult") or []
        if total is None:
            total = to_int((data.get("dma_pageInfo") or {}).get("totalCnt")) or 0
        for row in rows:
            it = normalize_result(row)
            kw = which_keyword(it)
            if not kw or not it["sold"]:
                continue
            if it["year"] and it["year"] < YEAR_MIN:
                continue
            it["keyword"] = kw
            out.append(it)
        if len(rows) < PAGE_SIZE or (total and page * PAGE_SIZE >= total) or page > 50:
            break
        page += 1
        time.sleep(0.6)
    return out, total


def merge_results(hist, results, run_date):
    known = {r["key"] for r in hist["results"]}
    added = 0
    for it in results:
        if it["key"] in known:
            continue
        hist["results"].append({
            "key": it["key"], "court": it["court"], "case_no": it["case_no"],
            "name": it["name"], "year": it["year"], "keyword": it["keyword"],
            "appraisal": it["appraisal"], "hammer": it["hammer"],
            "hammer_ratio": it["hammer_ratio"], "sale_date": it["sale_date"],
            "fail_count": it["fail_count"], "fuel": it.get("fuel"),
            "first_recorded": run_date,
        })
        known.add(it["key"])
        added += 1
    return added


def compute_stats(results):
    stats = {}
    for kw in KEYWORDS:
        rs = [r for r in results if r.get("keyword") == kw and r.get("hammer_ratio")]
        if rs:
            ratios = sorted(r["hammer_ratio"] for r in rs)
            stats[kw] = {"n": len(rs), "avg": round(sum(ratios) / len(ratios), 1),
                         "min": ratios[0], "max": ratios[-1]}
    return stats


# ---------------------------------------------------------------------------
# 이력 diff (유찰/가격인하/기일변경/신규/소멸)
# ---------------------------------------------------------------------------
SNAP = ["min_price", "fail_count", "sale_date"]


def load_history():
    p = os.path.join(DATA_DIR, "history.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"items": {}, "results": [], "stats": {}, "updated": None}


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

    # 직접 링크(상세 POST)로 각 물건 보강 — 주행거리·차량번호·보증금율·가격이력·사진
    enriched = 0
    if ENRICH_DETAIL and items:
        enriched = enrich_items(s, items, errors)

    # 종료(목록에서 빠진) 매물의 사진 폴더 삭제 — 진행·예정 매물 사진만 유지
    pruned = 0
    if SAVE_PHOTOS:
        pruned = prune_photos({item_anchor(it) for it in items})

    hist = load_history()
    hist.setdefault("results", [])
    hist.setdefault("stats", {})
    ch, first_run = apply_diff(hist, items, run_date)

    # 낙찰 DB(매각결과) — 실패해도 본 수집은 진행
    added_results, rtotal = 0, None
    try:
        results, rtotal = fetch_results(s, errors)
        added_results = merge_results(hist, results, run_date)
        hist["stats"] = compute_stats(hist["results"])
    except Exception as e:
        errors.append(f"results: {str(e)[:200]}")

    save_history(hist)

    items_by_key = {i["key"]: i for i in items}
    lines = summarize(ch, items_by_key, hist)
    kw_counts = {kw: sum(1 for i in items if i.get("keyword") == kw) for kw in KEYWORDS}

    out = build_output(items, totals, kw_counts, ch, lines, first_run, run_date, errors)
    out["results_total"] = len(hist["results"])
    out["results_added"] = added_results
    out["results_snapshot"] = rtotal
    out["stats"] = hist.get("stats", {})
    out["recent_results"] = sorted(
        [r for r in hist["results"] if r.get("hammer")],
        key=lambda r: r.get("sale_date") or "", reverse=True)[:20]
    with open(os.path.join(DATA_DIR, "latest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    build_detail_page(out)
    dash = build_dashboard(out)

    has_change = any(ch[k] for k in ("new", "price_drop", "date_changed", "gone")) or added_results > 0
    kwstr = " / ".join(f"{k} {kw_counts.get(k, 0)}" for k in KEYWORDS)
    photo_total = sum(i.get("photo_count") or 0 for i in items)
    print(f"[OK] 수집 {len(items)}건 ({kwstr}) · 상세보강 {enriched}건 "
          f"· 사진 {photo_total}장(정리 {pruned}건) "
          f"· 신규 {len(ch['new'])} · 인하 {len(ch['price_drop'])} "
          f"· 낙찰DB +{out['results_added']} (누적 {out['results_total']})")
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


def build_detail_page(data):
    """수집한 상세 데이터로 매물별 상세 화면(로컬)을 생성. 사건번호 링크의 목적지.
    carbid_detail.html 을 대시보드와 같은 폴더(ROOT, docs)에 저장 → 상대링크로 열림."""
    payload = json.dumps(data, ensure_ascii=False)
    doc = DETAIL_TEMPLATE.replace("__DATA__", html.escape(payload, quote=False).replace("</", "<\\/"))
    for d in (ROOT, DOCS_DIR):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, DETAIL_PAGE), "w", encoding="utf-8") as f:
            f.write(doc)
    return os.path.join(ROOT, DETAIL_PAGE)


def dashboard_only():
    with open(os.path.join(DATA_DIR, "latest.json"), encoding="utf-8") as f:
        data = json.load(f)
    build_detail_page(data)
    return build_dashboard(data)


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
 .ext{font-size:10px;color:var(--ink-3)}
 .rcard{margin-top:22px}
 .rcard h2{font-size:15px;font-weight:700;padding:14px 14px 2px}
 .rsub{font-size:12px;color:var(--ink-3);padding:0 14px 8px}
 .rstat{display:flex;flex-wrap:wrap;gap:14px;padding:2px 14px 14px;font-size:13px;color:var(--ink-2)}
 .rstat .k{background:#eef4fd;border:1px solid var(--border);border-radius:8px;padding:7px 12px}
 .rstat b{color:var(--ink-1);font-size:15px}
 .dl{color:var(--drop);font-size:12px}
 .chain{font-size:11px;color:var(--ink-3);font-weight:400;margin-top:2px;white-space:nowrap}
 .apr{white-space:normal;max-width:230px;line-height:1.35}
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

 <div class="card rcard" id="rcard" style="display:none">
  <h2>낙찰 DB — 최근 매각결과</h2>
  <div class="rsub" id="rsub"></div>
  <div class="rstat" id="rstat"></div>
  <table style="min-width:820px"><thead><tr>
   <th>차명</th><th class="num">연식</th><th>법원</th><th>사건번호</th>
   <th class="num">감정가</th><th class="num">낙찰가</th><th class="num">낙찰가율</th>
   <th class="num">유찰</th><th>매각일</th>
  </tr></thead><tbody id="rtb"></tbody></table>
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
function dday(s){if(!s)return '';const p=s.split('-').map(Number);if(p.length!==3)return '';
 const kst=new Date(Date.now()+9*3600*1000);            // 뷰어 시간대와 무관하게 KST 기준 오늘
 const t0=Date.UTC(kst.getUTCFullYear(),kst.getUTCMonth(),kst.getUTCDate());
 const d=Math.round((Date.UTC(p[0],p[1]-1,p[2])-t0)/864e5);
 if(isNaN(d))return '';if(d<0)return '지남';if(d===0)return 'D-DAY';return 'D-'+d;}
const kc=D.kw_counts||{};
let tiles=[{l:'진행 중',v:D.count,u:'건'},{l:'신규',v:D.new_count||0,u:'건',c:'n'},
 {l:'가격 인하',v:D.drop_count||0,u:'건',c:'d'}];
(D.keywords||[]).forEach(k=>tiles.push({l:'“'+k+'”',v:kc[k]||0,u:'건'}));
const cheap=items.filter(i=>i.min_price).sort((a,b)=>a.ratio-b.ratio)[0];
if(cheap)tiles.push({l:'최저가율 최소',v:(cheap.ratio||0)+'%',u:''});
if((D.results_total||0)>0)tiles.push({l:'낙찰 DB 누적',v:D.results_total,u:'건'});
document.getElementById('tiles').innerHTML=tiles.map(t=>
 `<div class="tile ${t.c||''}"><div class="l">${t.l}</div><div class="v">${t.v}<span class="u">${t.u||''}</span></div></div>`).join('');
const chips=['전체',...(D.keywords||[]),'신규만','가격인하','재매각'];
document.getElementById('chips').innerHTML=chips.map(c=>`<span class="chip${c===filt?' on':''}" data-c="${c}">${c}</span>`).join('');
document.getElementById('chips').onclick=e=>{const c=e.target.dataset.c;if(!c)return;filt=c;
 document.querySelectorAll('.chip').forEach(x=>x.classList.toggle('on',x.dataset.c===c));render();};
document.querySelectorAll('#t thead th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;
 if(sortK===k)asc=!asc;else{sortK=k;asc=true;}render();});
function km(v){return v==null?'':v>=1e4?(Math.round(v/1e3)/10)+'만km':v.toLocaleString()+'km';}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function sub(i){const b=[];
 if(i.year)b.push(i.year+'년');
 if(i.fuel)b.push(i.fuel);
 if(i.mileage!=null)b.push('🛣️'+km(i.mileage));
 if(i.plate)b.push('🚗'+i.plate);
 if(i.photo_count)b.push('📷'+i.photo_count);
 if(i.body_type)b.push(i.body_type);
 let s=b.length?`<div class="muted">${b.join(' · ')}</div>`:'';
 if(i.appraiser_summary)s+=`<div class="muted apr" title="${esc(i.appraiser_notes||'')}">🔎 ${esc(i.appraiser_summary)}</div>`;
 const loc=i.storage||i.region;
 if(loc)s+=`<div class="muted">📍${esc(loc.split('\n')[0].replace(/^보관장소\s*:\s*/,''))}</div>`;
 return s;}
function badges(i){let s='';
 if(i.is_new)s+='<span class="b">NEW</span>';
 if(i.drop_pct)s+='<span class="b drop">↓'+i.drop_pct+'%</span>';
 (i.flags||[]).forEach(f=>{s+=f==='재매각'?'<span class="b re">재매각</span>':'<span class="b sp">'+f+'</span>';});
 if(i.deposit_rate&&i.deposit_rate>10)s+='<span class="b sp">보증금'+i.deposit_rate+'%</span>';
 return s;}
// 회차별 최저가 이력(유찰마다 저감) — 사이트 공식 데이터
function priceChain(i){const h=(i.schedule||[]).filter(r=>r.price>0);
 if(h.length<2)return '';
 const seq=h.map(r=>won(r.price)).join(' → ');
 return `<div class="chain">${seq}</div>`;}
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
   <td>${i.link?`<a href="${i.link}" target="_blank" rel="noopener">${i.case_no||'—'}</a> <span class="ext">↗</span>`:(i.case_no||'—')}${i.tel?`<div class="muted">${i.tel}</div>`:''}</td>
   <td class="num">${won(i.appraisal)}</td>
   <td class="num">${won(i.min_price)}${i.prev_min_price?`<div class="dl">전 ${won(i.prev_min_price)}</div>`:''}${priceChain(i)}</td>
   <td class="num">${i.ratio!=null?`<span class="${i.ratio<=60?'rlow':''}">${i.ratio}%</span>`:'—'}</td>
   <td class="num">${i.fail_count||0}회</td>
   <td>${i.sale_date||'—'}${i.sale_time?' '+i.sale_time:''} <span class="dday ${soon?'soon':''}">${dd}</span></td>
  </tr>`;}).join('');}
render();

// 낙찰 DB
const rr=D.recent_results||[]; const st=D.stats||{};
if(rr.length||(D.results_total||0)>0){
 document.getElementById('rcard').style.display='';
 document.getElementById('rsub').textContent=
  `누적 ${D.results_total}건`+(D.results_added?` (이번 +${D.results_added})`:'')
  +` · 낙찰가율 = 낙찰가 / 감정가 · 매주 누적됩니다`;
 document.getElementById('rstat').innerHTML=Object.keys(st).length
  ? Object.keys(st).map(k=>`<span class="k">“${k}” 평균 <b>${st[k].avg}%</b> <span class="muted">(${st[k].n}건 · ${st[k].min}~${st[k].max}%)</span></span>`).join('')
  : '<span class="muted">낙찰가율 통계는 데이터가 쌓이면 표시됩니다.</span>';
 document.getElementById('rtb').innerHTML=rr.map(r=>`<tr>
   <td class="car">${r.name||'—'}${r.fuel?`<div class="muted">${r.fuel}</div>`:''}</td>
   <td class="num">${r.year||'—'}</td>
   <td>${r.court||'—'}</td>
   <td>${r.case_no||'—'}</td>
   <td class="num">${won(r.appraisal)}</td>
   <td class="num">${won(r.hammer)}</td>
   <td class="num"><b>${r.hammer_ratio!=null?r.hammer_ratio+'%':'—'}</b></td>
   <td class="num">${r.fail_count||0}회</td>
   <td>${r.sale_date||'—'}</td>
  </tr>`).join('');
}
</script></body></html>
"""


# ---------------------------------------------------------------------------
# 매물 상세 페이지(로컬) — 사건번호 링크 클릭 시 열리는 화면
# ---------------------------------------------------------------------------
DETAIL_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="light only">
<title>매물 상세 — 법원경매 승용차 모니터</title>
<style>
 :root{color-scheme:light}
 .viz-root{--surface-1:#ffffff;--page:#f4f5f7;--ink-1:#0b0b0b;--ink-2:#52514e;--ink-3:#898781;
   --grid:#e6e6e2;--accent:#2a78d6;--good:#006300;--warn:#c98500;--drop:#d83b3a;--purple:#4a3aa7;
   --border:rgba(11,11,11,.12)}
 *{box-sizing:border-box;margin:0;padding:0}
 body{background:var(--page);color:var(--ink-1);
   font:14px/1.6 system-ui,-apple-system,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif}
 .viz-root{max-width:920px;margin:0 auto;padding:22px 18px 60px}
 .top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}
 .back{font-size:13px;color:var(--accent);text-decoration:none}
 .back:hover{text-decoration:underline}
 h1{font-size:22px;font-weight:700;line-height:1.3}
 .case{color:var(--ink-2);font-size:14px;margin-top:3px}
 .b{display:inline-block;font-size:11px;font-weight:700;color:#fff;border-radius:5px;
   padding:2px 8px;margin-left:6px;vertical-align:2px;background:var(--accent)}
 .b.drop{background:var(--drop)} .b.re{background:var(--purple)} .b.sp{background:var(--warn);color:#241a00}
 .card{background:var(--surface-1);border:1px solid var(--border);border-radius:12px;
   padding:18px 20px;margin-top:16px}
 .card h2{font-size:14px;font-weight:700;color:var(--ink-2);margin-bottom:12px;
   text-transform:none;letter-spacing:0}
 .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px 18px}
 .f .k{font-size:12px;color:var(--ink-3)}
 .f .v{font-size:15px;font-weight:600;margin-top:1px;font-variant-numeric:tabular-nums}
 .price{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:14px 18px}
 .price .v{font-size:19px}
 .price .v.hl{color:var(--accent)} .price .v.low{color:var(--good)}
 table{width:100%;border-collapse:collapse;margin-top:2px}
 th,td{padding:8px 10px;text-align:left;font-size:13px}
 th{font-size:12px;color:var(--ink-3);font-weight:600;border-bottom:1px solid var(--grid)}
 td{border-bottom:1px solid var(--grid);font-variant-numeric:tabular-nums}
 tr.now td{background:color-mix(in srgb,var(--accent) 8%,transparent);font-weight:600}
 .rslt-f{color:var(--drop)} .rslt-s{color:var(--good);font-weight:700}
 .txt{font-size:13.5px;color:var(--ink-1);white-space:pre-wrap;line-height:1.65}
 .txt.note{color:var(--ink-2);font-size:13px}
 .muted{color:var(--ink-3)}
 .foot{margin-top:20px;font-size:12.5px;color:var(--ink-3)}
 .foot a{color:var(--accent)}
 .idx a{display:block;padding:9px 4px;border-bottom:1px solid var(--grid);color:var(--ink-1);text-decoration:none}
 .idx a:hover{color:var(--accent)}
 .idx .m{color:var(--ink-3);font-size:12px}
 .empty{padding:40px;text-align:center;color:var(--ink-2)}
 .gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
 .gal img{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:8px;border:1px solid var(--border);
   cursor:zoom-in;background:#f0efec;transition:opacity .15s}
 .gal img:hover{opacity:.88}
 .lb{position:fixed;inset:0;background:rgba(0,0,0,.92);display:none;z-index:50;
   align-items:center;justify-content:center}
 .lb.on{display:flex}
 .lb img{max-width:94vw;max-height:88vh;object-fit:contain;border-radius:4px}
 .lb .x{position:absolute;top:14px;right:20px;color:#fff;font-size:30px;cursor:pointer;
   line-height:1;background:none;border:none}
 .lb .nav{position:absolute;top:50%;transform:translateY(-50%);color:#fff;font-size:40px;
   cursor:pointer;background:none;border:none;padding:10px 18px;user-select:none;opacity:.8}
 .lb .nav:hover{opacity:1}
 .lb .prev{left:6px} .lb .next{right:6px}
 .lb .cnt{position:absolute;bottom:16px;left:0;right:0;text-align:center;color:#fff;font-size:13px;opacity:.85}
 @media(max-width:560px){.viz-root{padding:16px 12px 48px}h1{font-size:19px}
   .gal{grid-template-columns:repeat(auto-fill,minmax(104px,1fr))}}
</style></head>
<body><div class="viz-root">
 <div class="top">
  <a class="back" href="#" onclick="history.length>1?history.back():location.href='carbid_detail.html';return false;">← 목록으로</a>
  <a class="back" id="official" target="_blank" rel="noopener"
     href="https://www.courtauction.go.kr/pgj/index.on?w2xPath=/pgj/ui/pgj100/PGJ154M00.xml">법원 사이트에서 검색 ↗</a>
 </div>
 <div id="body"></div>
 <div class="foot" id="foot"></div>
</div>
<div class="lb" id="lb">
 <button class="x" onclick="lbClose()" aria-label="닫기">×</button>
 <button class="nav prev" onclick="lbStep(-1)" aria-label="이전">‹</button>
 <img id="lbimg" src="" alt="">
 <button class="nav next" onclick="lbStep(1)" aria-label="다음">›</button>
 <div class="cnt" id="lbcnt"></div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const items=D.items||[];
const anc=i=>(i.key||'').replace(/\|/g,'_');
let GAL=[], lbi=0;
function lbOpen(n){if(!GAL.length)return;lbi=n;document.getElementById('lbimg').src=GAL[lbi];
 document.getElementById('lbcnt').textContent=(lbi+1)+' / '+GAL.length;
 document.getElementById('lb').classList.add('on');}
function lbClose(){document.getElementById('lb').classList.remove('on');}
function lbStep(d){if(!GAL.length)return;lbi=(lbi+d+GAL.length)%GAL.length;lbOpen(lbi);}
document.getElementById('lb').addEventListener('click',e=>{if(e.target.id==='lb')lbClose();});
document.addEventListener('keydown',e=>{if(!document.getElementById('lb').classList.contains('on'))return;
 if(e.key==='Escape')lbClose();else if(e.key==='ArrowLeft')lbStep(-1);else if(e.key==='ArrowRight')lbStep(1);});
function won(v){if(v==null)return '—';
 if(v>=1e8){const e=Math.floor(v/1e8),m=Math.floor(v%1e8/1e4);return m?e+'억 '+m.toLocaleString()+'만':e+'억';}
 if(v>=1e4)return Math.floor(v/1e4).toLocaleString()+'만';return v.toLocaleString();}
function km(v){return v==null?'—':v>=1e4?(Math.round(v/1e3)/10)+'만km':v.toLocaleString()+'km';}
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function dday(s){if(!s)return '';const p=s.split('-').map(Number);if(p.length!==3)return '';
 const kst=new Date(Date.now()+9*3600*1000);            // 뷰어 시간대와 무관하게 KST 기준 오늘
 const t0=Date.UTC(kst.getUTCFullYear(),kst.getUTCMonth(),kst.getUTCDate());
 const d=Math.round((Date.UTC(p[0],p[1]-1,p[2])-t0)/864e5);
 if(isNaN(d))return '';if(d<0)return '(지남)';if(d===0)return '(D-DAY)';return '(D-'+d+')';}
function fld(k,v){return v==null||v===''?'':`<div class="f"><div class="k">${k}</div><div class="v">${v}</div></div>`;}
function badges(i){let s='';
 (i.flags||[]).forEach(f=>{s+=f==='재매각'?'<span class="b re">재매각</span>':'<span class="b sp">'+esc(f)+'</span>';});
 if(i.deposit_rate&&i.deposit_rate>10)s+='<span class="b sp">보증금'+i.deposit_rate+'%</span>';
 return s;}
function renderDetail(i){
 document.getElementById('official').href=
  'https://www.courtauction.go.kr/pgj/index.on?w2xPath=/pgj/ui/pgj100/PGJ154M00.xml';
 const spec=[fld('연식',i.year?i.year+'년':''),fld('연료',i.fuel),fld('주행거리',i.mileage!=null?km(i.mileage):''),
  fld('차량번호',i.plate),fld('차대번호(VIN)',i.vin),fld('원동기형식',i.engine),
  fld('차종',i.body_type),fld('제조사',i.maker),fld('검사유효기간',i.inspection_until)].join('');
 const ratio=i.ratio!=null?`<span class="${i.ratio<=60?'low':'hl'}">${i.ratio}%</span>`:'—';
 const price=[fld('감정가',won(i.appraisal)),
  `<div class="f"><div class="k">최저매각가</div><div class="v hl">${won(i.min_price)}</div></div>`,
  `<div class="f"><div class="k">최저가율</div><div class="v">${ratio}</div></div>`,
  fld('유찰',(i.fail_count||0)+'회'),fld('입찰보증금율',(i.deposit_rate!=null?i.deposit_rate+'%':'')),
  fld('청구금액',i.claim_amt!=null?won(i.claim_amt):'')].join('');
 // 회차별 이력
 let sched='';
 if((i.schedule||[]).length){
  sched=`<div class="card"><h2>회차별 진행 이력</h2><table>
   <tr><th>기일</th><th>구분</th><th style="text-align:right">최저매각가</th><th>결과</th></tr>`+
   i.schedule.map(r=>{const isNow=r.date===i.sale_date;
    const rc=r.result==='유찰'?'rslt-f':(r.result==='낙찰'||r.result==='매각허가')?'rslt-s':'';
    const kind=r.kind==='02'?'매각결정':'매각';
    return `<tr class="${isNow?'now':''}"><td>${r.date||'—'}${isNow?' ◀ 이번':''}</td>
     <td class="muted">${kind}</td><td style="text-align:right">${r.price?won(r.price):'—'}</td>
     <td class="${rc}">${r.result||(isNow?'예정':'—')}</td></tr>`;}).join('')+`</table></div>`;
 }
 // 감정평가 요항
 let aee='';
 if(i.appraiser_summary||i.appraiser_notes||i.rights){
  aee=`<div class="card"><h2>감정평가 요항 / 권리</h2>`+
   (i.appraiser_summary?`<div class="txt">${esc(i.appraiser_summary)}</div>`:'')+
   (i.appraiser_notes?`<div class="txt note" style="margin-top:8px">🔖 ${esc(i.appraiser_notes)}</div>`:'')+
   (i.rights?`<div class="txt note" style="margin-top:8px">⚖️ 말소기준/권리: ${esc(i.rights)}</div>`:'')+
   `</div>`;
 }
 // 사진 갤러리
 GAL=i.photos||[];
 let gallery='';
 if(GAL.length){
  gallery=`<div class="card"><h2>사진 ${GAL.length}장</h2><div class="gal">`+
   GAL.map((src,idx)=>`<img src="${src}" loading="lazy" alt="사진 ${idx+1}" onclick="lbOpen(${idx})">`).join('')+
   `</div></div>`;
 }
 const loc=i.storage||i.region;
 document.getElementById('body').innerHTML=`
  <h1>${esc(i.name||'—')}${badges(i)}</h1>
  <div class="case">${esc(i.court||'')} ${esc(i.dept||'')} · <b>${esc(i.case_no||'')}</b>${i.case_name?' · '+esc(i.case_name):''}</div>
  ${gallery}
  <div class="card"><h2>차량 정보</h2><div class="grid">${spec}</div></div>
  <div class="card"><h2>가격</h2><div class="price">${price}</div>
   ${i.prev_min_price?`<div class="muted" style="margin-top:8px;font-size:12.5px">직전 회차 최저가 ${won(i.prev_min_price)} → 현재 ${won(i.min_price)} (${i.drop_pct}%↓)</div>`:''}</div>
  <div class="card"><h2>매각기일 / 장소</h2><div class="grid">
   ${fld('매각기일',(i.sale_date||'—')+(i.sale_time?' '+i.sale_time:'')+' '+dday(i.sale_date))}
   ${fld('매각장소',i.sale_place)}
   ${fld('담당계 전화',i.tel)}
   ${fld('보관장소',loc?esc(loc):'')}
  </div></div>
  ${sched}
  ${aee}`;
 document.getElementById('foot').innerHTML=
  `수집 ${esc(D.generated_at||'')} · 본 페이지는 수집 시점의 스냅샷입니다. 실제 입찰 전 `+
  `<a href="https://www.courtauction.go.kr/pgj/index.on?w2xPath=/pgj/ui/pgj100/PGJ154M00.xml" target="_blank" rel="noopener">법원경매정보</a>에서 최신 상태를 반드시 확인하세요.`;
 document.title=`${i.name||'매물'} ${i.case_no||''} — 매물 상세`;
}
function renderIndex(){
 document.getElementById('body').innerHTML=
  `<h1>매물 상세</h1><div class="case">전체 ${items.length}건 · 사건번호를 선택하면 상세가 열립니다</div>
   <div class="card idx">`+
   items.map(i=>`<a href="#${anc(i)}">${esc(i.name||'—')} <span class="m">· ${esc(i.court||'')} ${esc(i.case_no||'')} · 최저 ${won(i.min_price)} (${i.ratio!=null?i.ratio+'%':'—'})</span></a>`).join('')+
   `</div>`;
 document.getElementById('foot').innerHTML='';
}
function route(){
 const h=decodeURIComponent((location.hash||'').replace(/^#/,''));
 if(!h){renderIndex();window.scrollTo(0,0);return;}
 const i=items.find(x=>anc(x)===h);
 if(i){renderDetail(i);window.scrollTo(0,0);}
 else{document.getElementById('body').innerHTML=
   `<div class="empty">해당 매물을 찾을 수 없습니다.<br>목록이 갱신되었을 수 있어요. <a href="carbid_detail.html">전체 목록 보기</a></div>`;}
}
window.addEventListener('hashchange',route);
route();
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
