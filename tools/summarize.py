#!/usr/bin/env python3
"""data/latest.json 을 사람이 읽기 쉬운 요약으로 출력 (영상 대본 작성용).

python3 tools/summarize.py [YYYYMMDD]   # 기본: 오늘(KST)
"""
import datetime as dt, json, os, sys

KST = dt.timezone(dt.timedelta(hours=9))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
d = json.load(open(os.path.join(ROOT, "data", "latest.json"), encoding="utf-8"))
now = dt.datetime.now(KST)
target = sys.argv[1] if len(sys.argv) > 1 else now.strftime("%Y%m%d")
tdate = dt.datetime.strptime(target, "%Y%m%d")

gen = dt.datetime.fromisoformat(d["generated_at"])
age_h = (now - gen).total_seconds() / 3600
print(f"# 데이터 생성: {gen:%Y-%m-%d %H:%M} KST ({age_h:.1f}시간 전)  {'⚠️ 오래됨' if age_h > 3 else 'OK'}")
print(f"# 대상 날짜: {tdate:%Y-%m-%d}")
print("# 수집 오류:", d.get("errors") or "없음")

k = d.get("kma")
if k:
    print(f"\n## 기상청 단기예보 (발표 {k['base_date']} {k['base_time']})")
    print("도시 | 최저 | 최고 | 오전 | 오후 | 강수확률(오전/오후)")
    for c, v in k["cities"].items():
        x = v["daily"].get(target, {})
        print(f"{c} | {x.get('min', '-')} | {x.get('max', '-')} | {x.get('sky_am', '-')} | {x.get('sky_pm', '-')} | {x.get('pop_am', '-')}/{x.get('pop_pm', '-')}")
    print("\n### 서울 시간별 (대상일)")
    for h in k["cities"].get("서울", {}).get("hourly", []):
        if h["time"].startswith(target) and int(h["time"][8:10]) % 3 == 0:
            print(f"  {h['time'][8:10]}시 {h['tmp']}° {h['sky']}{('/' + h['pty']) if h['pty'] else ''} 강수확률{h['pop']}% {h['pcp'] if h['pcp'] not in ('', '강수없음') else ''}")
    rainy = []
    for c, v in k["cities"].items():
        for dd, x in sorted(v["daily"].items()):
            if dd >= target and x.get("pop_max", 0) >= 60: rainy.append((dd, c, x.get("pop_max")))
    if rainy:
        print("\n### 강수확률 60% 이상 (대상일 이후)")
        for dd, c, p in rainy: print(f"  {dd} {c} {p}%")

a = d.get("air")
if a:
    key = f"{tdate:%Y-%m-%d}"
    for code in ("PM10", "PM25"):
        v = a.get(code)
        if not v: continue
        day = v["days"].get(key)
        print(f"\n## 미세먼지 {code} ({v['data_time']})")
        if day:
            print("  ", ", ".join(f"{r}:{g}" for r, g in day["grades"].items()))
            print("  ", day["overall"])
        else:
            print("   대상일 예보 없음. 있는 날짜:", list(v["days"]))

m = d.get("models", {})
if m:
    print("\n## 모델 비교 (참고용, 최근접 격자, 일별)")
    for name, v in m.items(): print(f"  {name} run: {v['run']}")
    rows = []
    cities = set.intersection(*[set(v["cities"]) for v in m.values()]) if len(m) > 1 else set()
    for c in sorted(cities):
        ec = {x["date"]: x for x in m.get("ecmwf", {}).get("cities", {}).get(c, [])}
        gf = {x["date"]: x for x in m.get("gfs", {}).get("cities", {}).get(c, [])}
        kd = (k or {}).get("cities", {}).get(c, {}).get("daily", {}) if k else {}
        for dd in sorted(set(ec) & set(gf)):
            if dd < target: continue
            e, g = ec[dd], gf[dd]; kk = kd.get(dd, {})
            rows.append((abs(e["precip"] - g["precip"]), abs(e["tmin"] - g["tmin"]), c, dd, e, g, kk))
    print("\n### 강수량 차이 큰 순 (도시 날짜: ECMWF / GFS / 기상청 강수확률)")
    for r in sorted(rows, key=lambda r: -r[0])[:8]:
        _, _, c, dd, e, g, kk = r
        print(f"  {c} {dd}: 비 {e['precip']}mm / {g['precip']}mm / 기상청 {kk.get('pop_max', '-')}%")
    print("\n### 최저기온 차이 큰 순 (ECMWF / GFS / 기상청)")
    for r in sorted(rows, key=lambda r: -r[1])[:6]:
        _, _, c, dd, e, g, kk = r
        print(f"  {c} {dd}: {e['tmin']}° / {g['tmin']}° / 기상청 {kk.get('min', '-')}°   (최고 {e['tmax']}/{g['tmax']}/{kk.get('max', '-')})")
