#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
K-뷰티 대시보드 실데이터 수집기 (관세청 + 식약처)
================================================================
data.go.kr 인증키로 관세청·식약처 API를 호출해 kbeauty_data.js(+json)를 만듭니다.
표준 라이브러리만 사용 → 추가 설치 불필요.

제품군: 관세청 hsSgn=3304 한 번 호출에 6단위 소분류가 함께 오므로, 그것을 나눠
        '색조(메이크업)=330410/20/30/91'와 '기초·기타=330499'로 분리합니다.
        (호출 수는 4단위 5개로, 안정적으로 동작하던 수준 그대로)
견고성: 개별 호출이 실패해도 그 칸만 0으로 두고 끝까지 진행 → 항상 결과 파일을 만듭니다.

사용법:  python3 kbeauty_collector.py --key "인증키"   [--no-eu]
"""
import argparse, json, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import date

CUSTOMS_URL = "https://apis.data.go.kr/1220000/nitemtrade/getNitemtradeList"

# 제품군(표시 순서)과 평균단가(USD/kg, 중량환산용)
CATS = [
    ("기초·기타(스킨케어)", 35),
    ("색조화장품(메이크업)", 48),
    ("향수·화장수",         70),
    ("두발용",             10),
    ("면도·탈취·기타",      12),
    ("비누·세안",          12),
]
NC = len(CATS)
CI = {name: i for i, (name, _) in enumerate(CATS)}
QUERY_HS = ["3303", "3304", "3305", "3307", "3401"]   # 4단위 5개만 호출
MAKEUP_SUB = {"330410", "330420", "330430", "330491"}  # 색조(립·아이·네일·파우더)


def hscd_to_cat(hs):
    hs = str(hs or "")
    if hs.startswith("3303"): return CI["향수·화장수"]
    if hs.startswith("3305"): return CI["두발용"]
    if hs.startswith("3307"): return CI["면도·탈취·기타"]
    if hs.startswith("3401"): return CI["비누·세안"]
    if hs.startswith("3304"):
        return CI["색조화장품(메이크업)"] if hs[:6] in MAKEUP_SUB else CI["기초·기타(스킨케어)"]
    return None


TARGETS = [
    ("미국", "United States of America", "북미", "US"),
    ("중국", "China", "동아시아", "CN"),
    ("일본", "Japan", "동아시아", "JP"),
    ("베트남", "Vietnam", "동남아", "VN"),
    ("홍콩", "Hong Kong", "동아시아", "HK"),
    ("대만", "Taiwan", "동아시아", "TW"),
    ("태국", "Thailand", "동남아", "TH"),
    ("싱가포르", "Singapore", "동남아", "SG"),
    ("라오스", "Laos", "동남아", "LA"),
    ("아랍에미리트", "United Arab Emirates", "중동", "AE"),
    ("인도", "India", "서남아", "IN"),
    ("호주", "Australia", "오세아니아", "AU"),
    ("영국", "United Kingdom", "유럽", "GB"),
    ("프랑스", "France", "유럽", "FR"),
    ("러시아", "Russia", "CIS", "RU"),
    ("폴란드", "Poland", "유럽", "PL"),
    ("독일", "Germany", "유럽", "DE"),
]
EU_MEMBERS = [("프랑스", "FR"), ("독일", "DE"), ("이탈리아", "IT"), ("스페인", "ES"), ("네덜란드", "NL"),
              ("폴란드", "PL"), ("스웨덴", "SE"), ("벨기에", "BE"), ("오스트리아", "AT"),
              ("아일랜드", "IE"), ("덴마크", "DK"), ("핀란드", "FI"), ("포르투갈", "PT")]

YEARS = [2024, 2025, 2026]
MONTHS = [f"{y}{m:02d}" for y in YEARS for m in range(1, 13)]
N = len(MONTHS)
IDX = {p: i for i, p in enumerate(MONTHS)}
TODAY = date.today()
LAST_ACTUAL = max((i for i, p in enumerate(MONTHS)
                   if (int(p[:4]), int(p[4:])) < (TODAY.year, TODAY.month)), default=0)

WARN = {"auth": None, "fail": 0}   # 실행 중 경고 수집


class AuthError(Exception):
    pass


def http_get_url(base, params, retries=4):
    url = f"{base}?{urllib.parse.urlencode(params, safe='%')}"
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kbeauty/4.0"})
            with urllib.request.urlopen(req, timeout=50) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            last = e; time.sleep(1.2 * (i + 1))
    raise last


def http_get(params):
    return http_get_url(CUSTOMS_URL, params)


def parse_items(xml_text):
    """(period, hsCd, expDlr, impDlr) 목록. 인증/한도 오류면 AuthError."""
    root = ET.fromstring(xml_text)  # ParseError는 상위에서 처리
    for tag in root.iter():
        if tag.tag.lower().endswith("returnauthmsg") and tag.text:
            raise AuthError(tag.text.strip())
    out = []
    for it in root.iter():
        if not it.tag.lower().endswith("item"):
            continue
        d = {ch.tag.split('}')[-1]: (ch.text or "").strip() for ch in it}
        yr = d.get("year", "")
        if "." not in yr:      # '총계' 행 제외
            continue
        try:
            exp = float(d.get("expDlr", 0) or 0)
            imp = float(d.get("impDlr", 0) or 0)
        except ValueError:
            continue
        out.append((yr.replace(".", ""), d.get("hsCd", ""), exp, imp))
    return out


def blank():
    return [[0.0] * N for _ in range(NC)]


def project_tail(mat):
    for ci in range(NC):
        row = mat[ci]
        for i in range(LAST_ACTUAL + 1, N):
            base = row[i - 12] if i - 12 >= 0 else row[LAST_ACTUAL]
            yoy = 0.0
            if i - 24 >= 0 and row[i - 24] > 0:
                yoy = max(-0.3, min(0.6, row[i - 12] / row[i - 24] - 1))
            row[i] = max(0.0, base * (1 + yoy))


def collect(key, cc):
    exp_m, imp_m = blank(), blank()
    for hs in QUERY_HS:
        for y in YEARS:
            if y > TODAY.year:
                continue
            try:
                items = parse_items(http_get({
                    "serviceKey": key, "strtYymm": f"{y}01", "endYymm": f"{y}12",
                    "hsSgn": hs, "cntyCd": cc}))
            except AuthError as ae:
                WARN["auth"] = str(ae)      # 키 미등록/한도초과 등
                raise
            except Exception:
                WARN["fail"] += 1           # 일시 오류 → 이 칸만 0, 계속 진행
                continue
            for period, hscd, exp, imp in items:
                j = IDX.get(period); ci = hscd_to_cat(hscd)
                if j is not None and ci is not None:
                    exp_m[ci][j] += exp / 1e6
                    imp_m[ci][j] += imp / 1e6
    project_tail(exp_m); project_tail(imp_m)
    return exp_m, imp_m


# ===== 식약처 =====
MFDS_RPT = "https://apis.data.go.kr/1471000/FtnltCosmRptPrdlstInfoService/getRptPrdlstInq"
MFDS_MFCR = "https://apis.data.go.kr/1471000/CsmtcsMfcrtrInfoService01/getCsmtcsMfcrtrInfoList01"


def mfds_body(base, key, page_no, rows):
    j = json.loads(http_get_url(base, {"serviceKey": key, "pageNo": page_no,
                                       "numOfRows": rows, "type": "json"}))
    b = j.get("body") or j.get("response", {}).get("body") or {}
    items = b.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    return int(b.get("totalCount") or 0), items


def fmt_date(s):
    s = str(s or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else s


def short_addr(a):
    parts = (a or "").split()
    return " ".join(parts[:2]) if len(parts) >= 2 else (a or "")


FUNC_CUTOFF = f"{TODAY.year - 2}{TODAY.month:02d}"   # 보고일 기준 최근 2년
COMP_CUTOFF = f"{TODAY.year - 3}{TODAY.month:02d}"   # 허가/등록 기준 최근 3년
FUNC_MAX = 5000   # 표에 담을 최신 기능성화장품 최대 건수(최근순)


def fetch_functionals(key):
    """보고일이 최근 2년 이내인 기능성화장품을 최신순 최대 FUNC_MAX건 수집.
    보고품목은 오래된→최신 순(마지막 페이지가 최신)이라, 마지막 페이지부터
    거꾸로 훑고 최근 2년 구간을 벗어나거나 FUNC_MAX건을 채우면 조기 종료한다.
    → 전 페이지(수천 페이지) 스캔을 피해 몇 분 내에 끝난다."""
    total, _ = mfds_body(MFDS_RPT, key, 1, 1)
    per = 100
    pages = max(1, -(-total // per))
    recent, newN, out_streak = [], 0, 0
    p = pages
    while p >= 1 and out_streak < 3 and len(recent) < FUNC_MAX:
        try:
            _, items = mfds_body(MFDS_RPT, key, p, per)
        except Exception:
            WARN["fail"] += 1
            p -= 1
            continue
        page_max = "0"
        for it in items:
            dt = str(it.get("REPORT_DATE", "") or "")
            nm = it.get("ITEM_NAME") or ""
            if len(dt) >= 6 and dt[:6] > page_max:
                page_max = dt[:6]
            if nm and len(dt) >= 6 and dt[:6] >= FUNC_CUTOFF:
                recent.append((dt, nm, it.get("ENTP_NAME") or "", it.get("REPORT_FLAG_NAME") or "보고"))
                if dt[:4] == str(TODAY.year):
                    newN += 1
        out_streak = out_streak + 1 if page_max < FUNC_CUTOFF else 0
        if (pages - p) % 25 == 0:
            print(f"    · 기능성 역방향 스캔 {pages - p + 1}p (최근2년 {len(recent):,}건)", flush=True)
        p -= 1
    recent.sort(key=lambda x: x[0], reverse=True)
    recent = recent[:FUNC_MAX]
    funcs = [[nm, en, fl, "보고", fmt_date(dt)] for (dt, nm, en, fl) in recent]
    return funcs, total, newN


def fetch_companies(key):
    """허가/등록일이 최근 3년 이내인 업체를 전부 수집(전 페이지 스캔)."""
    total, _ = mfds_body(MFDS_MFCR, key, 1, 1)
    per = 100
    pages = max(1, -(-total // per))
    picked = []
    for p in range(1, pages + 1):
        try:
            _, items = mfds_body(MFDS_MFCR, key, p, per)
        except Exception:
            WARN["fail"] += 1
            continue
        for it in items:
            nm = it.get("ENTP_NAME") or ""
            pd = str(it.get("ENTP_PERMIT_DATE", "") or "")
            if nm and len(pd) >= 6 and pd[:6] >= COMP_CUTOFF:
                picked.append((pd, [nm, it.get("INDUTY") or "", short_addr(it.get("FACTORY_ADDR")),
                                    it.get("BOSS_NAME") or "", "", "", pd[:4]]))
        if p % 25 == 0:
            print(f"    · 업체 {p}/{pages}p 스캔 (최근3년 {len(picked):,}개사)", flush=True)
    picked.sort(key=lambda x: x[0], reverse=True)
    rows = [r for _, r in picked]
    return rows, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", required=True)
    ap.add_argument("--no-eu", action="store_true")
    ap.add_argument("--out", default="kbeauty_data.json")
    a = ap.parse_args()

    print(f"관세청 수집 시작 · 기간 {MONTHS[0]}~{MONTHS[-1]} · 실적마지막 {MONTHS[LAST_ACTUAL]}")
    print(f"(제품군 {NC}개 · 4단위 5회 호출로 6단위 소분류 분리 · 개별 {len(TARGETS)}개국"
          f"{' + 유럽 EU평균' if not a.no_eu else ''})\n", flush=True)
    countries, EXP, IMP = [], [], []

    for ko, en, region, cc in TARGETS:
        print(f"· {ko}({cc}) 수집 중…", flush=True)
        try:
            e, im = collect(a.key, cc)
        except AuthError as ae:
            print(f"\n! 관세청 인증/한도 오류: {ae}\n  → 인증키 승인 상태 또는 일일 호출한도(1만건)를 확인하세요.", flush=True)
            e, im = blank(), blank()
        except Exception as ex:
            print(f"  ! {ko} 건너뜀: {ex}", flush=True)
            e, im = blank(), blank()
        countries.append({"ko": ko, "en": en, "region": region, "cc": cc})
        EXP.append(e); IMP.append(im)

    if not a.no_eu:
        print("· 유럽(EU 회원국) 수집 중…", flush=True)
        euE, euI, got = blank(), blank(), 0
        for ko, cc in EU_MEMBERS:
            try:
                e, im = collect(a.key, cc); got += 1
                for ci in range(NC):
                    for i in range(N):
                        euE[ci][i] += e[ci][i]; euI[ci][i] += im[ci][i]
            except Exception as ex:
                print(f"   (건너뜀 {ko}/{cc}: {ex})", flush=True)
        if got:
            for ci in range(NC):
                for i in range(N):
                    euE[ci][i] /= got; euI[ci][i] /= got
            countries.append({"ko": "유럽(EU 평균)", "en": "European Union", "region": "유럽", "cc": "EU"})
            EXP.append(euE); IMP.append(euI)

    # ----- 식약처 -----
    functionals, companies, counts = [], [], {}
    try:
        print("· 식약처 기능성화장품 보고품목 수집…", flush=True)
        functionals, func_total, new2026 = fetch_functionals(a.key)
        print("· 식약처 화장품 업체 수집…", flush=True)
        companies, comp_total = fetch_companies(a.key)
        counts = {"companies": comp_total, "funcTotal": func_total, "newFunctional": new2026}
        print(f"  → 기능성 총 {func_total:,}건 중 최근2년 {len(functionals):,}건(올해 신규 {new2026:,}) · "
              f"업체 총 {comp_total:,}개사 중 최근3년 {len(companies):,}개사", flush=True)
    except Exception as ex:
        print(f"  (식약처 수집 실패: {ex} — 기능성·기업은 데모 유지)", flush=True)

    payload = {
        "source": "관세청 품목별 국가별 수출입실적 + 식약처 기능성·업체",
        "generated": TODAY.isoformat(), "unit": "백만 달러", "granularity": "monthly",
        "months": [{"key": f"{p[:4]}-{p[4:]}", "y": int(p[:4]), "m": int(p[4:]),
                    "label": f"{p[2:4]}.{p[4:]}"} for p in MONTHS],
        "lastActualIdx": LAST_ACTUAL,
        "countries": countries,
        "categories": [{"ko": name, "hs": "3304" if "색조" in name or "기초" in name else "", "price": price}
                       for name, price in CATS],
        "exp": EXP, "imp": IMP,
        "functionals": functionals, "companies": companies, "counts": counts,
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    with open(a.out.rsplit(".", 1)[0] + ".js", "w", encoding="utf-8") as f:
        f.write("window.KBEAUTY_DATA=" + json.dumps(payload, ensure_ascii=False) + ";")

    tot = sum(sum(sum(c) for c in country) for country in EXP)
    print(f"\n✓ 완료: kbeauty_data.js + kbeauty_data.json (수출 합계 약 {tot/100:.0f}억 달러)")
    if WARN["fail"]:
        print(f"  (참고: 일시적 호출 실패 {WARN['fail']}건은 건너뛰었습니다. 값이 비어 보이면 한 번 더 실행하세요.)")
    if WARN["auth"]:
        print(f"  (경고: 관세청 응답에 '{WARN['auth']}' — 한도/승인 상태 확인 필요)")
    print("  브라우저 새로고침 시 실데이터로 표시됩니다.")


if __name__ == "__main__":
    main()
