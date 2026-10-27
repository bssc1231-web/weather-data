#!/usr/bin/env python3
"""매일 새벽 GitHub Actions에서 실행: 날씨 데이터를 모아 data/latest.json 으로 저장.

1) 기상청 단기예보 (공식 예보, data.go.kr)   -> kma
2) 에어코리아 미세먼지 예보 (data.go.kr)     -> air
3) ECMWF IFS 공개 데이터 (원본 GRIB)         -> models.ecmwf
4) NOAA GFS 0.25 (NOMADS 지역 필터)          -> models.gfs

각 단계는 독립적으로 실패할 수 있고, 실패하면 errors 에 기록만 하고 계속 진행.
환경변수: DATA_GO_KR_KEY (공공데이터포털 일반 인증키, Decoding 값)
"""
import datetime as dt, json, math, os, sys, time, traceback
import requests

KST = dt.timezone(dt.timedelta(hours=9))
UTC = dt.timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data")
TMP = os.path.join(ROOT, ".tmp")
os.makedirs(OUT_DIR, exist_ok=True); os.makedirs(TMP, exist_ok=True)

CITIES = {  # name: (lat, lon)
    "서울": (37.57, 126.98), "인천": (37.46, 126.70), "춘천": (37.88, 127.73), "강릉": (37.75, 128.90),
    "청주": (36.64, 127.49), "대전": (36.35, 127.38), "세종": (36.48, 127.29), "전주": (35.82, 127.15),
    "광주": (35.16, 126.85), "대구": (35.87, 128.60), "부산": (35.18, 129.08), "울산": (35.54, 129.31),
    "제주": (33.50, 126.53), "울릉도": (37.48, 130.90), "안동": (36.57, 128.73), "목포": (34.81, 126.39),
}

errors = []
def log(*a): print(*a, flush=True)

def get(url, params=None, tries=3, timeout=40, **kw):
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout, **kw)
            r.raise_for_status(); return r
        except Exception as e:
            if i == tries - 1: raise
            log("  retry", i + 1, e); time.sleep(3 * (i + 1))

# ---------------------------------------------------------------- KMA
def latlon_to_grid(lat, lon):
    RE, GRID, SLAT1, SLAT2, OLON, OLAT, XO, YO = 6371.00877, 5.0, 30.0, 60.0, 126.0, 38.0, 43, 136
    D = math.pi / 180.0; re = RE / GRID
    s1, s2, olon, olat = SLAT1 * D, SLAT2 * D, OLON * D, OLAT * D
    sn = math.log(math.cos(s1) / math.cos(s2)) / math.log(math.tan(math.pi * 0.25 + s2 * 0.5) / math.tan(math.pi * 0.25 + s1 * 0.5))
    sf = math.tan(math.pi * 0.25 + s1 * 0.5) ** sn * math.cos(s1) / sn
    ro = re * sf / math.tan(math.pi * 0.25 + olat * 0.5) ** sn
    ra = re * sf / math.tan(math.pi * 0.25 + lat * D * 0.5) ** sn
    th = lon * D - olon
    if th > math.pi: th -= 2 * math.pi
    if th < -math.pi: th += 2 * math.pi
    th *= sn
    return int(ra * math.sin(th) + XO + 0.5), int(ro - ra * math.cos(th) + YO + 0.5)

SKY = {"1": "맑음", "3": "구름많음", "4": "흐림"}
PTY = {"0": "", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}

def kma_base(now):
    """가장 최근 발표 시각 (발표 후 10분 지나야 제공)"""
    hours = [2, 5, 8, 11, 14, 17, 20, 23]
    t = now - dt.timedelta(minutes=12)
    for h in reversed(hours):
        if t.hour >= h: return t.strftime("%Y%m%d"), f"{h:02d}00"
    y = t - dt.timedelta(days=1); return y.strftime("%Y%m%d"), "2300"

