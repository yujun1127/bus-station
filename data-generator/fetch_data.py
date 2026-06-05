#!/usr/bin/env python3
"""
fetch_data.py  — 울산광역시 버스 대시보드 (500 에러 완벽 방어 및 Fallback 버전)
────────────────────────────────────────────
API 엔드포인트 (공공데이터포털 B551982/rte 및 bstp)
  mst_info     : 노선 마스터  (rteId, rteNo, rteType, stpnt, edpnt)
  rtm_loc_info : 실시간 GPS   (lat, lot, vhclNo, rteId, gthrDt)
  bstp_info    : 정류장 목록  (arsId/bstpId, bsNm/bstpNm, lat, lot)
  bstp_rte_info: 정류장별 노선 (arsId → rteId, rteNo)
────────────────────────────────────────────
"""

import requests, os, sys, json, re, time
from datetime import datetime, timezone, timedelta
from collections import defaultdict, Counter
from math import floor

OUTPUT_DIR    = "/app/output"
OUTPUT_FILE   = os.path.join(OUTPUT_DIR, "index.html")
NEARBY_SRC    = "/app/nearby.html"
NEARBY_OUTPUT = os.path.join(OUTPUT_DIR, "nearby.html")
BUS_DATA_JSON   = os.path.join(OUTPUT_DIR, "bus_data.json")
STOPS_DATA_JSON = os.path.join(OUTPUT_DIR, "stops_data.json")
KST = timezone(timedelta(hours=9))

SERVICE_KEY   = "71e3225e2bb79ab33516fb2188a9e5a9c3e696f87b3bcab56682066d9ac76996"
KAKAO_API_KEY = os.environ.get("KAKAO_API_KEY", "")
STDG_CD       = "3100000000"  # 울산광역시 법정동코드 기본값
BASE_URL      = "https://apis.data.go.kr/B551982"
GRID_SIZE     = 0.01

# ── 노선 분류 ─────────────────────────────────────────────────
TRUNK_ROUTES    = {"114","118","122","134","142","215","216","217","225","318","417","523",
                   "711","712","713","714","715","716","718","721","722","723","724","725",
                   "728","731","732","734","735","741","742","743","744","752","753","754",
                   "762","763","772","773"}
EXPRESS_ROUTES  = {"1134","1144","1154","1715"}
CIRCULAR_ROUTES = {"순환11","순환12","순환21","순환22"}
BRANCH_ROUTES    = {"남구02","북구05"}

def classify_route(rte_no: str) -> str:
    base = re.sub(r"\s*\(.*?\)", "", str(rte_no)).strip()
    if base in TRUNK_ROUTES:    return "일반시내버스"
    if base in EXPRESS_ROUTES:  return "좌석·직행좌석"
    if base in CIRCULAR_ROUTES: return "순환버스"
    if base in BRANCH_ROUTES:   return "지선버스"
    return "기타"

# ── Kakao 역지오코딩 ──────────────────────────────────────────
_addr_cache: dict = {}
def coord_to_address(lat, lon):
    ck = (round(lat,3), round(lon,3))
    if ck in _addr_cache: return _addr_cache[ck]
    if not KAKAO_API_KEY:
        v = f"{lat:.4f}°N {lon:.4f}°E"; _addr_cache[ck]=v; return v
    try:
        r = requests.get("https://dapi.kakao.com/v2/local/geo/coord2address.json",
            params={"x":lon,"y":lat,"input_coord":"WGS84"},
            headers={"Authorization":f"KakaoAK {KAKAO_API_KEY}"}, timeout=5)
        r.raise_for_status()
        docs = r.json().get("documents",[])
        obj  = docs[0] if docs else {}
        v    = (obj.get("road_address") or obj.get("address") or {}).get("address_name","") or f"{lat:.4f}°N {lon:.4f}°E"
    except: v = f"{lat:.4f}°N {lon:.4f}°E"
    _addr_cache[ck] = v; return v

# ── API 호출 (공통) ───────────────────────────────────────────
def fetch_all_pages(endpoint: str, extra: dict = {}) -> list:
    all_items, page = [], 1
    # [교정] bstp_info의 500 에러 과부하 방지를 위해 페이징 단위를 50개로 대폭 축소
    rows_per_page = 50 if endpoint == "bstp_info" else 1000
    
    while True:
        params = {"serviceKey": SERVICE_KEY, "pageNo": page,
                  "numOfRows": rows_per_page, "type": "json", "stdgCd": STDG_CD, **extra}
        
        if endpoint == "bstp_info":
            url = f"{BASE_URL}/bstp/{endpoint}"
            # [교정] 500 에러 원인이 되는 잘못된 파라미터 방어
            if "cityCode" in params: del params["cityCode"]
        else:
            url = f"{BASE_URL}/rte/{endpoint}"

        # 500 에러 및 타임아웃 발생 시 최대 2회 딜레이 후 재시도
        success = False
        for retry in range(3):
            try:
                resp = requests.get(url, params=params, timeout=15)
                if resp.status_code == 500:
                    raise requests.exceptions.HTTPError("500 Server Error")
                resp.raise_for_status()
                data = resp.json()
                success = True
                break
            except Exception as e:
                if retry == 2:
                    print(f"[ERROR] {endpoint} p{page}: {e}", file=sys.stderr)
                    break
                time.sleep(1.5)

        if not success:
            break

        body  = data.get("body",{})
        items = body.get("items",{})
        if isinstance(items, dict): items = items.get("item",[])
        if not items: break
        if isinstance(items, dict): items = [items]
        all_items.extend(items)
        total = int(body.get("totalCount",0))
        if not total or len(all_items) >= total or len(items) < rows_per_page: break
        page += 1
    return all_items

# ── 정류장 전체 + 정류장별 노선 수집 ────────────────────────
def fetch_stops_with_routes(route_map: dict, locations: list) -> list:
    print("  → 정류장 목록(bstp_info) 조회 중...", flush=True)
    raw = fetch_all_pages("bstp_info")
    print(f"    bstp_info 수집 결과: {len(raw)}건", flush=True)

    stops = []
    for s in raw:
        try:
            lat = float(s.get("lat") or s.get("gpsY") or 0)
            lot = float(s.get("lot") or s.get("gpsX") or 0)
            if not lat or not lot: continue
        except: continue
        sid  = str(s.get("arsId") or s.get("bstpId") or "")
        snm  = s.get("bsNm") or s.get("bstpNm") or sid
        stops.append({"arsId":sid, "bsNm":snm, "lat":lat, "lot":lot, "routes":[]})

    # [교정] 만약 공공데이터 500 에러로 정류장 목록이 0건일 때 대시보드가 터지지 않게 실시간 버스 좌표로 동적 복구(Fallback)
    if not stops and locations:
        print("    [WARN] bstp_info가 완전히 차단됨. 실시간 버스 좌표 정보로 임시 정류장 클러스터를 생성합니다.", flush=True)
        fallback_map = {}
        for loc in locations:
            try:
                lat, lot = float(loc["lat"]), float(loc["lot"])
                rid = str(loc.get("rteId",""))
                rno = route_map.get(rid, {}).get("no", rid)
                # 약 100m 단위로 유니크 키 생성하여 가상 정류장화
                fallback_key = (round(lat, 3), round(lot, 3))
                if fallback_key not in fallback_map:
                    fallback_map[fallback_key] = {
                        "arsId": f"FB-{fallback_key[0]}-{fallback_key[1]}",
                        "bsNm": coord_to_address(lat, lot).split()[-1] + " 근처",
                        "lat": lat, "lot": lot, "routes": set()
                    }
                if rno:
                    fallback_map[fallback_key]["routes"].add(rno)
            except: continue
        
        for fb in fallback_map.values():
            stops.append({
                "arsId": fb["arsId"], "bsNm": fb["bsNm"], "lat": fb["lat"], "lot": fb["lot"],
                "routes": [{"rteId":"","rteNo":r, "category":classify_route(r)} for r in fb["routes"]]
            })
        print(f"    [Fallback] 실시간 데이터 기반 가상 정류장 {len(stops)}개 확보 성공.", flush=True)
        return stops

    if not stops:
        return []

    print(f"  → 정류장별 노선(bstp_rte_info) 조회 중... (최대 {len(stops)}건)", flush=True)
    stop_map = {s["arsId"]: s for s in stops if s["arsId"]}
    ok, fail = 0, 0
    for arsId, stop in list(stop_map.items())[:100]: # 부하 최소화를 위해 상위 100개 제한
        try:
            items = fetch_all_pages("bstp_rte_info", {"arsId": arsId})
            routes = []
            for r in items:
                rid  = str(r.get("rteId",""))
                rno  = r.get("rteNo","") or route_map.get(rid,{}).get("no","")
                if rno:
                    routes.append({"rteId":rid,"rteNo":str(rno),
                                   "category":classify_route(str(rno))})
            stop["routes"] = routes
            ok += 1
        except:
            fail += 1
    print(f"    노선 매핑: {ok}개 성공 / {fail}개 실패", flush=True)
    return stops