def fetch_kma(key, now):
    bd, bt = kma_base(now)
    log(f"[KMA] base {bd} {bt}")
    url = "http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
    out = {"base_date": bd, "base_time": bt, "cities": {}}
    for name, (lat, lon) in CITIES.items():
        nx, ny = latlon_to_grid(lat, lon)
        try:
            r = get(url, {"serviceKey": key, "pageNo": 1, "numOfRows": 1500, "dataType": "JSON",
                          "base_date": bd, "base_time": bt, "nx": nx, "ny": ny})
            j = r.json()
            code = j["response"]["header"]["resultCode"]
            if code != "00": raise RuntimeError(j["response"]["header"])
            items = j["response"]["body"]["items"]["item"]
        except Exception as e:
            errors.append(f"kma {name}: {e}"); continue
        hourly, daily = {}, {}
        for it in items:
            d, t, c, v = it["fcstDate"], it["fcstTime"], it["category"], it["fcstValue"]
            if c == "TMN": daily.setdefault(d, {})["min"] = float(v)
            elif c == "TMX": daily.setdefault(d, {})["max"] = float(v)
            else: hourly.setdefault(d + t, {})[c] = v
        hrs = []
        for k in sorted(hourly):
            h = hourly[k]
            hrs.append({"time": k, "tmp": float(h["TMP"]) if "TMP" in h else None, "pop": int(h.get("POP", 0) or 0),
                        "sky": SKY.get(h.get("SKY", ""), ""), "pty": PTY.get(h.get("PTY", "0"), ""),
                        "pcp": h.get("PCP", ""), "sno": h.get("SNO", ""), "wsd": float(h["WSD"]) if "WSD" in h else None,
                        "reh": int(h["REH"]) if "REH" in h else None})
        # 일별 요약: 오전/오후 대표 하늘, 최대 강수확률
        for d in sorted({h["time"][:8] for h in hrs}):
            dh = [h for h in hrs if h["time"][:8] == d]
            dd = daily.setdefault(d, {})
            dd["pop_max"] = max(h["pop"] for h in dh)
            for part, rng in (("am", range(6, 12)), ("pm", range(12, 19))):
                ph = [h for h in dh if int(h["time"][8:10]) in rng]
                if ph:
                    rain = [h["pty"] for h in ph if h["pty"]]
                    dd[f"sky_{part}"] = rain[0] if rain else max(set(h["sky"] for h in ph), key=lambda s: sum(1 for h in ph if h["sky"] == s))
                    dd[f"pop_{part}"] = max(h["pop"] for h in ph)
        out["cities"][name] = {"grid": [nx, ny], "daily": daily, "hourly": hrs}
        time.sleep(0.3)
    return out

def fetch_air(key, now):
    url = "http://apis.data.go.kr/B552584/ArpltnInforInqireSvc/getMinuDustFrcstDspth"
    out = {}
    for code in ("PM10", "PM25"):
        res = None
        for day in (now, now - dt.timedelta(days=1)):
            r = get(url, {"serviceKey": key, "returnType": "json", "numOfRows": 100, "pageNo": 1,
                          "searchDate": day.strftime("%Y-%m-%d"), "InformCode": code})
            items = [x for x in (r.json()["response"]["body"]["items"] or []) if x.get("informCode") == code]
            if items: res = items; break
        if not res: errors.append(f"air {code}: no items"); continue
        res.sort(key=lambda x: x.get("dataTime", ""), reverse=True)
        latest_time = res[0]["dataTime"]
        days = {}
        for it in res:
            if it["dataTime"] != latest_time: continue
            grades = {}
            for part in (it.get("informGrade") or "").split(","):
                if ":" in part:
                    k, v = part.split(":", 1); grades[k.strip()] = v.strip()
            days[it["informData"]] = {"grades": grades, "overall": it.get("informOverall", ""), "cause": it.get("informCause", "")}
        out[code] = {"data_time": latest_time, "days": days}
    return out