# ── GPS 격자 ──────────────────────────────────────────────────
def to_grid(lat, lot):
    return (floor(lat/GRID_SIZE)*GRID_SIZE, floor(lot/GRID_SIZE)*GRID_SIZE)

# ── 분석 ──────────────────────────────────────────────────────
def analyze(routes, locations):
    route_map = {str(r.get("rteId","")): {"no":r.get("rteNo",""),"type":r.get("rteType","")}
                 for r in routes}
    valid = []
    for loc in locations:
        try:
            lat=float(loc["lat"]); lot=float(loc["lot"])
            if not lat or not lot: continue
            rid = str(loc.get("rteId",""))
            rno = route_map.get(rid,{"no":rid})["no"]
            valid.append({"lat":lat,"lot":lot,"rteId":rid,"rteNo":rno,
                          "vhclNo":str(loc.get("vhclNo","")),"gthrDt":loc.get("gthrDt",""),
                          "category":classify_route(rno)})
        except: continue

    grid_buses, grid_coords = defaultdict(list), {}
    for v in valid:
        gk = to_grid(v["lat"],v["lot"])
        rinfo = route_map.get(v["rteId"],{"no":v["rteId"],"type":""})
        grid_buses[gk].append({**rinfo,"category":v["category"],
                                "vhclNo":v["vhclNo"],"lat":v["lat"],"lot":v["lot"],"gthrDt":v["gthrDt"]})
        if gk not in grid_coords: grid_coords[gk]=(v["lat"],v["lot"])

    sorted_grids = sorted(grid_buses.items(), key=lambda x:-len(x[1]))
    grid_addr = {gk:coord_to_address(*grid_coords[gk]) for gk,_ in sorted_grids[:50]}
    ranked = [{"grid":list(gk),"lat":grid_coords[gk][0],"lot":grid_coords[gk][1],
               "label":grid_addr.get(gk,f"{grid_coords[gk][0]:.4f}°N {grid_coords[gk][1]:.4f}°E"),
               "buses":buses,"count":len(buses),
               "cat_counts":dict(Counter(b["category"] for b in buses))}
              for gk,buses in sorted_grids]

    route_cnt = Counter(route_map.get(v["rteId"],{"no":v["rteId"]})["no"] for v in valid)
    return {"ranked":ranked,"valid":valid,"route_map":route_map,
            "total_buses":len(valid),"total_routes":len(routes),
            "total_grids":len(grid_buses),
            "route_top10":route_cnt.most_common(10),
            "cat_counts":dict(Counter(v["category"] for v in valid))}

# ── JSON 저장 ─────────────────────────────────────────────────
def atomic_write(path, obj):
    tmp = path+".tmp"
    with open(tmp,"w",encoding="utf-8") as f: json.dump(obj,f,ensure_ascii=False)
    os.replace(tmp, path)

def save_bus_data_json(data, now_str):
    atomic_write(BUS_DATA_JSON,{
        "updated_at":now_str,
        "route_map":data["route_map"],
        "locations":data["valid"],
        "total_buses":data["total_buses"],
    })
    print(f"  → {BUS_DATA_JSON} ({data['total_buses']}대)", flush=True)

def save_stops_data_json(stops, now_str):
    atomic_write(STOPS_DATA_JSON,{
        "updated_at": now_str,
        "total_stops": len(stops),
        "stops": stops,
    })
    print(f"  → {STOPS_DATA_JSON} ({len(stops)}개 정류장)", flush=True)

# ── HTML 생성 ─────────────────────────────────────────────────
CAT_COLORS = {
    "일반시내버스":  ("#e3b341","rgba(227,179,65,.12)"),
    "좌석·직행좌석": ("#79c0ff","rgba(121,192,255,.12)"),
    "순환버스":      ("#56d364","rgba(86,211,100,.12)"),
    "지선버스":      ("#f78166","rgba(247,129,102,.12)"),
    "기타":          ("#8b949e","rgba(139,148,158,.10)"),
}
CAT_ORDER = ["일반시내버스","좌석·직행좌석","순환버스","지선버스","기타"]

def pct_bar(val,max_val,color=None):
    p=max(3,round(val/max_val*100)) if max_val else 0
    fill=f"background:{color}" if color else ""
    return f'<div class="bar-track"><div class="bar-fill" style="width:{p}%;{fill}"></div></div>'

def rank_badge(i):
    d={1:("gold","🥇 1위"),2:("silver","🥈 2위"),3:("bronze","🥉 3위")}
    cls,txt=d.get(i,("normal",f"#{i}"))
    return f'<span class="badge {cls}">{txt}</span>'

def generate_html(data, now_str):
    ranked=data["ranked"]; top10=ranked[:10]; max_cnt=top10[0]["count"] if top10 else 1

    cards=""
    for i,g in enumerate(top10,1):
        seen = set()
        tags = ""
        for b in g["buses"]:
            if b["no"] not in seen:
                seen.add(b["no"])
                col,_=CAT_COLORS.get(b.get("category","기타"),("#8b949e",""))
                tags+=f'<span class="tag" style="border-color:{col}40;color:{col}">{b["no"]}</span>'
        times=[b["gthrDt"] for b in g["buses"] if b.get("gthrDt")]
        latest=max(times)[:16] if times else "—"
        cat_mini="".join(
            f'<span class="mini-cat" style="border-color:{CAT_COLORS[cn][0]};color:{CAT_COLORS[cn][0]};background:{CAT_COLORS[cn][1]}">{cn} {g["cat_counts"].get(cn,0)}</span>'
            for cn in CAT_ORDER if g["cat_counts"].get(cn,0))
        cards+=f"""<div class="card {'top3' if i<=3 else ''}">
          <div class="card-head">{rank_badge(i)}
            <span class="grid-label" title="{g['lat']:.4f},{g['lot']:.4f}">{g['label']}</span>
            <span class="cnt">{g['count']}대</span></div>
          {pct_bar(g['count'],max_cnt)}
          <div class="cat-mini-wrap">{cat_mini}</div>
          <div class="tags">{tags}</div>
          <div class="meta-row"><span>🕐 {latest}</span><span>📍 {g['lat']:.4f},{g['lot']:.4f}</span></div>
        </div>"""
    if not cards:
        cards='<div class="empty">⚠️ 실시간 버스 위치 데이터가 없습니다.<br>운행 시간대를 확인해 주세요.</div>'

    route_rows=""; max_rc=data["route_top10"][0][1] if data["route_top10"] else 1
    for rno,cnt in data["route_top10"]:
        cat=classify_route(rno); col,bg=CAT_COLORS.get(cat,("#8b949e","rgba(139,148,158,.10)"))
        route_rows+=f'<tr><td class="rno">{rno}<span class="mini-cat" style="border-color:{col};color:{col};background:{bg};margin-left:6px">{cat}</span></td><td>{pct_bar(cnt,max_rc)}</td><td class="rcnt">{cnt}대</td></tr>'

    all_rows=""
    for i,g in enumerate(ranked[:50],1):
        rs=", ".join(dict.fromkeys(b["no"] for b in g["buses"]))[:60]
        cat_str=" ".join(f'<span class="mini-cat" style="border-color:{CAT_COLORS[cn][0]};color:{CAT_COLORS[cn][0]};background:{CAT_COLORS[cn][1]}">{cn}&nbsp;{g["cat_counts"].get(cn,0)}</span>' for cn in CAT_ORDER if g["cat_counts"].get(cn,0))
        all_rows+=f'<tr class="{"hl" if i<=3 else ""}"><td>{i}</td><td class="addr-cell">{g["label"]}<br><span class="coord-sub">{g["lat"]:.4f},{g["lot"]:.4f}</span></td><td class="bcnt">{g["count"]}</td><td>{cat_str}</td><td class="rlist">{rs}</td></tr>'

    cat_summary="".join(f'<span class="cat-badge" style="border-color:{CAT_COLORS[cn][0]};color:{CAT_COLORS[cn][0]};background:{CAT_COLORS[cn][1]}">{cn} {data["cat_counts"].get(cn,0)}대</span>' for cn in CAT_ORDER if data["cat_counts"].get(cn,0))
    total=data["total_buses"] or 1
    cat_tr="".join(f'<tr><td><span class="mini-cat" style="border-color:{CAT_COLORS[cn][0]};color:{CAT_COLORS[cn][0]};background:{CAT_COLORS[cn][1]}">{cn}</span></td><td>{pct_bar(data["cat_counts"].get(cn,0),total,CAT_COLORS[cn][0])}</td><td class="bcnt">{data["cat_counts"].get(cn,0)}대</td><td class="bcnt" style="color:var(--muted)">{round(data["cat_counts"].get(cn,0)/total*100,1)}%</td></tr>' for cn in CAT_ORDER if data["cat_counts"].get(cn,0))

    return f"""<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<meta http-equiv="refresh" content="300"/>
<title>🚌 버스 밀집도 대시보드</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet"/>
<style>
:root{{--bg:#0d1117;--surface:#161b22;--surface2:#1c2333;--surface3:#21262d;--border:rgba(48,54,61,.9);--accent:#f78166;--accent2:#79c0ff;--green:#56d364;--text:#c9d1d9;--muted:#8b949e;--gold:#ffd700;--silver:#c0c0c0;--bronze:#cd7f32;--radius:12px}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Noto Sans KR',sans-serif;background:var(--bg);color:var(--text);background-image:radial-gradient(ellipse 70% 40% at 15% 5%,rgba(247,129,102,.07),transparent),radial-gradient(ellipse 50% 35% at 85% 85%,rgba(121,192,255,.05),transparent)}}
header{{padding:2.2rem 1.5rem 1.4rem;text-align:center;border-bottom:1px solid var(--border)}}
.pill{{display:inline-block;background:rgba(247,129,102,.1);border:1px solid rgba(247,129,102,.28);color:var(--accent);font-family:'Space Mono',monospace;font-size:.68rem;letter-spacing:.12em;padding:3px 12px;border-radius:20px;margin-bottom:.7rem;text-transform:uppercase}}
h1{{font-size:clamp(1.5rem,3vw,2.4rem);font-weight:700;background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.sub{{color:var(--muted);font-size:.82rem;margin-top:.35rem}}
.meta-bar{{display:flex;justify-content:center;gap:1.8rem;flex-wrap:wrap;padding:.55rem 1rem;background:var(--surface);border-bottom:1px solid var(--border);font-family:'Space Mono',monospace;font-size:.7rem;color:var(--muted)}}
.mi{{display:flex;align-items:center;gap:.35rem}}
.dot{{width:6px;height:6px;border-radius:50%;background:var(--green);animation:pulse 2s infinite}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:.4;transform:scale(1.5)}}}}
main{{max-width:1200px;margin:0 auto;padding:1.8rem 1.5rem 4rem}}
.sec{{font-size:.68rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:2.2rem 0 .9rem;display:flex;align-items:center;gap:.5rem}}
.sec::after{{content:'';flex:1;height:1px;background:var(--border)}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.9rem}}
.stat{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1.1rem 1.3rem}}
.slabel{{font-size:.68rem;color:var(--muted);font-weight:600;letter-spacing:.06em;text-transform:uppercase}}
.sval{{font-size:1.9rem;font-weight:700;font-family:'Space Mono',monospace;color:#fff;line-height:1.1;margin:.2rem 0 .1rem}}
.ssub{{font-size:.7rem;color:var(--muted)}}
.cat-badge{{font-size:.75rem;font-weight:600;padding:4px 12px;border-radius:8px;border:1px solid;display:inline-block;margin:.2rem}}
.mini-cat{{font-size:.63rem;font-weight:600;padding:2px 7px;border-radius:5px;border:1px solid;white-space:nowrap}}
.cat-mini-wrap{{display:flex;flex-wrap:wrap;gap:.25rem;margin-bottom:.4rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1rem}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:1.15rem;transition:transform .2s,border-color .2s;animation:fu .4s ease both}}
.card:hover{{transform:translateY(-3px);border-color:rgba(247,129,102,.3)}}
.card.top3{{border-color:rgba(247,129,102,.22);background:linear-gradient(135deg,var(--surface),rgba(247,129,102,.04))}}
@keyframes fu{{from{{opacity:0;transform:translateY(14px)}}to{{opacity:1;transform:translateY(0)}}}}
.card-head{{display:flex;align-items:center;gap:.5rem;margin-bottom:.6rem;flex-wrap:wrap}}
.grid-label{{font-size:.88rem;font-weight:700;flex:1;word-break:keep-all;line-height:1.3}}
.cnt{{font-family:'Space Mono',monospace;font-size:.95rem;font-weight:700;color:var(--accent);white-space:nowrap}}
.badge{{font-size:.68rem;font-weight:700;padding:2px 7px;border-radius:6px;white-space:nowrap}}
.badge.gold{{background:rgba(255,215,0,.12);color:var(--gold);border:1px solid rgba(255,215,0,.28)}}
.badge.silver{{background:rgba(192,192,192,.1);color:var(--silver);border:1px solid rgba(192,192,192,.22)}}
.badge.bronze{{background:rgba(205,127,50,.1);color:var(--bronze);border:1px solid rgba(205,127,50,.22)}}
.badge.normal{{background:var(--surface2);color:var(--muted);border:1px solid var(--border)}}
.bar-track{{background:var(--surface2);border-radius:4px;height:5px;margin:.45rem 0 .55rem;overflow:hidden}}
.bar-fill{{height:100%;border-radius:4px;background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .5s}}
.tags{{display:flex;flex-wrap:wrap;gap:.25rem;margin-bottom:.45rem}}
.tag{{font-size:.66rem;background:rgba(121,192,255,.08);border:1px solid rgba(121,192,255,.18);color:var(--accent2);padding:2px 7px;border-radius:5px}}
.meta-row{{display:flex;justify-content:space-between;font-size:.65rem;color:var(--muted);flex-wrap:wrap;gap:.2rem}}
.tbl-wrap{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}}
.full-wrap{{max-height:500px;overflow:auto}}
table{{width:100%;border-collapse:collapse}}
thead tr{{background:var(--surface2)}}
th{{padding:.55rem 1rem;font-size:.68rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:600;text-align:left}}
td{{padding:.5rem 1rem;font-size:.8rem;border-top:1px solid var(--border);vertical-align:middle}}
.rno{{font-weight:700}}.rcnt,.bcnt{{font-family:'Space Mono',monospace;font-weight:700;color:var(--accent);text-align:right}}
.addr-cell{{font-size:.8rem;line-height:1.5}}.coord-sub{{font-family:'Space Mono',monospace;font-size:.65rem;color:var(--muted)}}
.rlist{{color:var(--muted);font-size:.7rem}}tr.hl td{{background:rgba(247,129,102,.05)}}
.full-wrap th{{position:sticky;top:0;z-index:1;background:var(--surface2)}}
.empty{{background:rgba(247,129,102,.07);border:1px solid rgba(247,129,102,.2);border-radius:var(--radius);padding:2rem;color:var(--accent);text-align:center;line-height:1.8}}
footer{{text-align:center;padding:1.3rem;color:var(--muted);font-size:.7rem;border-top:1px solid var(--border)}}
footer a{{color:var(--accent2);text-decoration:none}}
@media(max-width:580px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body>
<header>
  <div class="pill">Real-time Bus Density · 울산광역시</div>
  <h1>🚌 버스 밀집도 대시보드</h1>
  <p class="sub">GPS 격자(약 1km²) 기반 실시간 버스 밀집도 분석 · 5분마다 자동 갱신</p>
  <div style="margin-top:.9rem">
    <a href="/nearby.html" style="display:inline-flex;align-items:center;gap:.4rem;background:rgba(247,129,102,.12);border:1px solid rgba(247,129,102,.35);color:var(--accent);text-decoration:none;padding:.45rem 1.1rem;border-radius:8px;font-size:.82rem;font-weight:700"
      onmouseover="this.style.background='rgba(247,129,102,.22)'" onmouseout="this.style.background='rgba(247,129,102,.12)'">
      📍 울산 버스 정류장 지도 &amp; 내 주변 버스
    </a>
  </div>
</header>
<div class="meta-bar">
  <span class="mi"><span class="dot"></span>LIVE</span>
  <span class="mi">🕐 {now_str}</span>
  <span class="mi">🚌 운행 버스 {data['total_buses']}대</span>
  <span class="mi">🛣️ 노선 {data['total_routes']}개</span>
  <span class="mi">📍 분석 구역 {data['total_grids']}개</span>
</div>
<main>
  <p class="sec">📊 요약 통계</p>
  <div class="stats">
    <div class="stat"><div class="slabel">실시간 운행 버스</div><div class="sval">{data['total_buses']}</div><div class="ssub">대</div></div>
    <div class="stat"><div class="slabel">등록 노선 수</div><div class="sval">{data['total_routes']}</div><div class="ssub">개 노선</div></div>
    <div class="stat"><div class="slabel">분석 구역 수</div><div class="sval">{data['total_grids']}</div><div class="ssub">개 격자(1km²)</div></div>
    <div class="stat"><div class="slabel">최고 밀집 구역</div><div class="sval" style="font-size:.9rem;margin-top:.35rem">{ranked[0]['label'] if ranked else '—'}</div><div class="ssub">{ranked[0]['count'] if ranked else 0}대 밀집</div></div>
  </div>
  <p class="sec">🚦 노선 유형별 운행 현황</p>
  <div style="margin-bottom:1rem">{cat_summary}</div>
  <div class="tbl-wrap"><table><thead><tr><th>유형</th><th>비율</th><th>버스 수</th><th>점유율</th></tr></thead><tbody>{cat_tr}</tbody></table></div>
  <p class="sec">🏆 버스 밀집도 TOP 10 구역</p>
  <div class="grid">{cards}</div>
  <p class="sec">🛣️ 노선별 운행 버스 수 TOP 10</p>
  <div class="tbl-wrap"><table><thead><tr><th>노선번호/유형</th><th>비율</th><th>버스 수</th></tr></thead><tbody>{route_rows or '<tr><td colspan="3" style="text-align:center;color:var(--muted);padding:1.5rem">데이터 없음</td></tr>'}</tbody></table></div>
  <p class="sec">📋 전체 구역 밀집도 순위 (상위 50개)</p>
  <div class="tbl-wrap full-wrap"><table><thead><tr><th>순위</th><th>주소/좌표</th><th>버스수</th><th>유형</th><th>주요노선</th></tr></thead><tbody>{all_rows or '<tr><td colspan="5" style="text-align:center;color:var(--muted);padding:1.5rem">데이터 없음</td></tr>'}</tbody></table></div>
</main>
<footer>
  데이터: <a href="https://www.data.go.kr" target="_blank">공공데이터포털</a> · 울산광역시 버스 API ·
  주소변환: Kakao API · <a href="/nearby.html">📍 정류장 지도</a> · <a href="https://docs.docker.com/compose/" target="_blank">Docker Compose</a>
</footer></body></html>"""