# ---------------------------------------------------------------- models
def nearest_values(path, want):
    """GRIB 파일에서 도시별 최근접 격자값. want: set of shortNames. returns list of (shortName, stepRange, {city: val})"""
    import eccodes as ec
    rows = []
    with open(path, "rb") as f:
        while True:
            gid = ec.codes_grib_new_from_file(f)
            if gid is None: break
            try:
                sn = ec.codes_get(gid, "shortName")
                if sn not in want: continue
                step = ec.codes_get(gid, "stepRange")
                vals = {}
                for name, (lat, lon) in CITIES.items():
                    n = ec.codes_grib_find_nearest(gid, lat, lon)[0]
                    vals[name] = n["value"]
                rows.append((sn, str(step), vals))
            finally:
                ec.codes_release(gid)
    return rows

def daily_from_series(temp, accum3, run_time):
    """temp: {valid_dt_utc: {city: K}}, accum3: {valid_dt_utc(end): {city: mm over 3h}} -> {city: [{date,tmin,tmax,precip}]}"""
    out = {c: {} for c in CITIES}
    for vt, vals in temp.items():
        d = vt.astimezone(KST).strftime("%Y%m%d")
        for c, v in vals.items():
            e = out[c].setdefault(d, {"t": [], "p": 0.0, "np": 0})
            e["t"].append(v - 273.15 if v > 150 else v)
    for vt, vals in accum3.items():
        d = (vt - dt.timedelta(minutes=1)).astimezone(KST).strftime("%Y%m%d")  # 끝시각 기준 -> 구간이 속한 날
        for c, v in vals.items():
            e = out[c].setdefault(d, {"t": [], "p": 0.0, "np": 0})
            e["p"] += max(0.0, v); e["np"] += 1
    res = {}
    for c, days in out.items():
        res[c] = []
        for d in sorted(days):
            e = days[d]
            if len(e["t"]) < 8 or e["np"] < 8: continue  # 하루 전체(3시간x8) 커버된 날만
            res[c].append({"date": d, "tmin": round(min(e["t"]), 1), "tmax": round(max(e["t"]), 1), "precip": round(e["p"], 1)})
    return res

def fetch_ecmwf():
    from ecmwf.opendata import Client
    cl = Client(source="ecmwf")
    steps = list(range(0, 145, 3))
    target = os.path.join(TMP, "ecmwf.grib2")
    res = cl.retrieve(type="fc", param=["2t", "tp"], step=steps, target=target)
    run = res.datetime.replace(tzinfo=UTC)
    log(f"[ECMWF] run {run}")
    rows = nearest_values(target, {"2t", "tp"})
    temp, tp = {}, {}
    for sn, step, vals in rows:
        end = int(step.split("-")[-1])
        vt = run + dt.timedelta(hours=end)
        if sn == "2t": temp[vt] = vals
        else: tp[end] = {c: v * 1000.0 for c, v in vals.items()}  # m -> mm, accumulated from 0
    accum3 = {}
    for end in sorted(tp):
        if end - 3 in tp:
            accum3[run + dt.timedelta(hours=end)] = {c: tp[end][c] - tp[end - 3][c] for c in CITIES}
    return {"run": run.isoformat(), "note": "IFS HRES 0.25deg open data, nearest grid point, 3-hourly 2t samples",
            "cities": daily_from_series(temp, accum3, run)}