# ── nearby.html 배포 ──────────────────────────────────────────
def deploy_nearby():
    kakao_js = os.environ.get("KAKAO_JS_KEY","")
    if not os.path.exists(NEARBY_SRC):
        print("[WARN] nearby.html 원본 없음", flush=True); return
    with open(NEARBY_SRC,"r",encoding="utf-8") as f: content=f.read()
    if kakao_js: content=content.replace("929d779966b30f02bd8801203e721e14", kakao_js)
    tmp=NEARBY_OUTPUT+".tmp"
    with open(tmp,"w",encoding="utf-8") as f: f.write(content)
    os.replace(tmp, NEARBY_OUTPUT)
    print(f"  → {NEARBY_OUTPUT} 배포", flush=True)

# ── 메인 ──────────────────────────────────────────────────────
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    now_kst=datetime.now(KST)
    now_str=now_kst.strftime("%Y년 %m월 %d일 %H:%M:%S KST")
    print(f"[{now_kst.strftime('%H:%M:%S')}] 수집 시작", flush=True)

    routes    = fetch_all_pages("mst_info")
    locations = fetch_all_pages("rtm_loc_info")
    print(f"  → 노선 {len(routes)}개 / 위치 {len(locations)}건", flush=True)

    route_map = {str(r.get("rteId","")): {"no":r.get("rteNo",""),"type":r.get("rteType","")}
                 for r in routes}

    # [교정] 500 차단 시 실시간 GPS를 주입하기 위해 locations 파라미터 전달
    stops = fetch_stops_with_routes(route_map, locations)

    data = analyze(routes, locations)
    top  = data["ranked"][0] if data["ranked"] else {}
    print(f"  → 최고 밀집: {top.get('label','—')} ({top.get('count',0)}대)", flush=True)

    # 파일 출력
    html=generate_html(data, now_str)
    tmp=OUTPUT_FILE+".tmp"
    with open(tmp,"w",encoding="utf-8") as f: f.write(html)
    os.replace(tmp, OUTPUT_FILE)
    print(f"  → {OUTPUT_FILE}", flush=True)

    save_bus_data_json(data, now_str)
    save_stops_data_json(stops, now_str)
    deploy_nearby()

if __name__=="__main__":
    main()