def fetch_gfs(now_utc):
    base = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
    # 사용할 수 있는 가장 최근 런 찾기 (공개까지 ~4시간)
    run = None
    for back in range(0, 30, 6):
        cand = now_utc - dt.timedelta(hours=4 + back)
        c = cand.replace(hour=cand.hour // 6 * 6, minute=0, second=0, microsecond=0)
        try:
            r = get(base, {"dir": f"/gfs.{c:%Y%m%d}/{c:%H}/atmos", "file": f"gfs.t{c:%H}z.pgrb2.0p25.f144",
                           "var_TMP": "on", "lev_2_m_above_ground": "on", "subregion": "", "leftlon": 124,
                           "rightlon": 132, "toplat": 39, "bottomlat": 33}, tries=1, timeout=30)
            if r.content[:4] == b"GRIB": run = c; break
        except Exception as e:
            log("  gfs run not ready:", c, e)
    if run is None: raise RuntimeError("no GFS run available")
    log(f"[GFS] run {run}")
    path = os.path.join(TMP, "gfs.grib2")
    with open(path, "wb") as f:
        for fh in range(3, 145, 3):
            r = get(base, {"dir": f"/gfs.{run:%Y%m%d}/{run:%H}/atmos", "file": f"gfs.t{run:%H}z.pgrb2.0p25.f{fh:03d}",
                           "var_TMP": "on", "var_APCP": "on", "lev_2_m_above_ground": "on", "lev_surface": "on",
                           "subregion": "", "leftlon": 124, "rightlon": 132, "toplat": 39, "bottomlat": 33})
            f.write(r.content); time.sleep(0.4)
    rows = nearest_values(path, {"2t", "t2m", "tp", "apcp", "unknown"})
    temp, acc = {}, {}
    for sn, step, vals in rows:
        if sn in ("2t", "t2m"):
            end = int(step.split("-")[-1]); temp[run + dt.timedelta(hours=end)] = vals
        else:
            a, b = (int(x) for x in step.split("-")) if "-" in step else (0, int(step))
            acc[(a, b)] = vals
    accum3 = {}
    for (a, b), vals in acc.items():
        if b - a == 3: accum3[run + dt.timedelta(hours=b)] = vals
        elif (a, b - 3) in acc:
            prev = acc[(a, b - 3)]
            accum3[run + dt.timedelta(hours=b)] = {c: vals[c] - prev[c] for c in CITIES}
    return {"run": run.isoformat(), "note": "GFS 0.25deg, nearest grid point, 3-hourly 2m temp samples",
            "cities": daily_from_series(temp, accum3, run)}

# ---------------------------------------------------------------- main
def main():
    now = dt.datetime.now(KST)
    key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    log(f"DATA_GO_KR_KEY: {'있음 (' + str(len(key)) + '자)' if key else '없음'}")
    data = {"generated_at": now.isoformat(), "kma": None, "air": None, "models": {}, "errors": errors}
    steps = []
    if key:
        steps += [("kma", lambda: fetch_kma(key, now)), ("air", lambda: fetch_air(key, now))]
    else:
        errors.append("DATA_GO_KR_KEY 없음: 기상청/에어코리아 건너뜀")
    steps += [("ecmwf", fetch_ecmwf), ("gfs", lambda: fetch_gfs(now.astimezone(UTC)))]
    for name, fn in steps:
        t0 = time.time()
        try:
            v = fn()
            if name in ("ecmwf", "gfs"): data["models"][name] = v
            else: data[name] = v
            log(f"[{name}] ok {time.time() - t0:.0f}s")
        except Exception as e:
            traceback.print_exc(); errors.append(f"{name}: {type(e).__name__}: {e}")
    s = json.dumps(data, ensure_ascii=False, indent=1)
    open(os.path.join(OUT_DIR, "latest.json"), "w", encoding="utf-8").write(s)
    os.makedirs(os.path.join(OUT_DIR, "archive"), exist_ok=True)
    open(os.path.join(OUT_DIR, "archive", now.strftime("%Y%m%d") + ".json"), "w", encoding="utf-8").write(s)
    # 오래된 아카이브 정리 (30일)
    for fn in sorted(os.listdir(os.path.join(OUT_DIR, "archive")))[:-30]:
        os.remove(os.path.join(OUT_DIR, "archive", fn))
    log("errors:", errors)

if __name__ == "__main__":
    main()
