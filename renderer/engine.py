#!/usr/bin/env python3
"""날씨 쇼츠 렌더러 (1080x1920, 30fps, 무음 mp4 + 더빙대본 + srt)

사용법:
  python3 engine.py spec.json out            # 영상 렌더링
  python3 engine.py spec.json out --preview  # 장면별 대표 프레임 1장씩 -> out/preview.png

spec.json:
{
  "brand": "날씨삼국지",
  "date_label": "10.28 (수)",
  "scenes": [ {"type": ..., "lines": [{"sub": "자막 {강조}", "tts": "읽기용 문장."}], ...}, ... ]
}
장면 type:
  hook      : headline(큰 문장, {강조}), icon(sun|cloud|partly|rain|snow|shower), note(작은 글씨)
  map_temp  : title, show("min"|"max"), cities{도시: {min, max, sky}}
              도시 이름: 서울 인천 춘천 강릉 청주 대전 세종 전주 광주 대구 부산 울산 제주 울릉도 독도 백령도 안동 포항 목포 여수 창원 수원
  map_region: title, legend_title, regions{지역: 등급}  등급: 좋음|보통|나쁨|매우나쁨 (또는 rain: 0~100 강수확률은 type rain_map)
  rain_map  : title, regions{지역: 강수확률0~100}
              지역 키: 서울 인천 경기 강원 충북 충남 세종 대전 전북 전남 광주 경북 경남 대구 울산 부산 제주
  timeline  : title, city, hours[{t:"06시", pop:20, sky:"cloud", temp:3}]  (4~8개)
  models    : title, unit, note, items[{name:"유럽", sub:"ECMWF", value:12}] (2~4개)
  tip       : headline({강조}), icon(umbrella|coat|mask|sun|...), lines
  outro     : sources["기상청", ...], cta

썸네일 (spec 최상위 "thumb", 렌더링 때 out/thumbnail.png 1080x1920 생성, --thumb 로 썸네일만):
  "thumb": {"theme": "sunny|cold|rain|snow|hot|dust|cloudy", "top": "춘천 아침",
            "big": "-2°", "bottom": "출근길 {꽁꽁}", "icon": "sun|partly|cloud|rain|shower|snow|umbrella|coat|mask"}
  top: 큰 숫자 위 짧은 문구(8자 안팎), big: 제일 큰 숫자/단어(5자 이내), bottom: 결론 한 줄(10자 안팎, {강조} 1곳)
"""
import json, math, os, re, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

W, H, FPS = 1080, 1920, 30
HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = "/usr/share/fonts/opentype/noto/"

def font(size, weight="Bold"):
    return ImageFont.truetype(FONT_DIR + f"NotoSansCJK-{weight}.ttc", size, index=1)  # KR

# ---------- palette ----------
BG_TOP = (10, 24, 48); BG_BOT = (18, 58, 104)
LAND = (226, 236, 246); LAND_EDGE = (150, 178, 210)
SEA_GLOW = (40, 90, 150)
WHITE = (255, 255, 255); INK = (18, 30, 50)
HL = (255, 214, 64)
GRADE = {"좋음": (52, 140, 255), "보통": (46, 196, 120), "나쁨": (255, 160, 40), "매우나쁨": (235, 64, 64)}

def temp_color(t):
    stops = [(-15, (90, 70, 200)), (-5, (60, 120, 240)), (5, (40, 180, 230)), (12, (60, 200, 140)),
             (20, (250, 200, 50)), (27, (250, 130, 40)), (34, (225, 50, 50))]
    if t <= stops[0][0]: return stops[0][1]
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if t <= b:
            k = (t - a) / (b - a)
            return tuple(int(ca[i] + (cb[i] - ca[i]) * k) for i in range(3))
    return stops[-1][1]

def pop_color(p):
    p = max(0, min(100, p)) / 100
    a, b = (70, 100, 140), (40, 150, 255)
    if p >= 0.6: a, b, p = (40, 150, 255), (30, 70, 230), (p - 0.6) / 0.4
    else: p = p / 0.6
    return tuple(int(a[i] + (b[i] - a[i]) * p) for i in range(3))

# ---------- easing ----------
def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease_out(x): x = clamp(x); return 1 - (1 - x) ** 3
def ease_back(x):
    x = clamp(x); c1 = 1.70158; c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2

# ---------- map ----------
LON0, LON1, LAT0, LAT1 = 125.0, 129.75, 33.05, 38.65
COSL = math.cos(math.radians(36))
MAP_Y = 290
SC = 1000 / (LAT1 - LAT0)
MAP_H = 1000
MAP_X = (W - (LON1 - LON0) * COSL * SC) / 2 - 60
INSET = (845, 300, 1050, 500)  # 울릉도·독도 inset box
def proj(lon, lat):
    if lon > 130.3:  # inset
        x0, y0, x1, y1 = INSET
        return (x0 + 20 + (lon - 130.75) / (131.95 - 130.75) * (x1 - x0 - 40), y0 + 30 + (37.62 - lat) / (37.62 - 37.15) * (y1 - y0 - 70))
    return (MAP_X + (lon - LON0) * COSL * SC, MAP_Y + (LAT1 - lat) * SC)

PROV_KEY = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
            "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원도": "강원",
            "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전라남도": "전남", "경상북도": "경북",
            "경상남도": "경남", "제주특별자치도": "제주"}
PROVS = {}
def load_map():
    d = json.load(open(os.path.join(HERE, "assets", "prov.json"), encoding="utf-8"))
    for f in d["features"]:
        g = f["geometry"]; polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        out = []
        for poly in polys:
            ring = poly[0]
            if max(p[0] for p in ring) < LON0 - 0.05: continue  # 백령도 등 지도 밖
            out.append([proj(*p) for p in ring])
        PROVS[PROV_KEY[f["properties"]["name"]]] = out
load_map()
REGION_ALIAS = {"강원영서": "강원", "강원영동": "강원", "경기북부": "경기", "경기남부": "경기", "강원도": "강원", "경기도": "경기",
                "충청북도": "충북", "충청남도": "충남", "전라북도": "전북", "전북특별자치도": "전북", "전라남도": "전남",
                "경상북도": "경북", "경상남도": "경남", "강원특별자치도": "강원", "제주도": "제주", "영동": "강원", "영서": "강원"}

# lon, lat, marker center in px (None = at the city point)
CITY = {
    "서울": (126.98, 37.57, (330, 455)), "인천": (126.70, 37.46, (205, 610)), "수원": (127.03, 37.26, (330, 590)),
    "춘천": (127.73, 37.88, (545, 375)), "강릉": (128.90, 37.75, (770, 420)), "홍성": (126.66, 36.60, (280, 680)),
    "청주": (127.49, 36.64, (595, 610)), "세종": (127.29, 36.48, (350, 690)), "대전": (127.38, 36.35, (390, 720)),
    "안동": (128.73, 36.57, (770, 610)), "전주": (127.15, 35.82, (500, 830)), "광주": (126.85, 35.16, (320, 930)),
    "목포": (126.39, 34.81, (260, 1030)), "여수": (127.66, 34.76, (540, 1050)), "대구": (128.60, 35.87, (690, 790)),
    "포항": (129.36, 36.02, (850, 740)), "울산": (129.31, 35.54, (880, 860)), "창원": (128.68, 35.23, (640, 990)),
    "부산": (129.08, 35.18, (800, 990)), "제주": (126.53, 33.50, (520, 1200)),
    "울릉도": (130.87, 37.49, (915, 385)), "독도": (131.87, 37.24, None),
}

def gradient_bg():
    y = np.linspace(0, 1, H)[:, None, None]
    top = np.array(BG_TOP)[None, None, :]; bot = np.array(BG_BOT)[None, None, :]
    arr = (top * (1 - y) + bot * y).repeat(W, axis=1)
    # soft glow behind map
    yy, xx = np.mgrid[0:H, 0:W]
    g = np.exp(-(((xx - 540) / 520) ** 2 + ((yy - 760) / 600) ** 2))[:, :, None]
    arr = arr + g * (np.array(SEA_GLOW) - np.array(BG_BOT)) * 0.6
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").convert("RGBA")

_BG = None
def bg():
    global _BG
    if _BG is None: _BG = gradient_bg()
    return _BG.copy()

def map_layer(fills=None, ss=2):
    """returns RGBA image of the land; fills: {region: rgb}"""
    big = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    for key, polys in PROVS.items():
        col = (fills or {}).get(key, LAND)
        for poly in polys:
            pts = [(x * ss, y * ss) for x, y in poly]
            d.polygon(pts, fill=col + (255,))
            d.line(pts + [pts[0]], fill=LAND_EDGE + (255,), width=2 * ss)
    x0, y0, x1, y1 = INSET
    d.rounded_rectangle([x0 * ss, y0 * ss, x1 * ss, y1 * ss], radius=18 * ss, outline=(120, 150, 190, 255), width=2 * ss)
    x, y = proj(131.87, 37.24)
    d.ellipse([(x - 6) * ss, (y - 6) * ss, (x + 6) * ss, (y + 6) * ss], fill=(fills or {}).get("독도", LAND) + (255,))
    img = big.resize((W, H), Image.LANCZOS)
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    a = img.split()[3].filter(ImageFilter.GaussianBlur(14))
    shadow.putalpha(a.point(lambda v: int(v * 0.55)))
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    out.alpha_composite(shadow, (8, 14)); out.alpha_composite(img)
    return out

_MAP_CACHE = {}
def cached_map(fills_key, fills):
    if fills_key not in _MAP_CACHE: _MAP_CACHE[fills_key] = map_layer(fills)
    return _MAP_CACHE[fills_key]

# ---------- icons (drawn) ----------
def draw_icon(kind, size):
    ss = 3; S = size * ss
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    def sun(cx, cy, r):
        for i in range(8):
            a = i * math.pi / 4
            d.line([(cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35), (cx + math.cos(a) * r * 1.75, cy + math.sin(a) * r * 1.75)],
                   fill=(255, 196, 40, 255), width=int(r * 0.28))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 205, 50, 255))
    def cloud(cx, cy, w, col=(245, 248, 252, 255), edge=(170, 185, 205, 255)):
        h = w * 0.45
        parts = [(cx - w * 0.28, cy + h * 0.05, w * 0.24), (cx + w * 0.02, cy - h * 0.25, w * 0.30), (cx + w * 0.30, cy + h * 0.05, w * 0.22)]
        for (x, y, r) in parts: d.ellipse([x - r - 3, y - r - 3, x + r + 3, y + r + 3], fill=edge)
        d.rounded_rectangle([cx - w * 0.5 - 3, cy - 3, cx + w * 0.5 + 3, cy + h * 0.5 + 3], radius=h * 0.25, fill=edge)
        for (x, y, r) in parts: d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        d.rounded_rectangle([cx - w * 0.5, cy, cx + w * 0.5, cy + h * 0.5], radius=h * 0.25, fill=col)
    k = kind
    if k == "sun": sun(S / 2, S / 2, S * 0.24)
    elif k == "partly": sun(S * 0.38, S * 0.36, S * 0.17); cloud(S * 0.56, S * 0.56, S * 0.72)
    elif k == "cloud": cloud(S * 0.5, S * 0.45, S * 0.86, (225, 232, 242, 255))
    elif k in ("rain", "shower", "snow", "sleet"):
        if k == "shower": sun(S * 0.36, S * 0.28, S * 0.15)
        cloud(S * 0.5, S * 0.36, S * 0.86, (200, 212, 228, 255), (140, 158, 182, 255))
        for i, x in enumerate([0.30, 0.50, 0.70]):
            X = S * x; Y = S * 0.74
            if k == "snow" or (k == "sleet" and i == 1):
                r = S * 0.05; d.ellipse([X - r, Y - r, X + r, Y + r], fill=(255, 255, 255, 255))
            else:
                d.line([(X + S * 0.04, Y - S * 0.08), (X - S * 0.03, Y + S * 0.08)], fill=(60, 150, 255, 255), width=int(S * 0.055))
    elif k == "umbrella":
        cx, cy, r = S / 2, S * 0.45, S * 0.4
        d.pieslice([cx - r, cy - r, cx + r, cy + r], 180, 360, fill=(60, 140, 255, 255))
        d.line([(cx, cy), (cx, S * 0.82)], fill=(230, 230, 240, 255), width=int(S * 0.05))
        d.arc([cx - S * 0.12, S * 0.72, cx, S * 0.9], 0, 180, fill=(230, 230, 240, 255), width=int(S * 0.05))
    elif k == "coat":
        c = (120, 80, 200, 255)
        d.polygon([(S * .3, S * .15), (S * .7, S * .15), (S * .9, S * .4), (S * .8, S * .48), (S * .72, S * .38), (S * .72, S * .88),
                   (S * .28, S * .88), (S * .28, S * .38), (S * .2, S * .48), (S * .1, S * .4)], fill=c)
        d.polygon([(S * .4, S * .15), (S * .5, S * .35), (S * .6, S * .15)], fill=(240, 230, 255, 255))
        d.line([(S * .5, S * .35), (S * .5, S * .88)], fill=(80, 50, 150, 255), width=int(S * .03))
    elif k == "mask":
        d.rounded_rectangle([S * .2, S * .32, S * .8, S * .7], radius=S * .1, fill=(245, 248, 252, 255))
        for y in (0.44, 0.52, 0.6): d.line([(S * .26, S * y), (S * .74, S * y)], fill=(190, 200, 215, 255), width=int(S * .02))
        d.arc([S * .02, S * .3, S * .3, S * .62], 90, 270, fill=(220, 225, 235, 255), width=int(S * .03))
        d.arc([S * .7, S * .3, S * .98, S * .62], 270, 90, fill=(220, 225, 235, 255), width=int(S * .03))
    else:
        sun(S / 2, S / 2, S * 0.24)
    return im.resize((size, size), Image.LANCZOS)

_ICON = {}
def icon(kind, size):
    key = (kind, size)
    if key not in _ICON: _ICON[key] = draw_icon(kind, size)
    return _ICON[key]

# ---------- text ----------
def parse_hl(s):
    """'abc {def} g' -> [(text, highlighted)]"""
    out = []
    for i, part in enumerate(re.split(r"[{}]", s)):
        if part: out.append((part, i % 2 == 1))
    return out

def text_w(d, s, f): return d.textlength(s, font=f)

def wrap_hl(d, s, f, maxw):
    """word wrap keeping highlight segments; returns list of lines (list of segments)"""
    words = []
    for seg, h in parse_hl(s):
        toks = re.split(r"(\s+)", seg)
        for t in toks:
            if t: words.append((t, h))
    lines, cur, cw = [], [], 0
    for t, h in words:
        tw = text_w(d, t, f)
        if cur and cw + tw > maxw and not t.isspace():
            lines.append(cur); cur, cw = [], 0
            if t.isspace(): continue
        cur.append((t, h)); cw += tw
    if cur: lines.append(cur)
    return [[(t, h) for t, h in ln] for ln in lines]

def draw_hl_lines(img, lines, f, cx, y, color=WHITE, hl=HL, line_gap=1.25, stroke=0, mark=False):
    d = ImageDraw.Draw(img)
    asc = f.getmetrics()[0]; lh = int(f.size * line_gap)
    for ln in lines:
        # strip trailing space
        while ln and ln[-1][0].isspace(): ln = ln[:-1]
        while ln and ln[0][0].isspace(): ln = ln[1:]
        tw = sum(text_w(d, t, f) for t, _ in ln)
        x = cx - tw / 2
        for t, h in ln:
            w = text_w(d, t, f)
            if h and mark and not t.isspace():
                d.rounded_rectangle([x - 6, y + f.size * 0.12, x + w + 6, y + f.size * 1.3], radius=10, fill=hl)
                d.text((x, y), t, font=f, fill=INK)
            else:
                d.text((x, y), t, font=f, fill=(hl if h else color), stroke_width=stroke, stroke_fill=(0, 0, 0))
            x += w
        y += lh
    return y

# ---------- common chrome ----------
def header(img, spec, title, t_in):
    d = ImageDraw.Draw(img)
    a = ease_out(t_in / 0.4)
    # brand chip
    fb = font(40, "Black")
    brand = spec.get("brand", "날씨")
    bw = text_w(d, brand, fb) + 44
    d.rounded_rectangle([50, 130, 50 + bw, 196], radius=33, fill=(255, 214, 64))
    d.text((72, 136), brand, font=fb, fill=INK)
    fd = font(48, "Black")
    d.text((50 + bw + 22, 132), spec.get("date_label", ""), font=fd, fill=WHITE)
    if title:
        ft = font(66, "Black")
        layer = Image.new("RGBA", (W, 120), (0, 0, 0, 0)); dl = ImageDraw.Draw(layer)
        draw_hl_lines(layer, wrap_hl(dl, title, ft, 980), ft, W / 2 - 0, 8)
        img.alpha_composite(layer.point(lambda v: v) if a >= 1 else fade(layer, a), (int(-30 * (1 - a)) + 0, 210))

def fade(layer, a):
    r, g, b, al = layer.split()
    return Image.merge("RGBA", (r, g, b, al.point(lambda v: int(v * a))))

def subtitle(img, sub, t_line):
    if not sub: return
    a = ease_out(t_line / 0.18)
    f = font(58, "Black")
    layer = Image.new("RGBA", (W, 420), (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
    lines = wrap_hl(d, sub, f, 880)[:2]
    lh = int(58 * 1.32)
    h = lh * len(lines) + 40
    y0 = 0
    d.rounded_rectangle([60, y0, W - 60, y0 + h], radius=28, fill=(6, 14, 30, 215))
    draw_hl_lines(layer, lines, f, W / 2, y0 + 16, line_gap=1.32, mark=True)
    img.alpha_composite(fade(layer, a), (0, 1330 + int(20 * (1 - a))))

# ---------- scenes ----------
def sc_hook(img, sc, spec, t, T):
    header(img, spec, None, t)
    s = ease_back(t / 0.6)
    sz = 380
    ic = icon(sc.get("icon", "sun"), sz)
    bob = math.sin(t * 2.2) * 10
    if s > 0.01:
        k = max(1, int(sz * s)); ic2 = ic.resize((k, k), Image.LANCZOS)
        img.alpha_composite(ic2, (int(W / 2 - k / 2), int(560 - k / 2 + bob)))
    f = font(104, "Black")
    a = ease_out((t - 0.25) / 0.45)
    layer = Image.new("RGBA", (W, 500), (0, 0, 0, 0)); d = ImageDraw.Draw(layer)
    y = draw_hl_lines(layer, wrap_hl(d, sc.get("headline", ""), f, 960), f, W / 2, 0, line_gap=1.2)
    if sc.get("note"):
        fn = font(40, "Medium"); nw = text_w(d, sc["note"], fn)
        d.text((W / 2 - nw / 2, y + 20), sc["note"], font=fn, fill=(190, 210, 235))
    img.alpha_composite(fade(layer, a), (0, 820 + int(40 * (1 - a))))

def marker(img, cx, cy, name, temp, sky, s):
    if s <= 0.01: return
    R = 50; S = 190
    lay = Image.new("RGBA", (S, S), (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
    col = temp_color(temp); c = S / 2; cy0 = c + 4
    d.ellipse([c - R - 5, cy0 - R - 5, c + R + 5, cy0 + R + 5], fill=(255, 255, 255, 255))
    d.ellipse([c - R, cy0 - R, c + R, cy0 + R], fill=col + (255,))
    ft = font(46 if abs(temp) < 10 else 42, "Black"); txt = f"{temp:g}°"
    tw = d.textlength(txt, font=ft)
    d.text((c - tw / 2 + 6, cy0 - 30), txt, font=ft, fill=WHITE, stroke_width=2, stroke_fill=tuple(int(v * 0.6) for v in col))
    ic = icon(sky, 74); lay.alpha_composite(ic, (int(c - R - 30), int(cy0 - R - 34)))
    fn = font(30, "Black"); nw = d.textlength(name, font=fn)
    d.text((c - nw / 2, cy0 + R + 6), name, font=fn, fill=WHITE, stroke_width=4, stroke_fill=(10, 24, 48))
    if s != 1:
        k = max(0.05, s); lay = lay.resize((max(1, int(S * k)), max(1, int(S * k))), Image.LANCZOS)
    img.alpha_composite(lay, (int(cx - lay.width / 2), int(cy - lay.height / 2 + 10)))

def city_xy(name):
    lon, lat, pos = CITY[name]
    x, y = proj(lon, lat)
    px, py = pos if pos else (x, y - 20)
    return x, y, px, py

def map_in(img, mp, t):
    a = ease_out(t / 0.5)
    img.alpha_composite(fade(mp, a) if a < 1 else mp, (0, int(40 * (1 - a))))

def sc_map_temp(img, sc, spec, t, T):
    header(img, spec, sc.get("title"), t)
    map_in(img, cached_map("plain", None), t)
    d = ImageDraw.Draw(img)
    key = sc.get("show", "max")
    order = sorted([kv for kv in sc["cities"].items() if kv[0] in CITY], key=lambda kv: -CITY[kv[0]][1])
    for i, (name, v) in enumerate(order):
        x, y, px, py = city_xy(name)
        s = ease_back((t - 0.45 - i * 0.07) / 0.35)
        if s > 0.01:
            if math.hypot(px - x, py - y) > 60:
                d.line([(x, y), (px, py)], fill=(255, 255, 255, 170), width=3)
            d.ellipse([x - 7, y - 7, x + 7, y + 7], fill=(20, 40, 70), outline=WHITE, width=3)
    for i, (name, v) in enumerate(order):
        x, y, px, py = city_xy(name)
        marker(img, px, py, name, v[key], v.get("sky", "sun"), ease_back((t - 0.45 - i * 0.07) / 0.35))
    fl = font(36, "Bold")
    lbl = "아침 최저기온" if key == "min" else "낮 최고기온"
    d.rounded_rectangle([50, 1262, 50 + text_w(d, lbl, fl) + 40, 1318], radius=28, fill=(255, 255, 255, 50))
    d.text((70, 1266), lbl, font=fl, fill=WHITE)

def region_fills(regions, conv):
    fills = {}
    for k, v in regions.items():
        k2 = REGION_ALIAS.get(k, k)
        if k2 in PROVS: fills[k2] = conv(v)
    if "경북" in fills: fills["독도"] = fills["경북"]
    return fills

def wave_reveal(img, colored, plain, t, start=0.5, dur=0.9):
    """reveal colored map from north to south"""
    p = ease_out((t - start) / dur)
    if p <= 0: return
    if p >= 1: img.alpha_composite(colored); return
    cut = int(MAP_Y + (MAP_H + 60) * p)
    top = colored.crop((0, 0, W, cut))
    img.alpha_composite(top, (0, 0))

def legend(img, items, y=1262):
    d = ImageDraw.Draw(img); f = font(34, "Bold")
    tot = sum(52 + text_w(d, lbl, f) + 36 for lbl, _ in items) - 36
    x = W - 50 - tot  # 오른쪽 정렬 (제주도와 겹치지 않게)
    for lbl, col in items:
        d.rounded_rectangle([x, y + 8, x + 40, y + 48], radius=10, fill=col)
        d.text((x + 52, y + 4), lbl, font=f, fill=WHITE)
        x += 52 + text_w(d, lbl, f) + 36

def sc_map_region(img, sc, spec, t, T):
    header(img, spec, sc.get("title"), t)
    plain = cached_map("plain", None)
    map_in(img, plain, t)
    regs = sc["regions"]
    key = "grade:" + json.dumps(regs, sort_keys=True, ensure_ascii=False)
    colored = cached_map(key, region_fills(regs, lambda g: GRADE.get(g, LAND)))
    wave_reveal(img, colored, plain, t)
    legend(img, [(g, c) for g, c in GRADE.items()])

def sc_rain_map(img, sc, spec, t, T):
    header(img, spec, sc.get("title"), t)
    plain = cached_map("plain", None)
    map_in(img, plain, t)
    regs = sc["regions"]
    key = "pop:" + json.dumps(regs, sort_keys=True, ensure_ascii=False)
    colored = cached_map(key, region_fills(regs, lambda p: pop_color(float(p))))
    wave_reveal(img, colored, plain, t)
    # % labels on biggest provinces
    d = ImageDraw.Draw(img); f = font(38, "Black")
    if t > 1.2:
        a = ease_out((t - 1.2) / 0.4)
        for k, v in regs.items():
            k2 = REGION_ALIAS.get(k, k)
            if k2 not in PROVS or k2 in ("서울", "인천", "대전", "광주", "대구", "울산", "부산", "세종"): continue
            poly = max(PROVS[k2], key=len); xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            s = f"{int(float(v))}%"; tw = text_w(d, s, f)
            d.text((cx - tw / 2, cy - 24), s, font=f, fill=(255, 255, 255, int(255 * a)), stroke_width=4, stroke_fill=(10, 30, 60))
    legend(img, [("0~30%", pop_color(15)), ("30~60%", pop_color(45)), ("60%+", pop_color(80))])

def sc_timeline(img, sc, spec, t, T):
    header(img, spec, sc.get("title"), t)
    d = ImageDraw.Draw(img)
    hrs = sc["hours"]; n = len(hrs)
    fcity = font(44, "Bold")
    d.text((60, 330), "📍 " .replace("📍 ", "") + sc.get("city", ""), font=fcity, fill=(200, 220, 245))
    x0, x1 = 70, 1010; colw = (x1 - x0) / n
    base = 1180; maxh = 460
    for i, h in enumerate(hrs):
        cx = x0 + colw * (i + 0.5)
        a = ease_out((t - 0.3 - i * 0.12) / 0.5)
        if a <= 0: continue
        ic = icon(h.get("sky", "cloud"), int(colw * 0.8));
        img.alpha_composite(fade(ic, a), (int(cx - ic.width / 2), 420))
        ft = font(44, "Black")
        if "temp" in h:
            s = f"{h['temp']:g}°"; d.text((cx - text_w(d, s, ft) / 2, 420 + ic.height + 4), s, font=ft, fill=WHITE)
        pop = float(h.get("pop", 0)); bh = maxh * 0.75 * pop / 100 * a
        d.rounded_rectangle([cx - colw * 0.3, base - maxh * 0.75, cx + colw * 0.3, base], radius=16, fill=(255, 255, 255, 25))
        if bh > 2: d.rounded_rectangle([cx - colw * 0.3, base - bh, cx + colw * 0.3, base], radius=16, fill=pop_color(pop))
        fp = font(36, "Black"); s = f"{int(pop * a)}%"
        d.text((cx - text_w(d, s, fp) / 2, base - bh - 50), s, font=fp, fill=WHITE)
        fh = font(34, "Bold"); s = h["t"]
        d.text((cx - text_w(d, s, fh) / 2, base + 14), s, font=fh, fill=(190, 210, 235))
    d.text((70, 1260), "막대 = 강수확률", font=font(30, "Medium"), fill=(160, 185, 215))

MODEL_COL = [(64, 140, 255), (255, 110, 80), (60, 200, 140), (200, 120, 255)]
def sc_models(img, sc, spec, t, T):
    header(img, spec, sc.get("title"), t)
    d = ImageDraw.Draw(img)
    items = sc["items"]; n = len(items)
    vmax = max([abs(float(i["value"])) for i in items] + [1e-6])
    unit = sc.get("unit", "")
    x0, x1 = 90, 990; colw = (x1 - x0) / n
    base = 1100; maxh = 540
    fv = font(64, "Black"); fn = font(48, "Black"); fs = font(30, "Bold")
    for i, it in enumerate(items):
        cx = x0 + colw * (i + 0.5); col = tuple(it.get("color", MODEL_COL[i % 4]))
        a = ease_out((t - 0.4 - i * 0.25) / 0.7)
        v = float(it["value"]); bh = max(8, maxh * abs(v) / vmax) * a
        d.rounded_rectangle([cx - colw * 0.32, base - maxh, cx + colw * 0.32, base], radius=24, fill=(255, 255, 255, 18))
        if a > 0:
            d.rounded_rectangle([cx - colw * 0.32, base - bh, cx + colw * 0.32, base], radius=24, fill=col)
            s = f"{v * a:.{it.get('decimals', 0)}f}{unit}"
            d.text((cx - text_w(d, s, fv) / 2, base - bh - 86), s, font=fv, fill=WHITE)
        d.text((cx - text_w(d, it["name"], fn) / 2, base + 16), it["name"], font=fn, fill=WHITE)
        if it.get("sub"):
            d.text((cx - text_w(d, it["sub"], fs) / 2, base + 80), it["sub"], font=fs, fill=(170, 195, 225))
    if sc.get("note"):
        fnt = font(32, "Medium"); d.text((W / 2 - text_w(d, sc["note"], fnt) / 2, 1268), sc["note"], font=fnt, fill=(170, 195, 225))
    # VS badges
    if n >= 2 and t > 0.3:
        fvs = font(40, "Black")
        for i in range(n - 1):
            cx = x0 + colw * (i + 1)
            d.ellipse([cx - 36, 900 - 36, cx + 36, 900 + 36], fill=(255, 214, 64))
            d.text((cx - text_w(d, "VS", fvs) / 2, 900 - 30), "VS", font=fvs, fill=INK)

def sc_tip(img, sc, spec, t, T):
    header(img, spec, None, t)
    d = ImageDraw.Draw(img)
    fl = font(48, "Black"); lbl = sc.get("label", "오늘의 한마디")
    a0 = ease_out(t / 0.4)
    lw = text_w(d, lbl, fl) + 60
    d.rounded_rectangle([W / 2 - lw / 2, 330, W / 2 + lw / 2, 410], radius=40, fill=(255, 255, 255, int(40 * a0)))
    d.text((W / 2 - text_w(d, lbl, fl) / 2, 340), lbl, font=fl, fill=(255, 255, 255, int(255 * a0)))
    s = ease_back((t - 0.15) / 0.6)
    if s > 0.01:
        k = max(1, int(320 * s)); ic = icon(sc.get("icon", "umbrella"), 320).resize((k, k), Image.LANCZOS)
        img.alpha_composite(ic, (int(W / 2 - k / 2), int(640 - k / 2 + math.sin(t * 2) * 8)))
    f = font(92, "Black"); a = ease_out((t - 0.4) / 0.5)
    layer = Image.new("RGBA", (W, 420), (0, 0, 0, 0)); dl = ImageDraw.Draw(layer)
    draw_hl_lines(layer, wrap_hl(dl, sc.get("headline", ""), f, 940), f, W / 2, 0, line_gap=1.22)
    img.alpha_composite(fade(layer, a), (0, 860))

def sc_outro(img, sc, spec, t, T):
    d = ImageDraw.Draw(img)
    a = ease_out(t / 0.5)
    fb = font(110, "Black"); brand = spec.get("brand", "")
    d.text((W / 2 - text_w(d, brand, fb) / 2, 560 - 30 * (1 - a)), brand, font=fb, fill=(255, 214, 64, int(255 * a)))
    fc = font(54, "Bold"); cta = sc.get("cta", "매일 아침 8시, 구독하고 받아보기")
    d.text((W / 2 - text_w(d, cta, fc) / 2, 720), cta, font=fc, fill=(255, 255, 255, int(255 * a)))
    fs = font(36, "Medium")
    src = "자료: " + " · ".join(sc.get("sources", []))
    lines = wrap_hl(d, src, fs, 900)
    y = 860
    for ln in lines:
        s = "".join(t_ for t_, _ in ln).strip(); d.text((W / 2 - text_w(d, s, fs) / 2, y), s, font=fs, fill=(170, 195, 225)); y += 50

# ---------- thumbnail ----------
THEMES = {  # top color, bottom color, big-number color, stroke
    "sunny": ((255, 170, 40), (40, 140, 255), (255, 255, 255), (20, 60, 140)),
    "cold": ((10, 40, 110), (90, 200, 255), (255, 255, 255), (10, 30, 90)),
    "rain": ((20, 28, 45), (55, 85, 125), (120, 200, 255), (5, 10, 25)),
    "snow": ((70, 95, 140), (200, 225, 245), (255, 255, 255), (30, 50, 100)),
    "hot": ((255, 200, 40), (225, 40, 30), (255, 255, 255), (120, 10, 10)),
    "dust": ((150, 120, 80), (90, 80, 70), (255, 214, 64), (40, 30, 20)),
    "cloudy": ((90, 105, 130), (160, 175, 195), (255, 255, 255), (30, 40, 60)),
}

def fit_font(d, text, weight, size, maxw):
    while size > 40:
        f = font(size, weight)
        if d.textlength(re.sub(r"[{}]", "", text), font=f) <= maxw: return f
        size -= 6
    return font(size, weight)

def make_thumb(spec, path):
    th = spec.get("thumb") or {}
    name = th.get("theme", "sunny"); top, bot, big_c, stroke = THEMES.get(name, THEMES["sunny"])
    y = np.linspace(0, 1, H)[:, None, None]
    arr = (np.array(top) * (1 - y) + np.array(bot) * y).repeat(W, axis=1)
    yy, xx = np.mgrid[0:H, 0:W]
    glow = np.exp(-(((xx - 540) / 600) ** 2 + ((yy - 700) / 500) ** 2))[:, :, None]
    arr = arr + glow * 40
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    fx = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(fx)
    rng = np.random.default_rng(7)
    if name == "rain":
        for _ in range(220):
            x, yv = rng.integers(-100, W), rng.integers(-100, H); L = rng.integers(40, 110)
            d.line([(x, yv), (x - L * 0.35, yv + L)], fill=(170, 210, 255, int(rng.integers(60, 140))), width=int(rng.integers(2, 5)))
    elif name in ("snow", "cold"):
        for _ in range(160 if name == "snow" else 70):
            x, yv = rng.integers(0, W), rng.integers(0, H); r = rng.integers(3, 10 if name == "snow" else 6)
            d.ellipse([x - r, yv - r, x + r, yv + r], fill=(255, 255, 255, int(rng.integers(90, 200))))
    elif name == "dust":
        for _ in range(40):
            x, yv = rng.integers(0, W), rng.integers(0, H); r = rng.integers(60, 200)
            d.ellipse([x - r, yv - r, x + r, yv + r], fill=(210, 190, 150, 18))
    elif name in ("sunny", "hot"):
        cx, cy = 880, 260
        for i in range(16):
            a = i * math.pi / 8
            d.polygon([(cx, cy), (cx + math.cos(a - 0.08) * 1400, cy + math.sin(a - 0.08) * 1400),
                       (cx + math.cos(a + 0.08) * 1400, cy + math.sin(a + 0.08) * 1400)], fill=(255, 255, 255, 22))
    img.alpha_composite(fx.filter(ImageFilter.GaussianBlur(1.2)))
    d = ImageDraw.Draw(img)
    # brand (작게) + 날짜 리본 (크게)
    fb = font(46, "Black"); brand = spec.get("brand", "")
    bw = d.textlength(brand, font=fb) + 48
    d.rounded_rectangle([W / 2 - bw / 2, 96, W / 2 + bw / 2, 172], radius=38, fill=(255, 214, 64))
    d.text((W / 2 - bw / 2 + 24, 103), brand, font=fb, fill=INK)
    dl = re.sub(r"\s*·.*$", "", spec.get("date_label", ""))
    m = re.match(r"\s*(\d{1,2})\.(\d{1,2})\s*\((.)\)", dl)
    dtxt = f"{int(m.group(1))}월 {int(m.group(2))}일 {m.group(3)}요일" if m else dl
    fdt = font(92, "Black"); dw = d.textlength(dtxt, font=fdt)
    sh0 = Image.new("RGBA", (W, H), (0, 0, 0, 0)); ImageDraw.Draw(sh0).rounded_rectangle(
        [W / 2 - dw / 2 - 44 + 8, 196 + 12, W / 2 + dw / 2 + 44 + 8, 330 + 12], radius=30, fill=(0, 0, 0, 110))
    img.alpha_composite(sh0.filter(ImageFilter.GaussianBlur(8))); d = ImageDraw.Draw(img)
    d.rounded_rectangle([W / 2 - dw / 2 - 44, 196, W / 2 + dw / 2 + 44, 330], radius=30, fill=(255, 255, 255), outline=stroke, width=6)
    d.text((W / 2 - dw / 2, 208), dtxt, font=fdt, fill=INK)
    OFF = 60
    # top line
    if th.get("top"):
        ft = fit_font(d, th["top"], "Black", 130, 960)
        tw = d.textlength(th["top"], font=ft)
        d.text((W / 2 - tw / 2, 330 + OFF), th["top"], font=ft, fill=WHITE, stroke_width=10, stroke_fill=stroke)
    # big number with shadow
    big = th.get("big", "")
    fbig = fit_font(d, big, "Black", 400, 1000)
    bw2 = d.textlength(big, font=fbig)
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0)); ds = ImageDraw.Draw(sh)
    ds.text((W / 2 - bw2 / 2 + 14, 500 + OFF + 18), big, font=fbig, fill=(0, 0, 0, 150))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(10)))
    d = ImageDraw.Draw(img)
    d.text((W / 2 - bw2 / 2, 500 + OFF), big, font=fbig, fill=big_c, stroke_width=16, stroke_fill=stroke)
    # bottom line with highlight
    if th.get("bottom"):
        fbo = fit_font(d, th["bottom"], "Black", 128, 980)
        layer = Image.new("RGBA", (W, 400), (0, 0, 0, 0)); dl2 = ImageDraw.Draw(layer)
        segs = parse_hl(th["bottom"]); total = sum(dl2.textlength(t, font=fbo) for t, _ in segs)
        x = W / 2 - total / 2
        dl2.rounded_rectangle([x - 34, 0, x + total + 34, fbo.size * 1.45], radius=30, fill=(10, 16, 30, 230))
        for t, h in segs:
            w = dl2.textlength(t, font=fbo)
            if h:
                dl2.rounded_rectangle([x - 6, fbo.size * 0.2, x + w + 6, fbo.size * 1.32], radius=14, fill=HL)
                dl2.text((x, 8), t, font=fbo, fill=INK)
            else:
                dl2.text((x, 8), t, font=fbo, fill=WHITE)
            x += w
        img.alpha_composite(layer, (0, 1010 + OFF))
    # icon
    ic = icon(th.get("icon", "sun"), 470)
    sh2 = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sh2.alpha_composite(ic, (W // 2 - 235 + 12, 1230 + OFF + 16))
    r_, g_, b_, a_ = sh2.split(); sh2 = Image.merge("RGBA", (r_.point(lambda v: 0), g_.point(lambda v: 0), b_.point(lambda v: 0), a_.point(lambda v: int(v * 0.35))))
    img.alpha_composite(sh2.filter(ImageFilter.GaussianBlur(12)))
    img.alpha_composite(ic, (W // 2 - 235, 1230 + OFF))
    img.convert("RGB").save(path)
    return path

SCENES = {"hook": sc_hook, "map_temp": sc_map_temp, "map_region": sc_map_region, "rain_map": sc_rain_map,
          "timeline": sc_timeline, "models": sc_models, "tip": sc_tip, "outro": sc_outro}

# ---------- timing ----------
def line_dur(tts):
    n = len(re.sub(r"[\s,.!?·]", "", tts or ""))
    return max(1.8, n / 6.8 + 0.45)

def build_timeline(spec):
    tl = []; t = 0.0
    for sc in spec["scenes"]:
        lines = sc.get("lines", [])
        ls = []; st = t
        for ln in lines:
            dur = line_dur(ln.get("tts", ln.get("sub", "")))
            ls.append((t, t + dur, ln)); t += dur
        if sc["type"] == "outro" and t - st < 2.5: t = st + 2.5
        if not lines: t += 2.0
        tl.append((st, t, sc, ls))
    return tl, t

def render_frame(spec, tl, tg):
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for st, en, sc, ls in tl:
        if st <= tg < en or (sc is tl[-1][2] and tg >= st):
            SCENES[sc["type"]](img, sc, spec, tg - st, en - st)
            for a, b, ln in ls:
                if a <= tg < b: subtitle(img, ln.get("sub"), tg - a)
            break
    base = bg(); base.alpha_composite(img)
    return base.convert("RGB")

def srt_time(x):
    h = int(x // 3600); m = int(x % 3600 // 60); s = x % 60
    return f"{h:02d}:{m:02d}:{int(s):02d},{int((s % 1) * 1000):03d}"

def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8"))
    out = sys.argv[2]; os.makedirs(out, exist_ok=True)
    tl, total = build_timeline(spec)
    print(f"예상 길이: {total:.1f}초 ({sum(len(x[3]) for x in tl)}문장)")
    if spec.get("thumb"):
        print("thumbnail ->", make_thumb(spec, os.path.join(out, "thumbnail.png")))
    if "--thumb" in sys.argv: return
    if "--preview" in sys.argv:
        frames = []
        for st, en, sc, ls in tl:
            tg = min(en - 0.05, st + max(1.6, (en - st) * 0.7))
            frames.append(render_frame(spec, tl, tg).resize((360, 640), Image.LANCZOS))
        cols = 4; rows = math.ceil(len(frames) / cols)
        sheet = Image.new("RGB", (cols * 370 + 10, rows * 650 + 10), (30, 30, 30))
        for i, fr in enumerate(frames): sheet.paste(fr, (10 + (i % cols) * 370, 10 + (i // cols) * 650))
        sheet.save(os.path.join(out, "preview.png")); print("preview ->", os.path.join(out, "preview.png")); return
    # scripts
    with open(os.path.join(out, "dubbing_script.txt"), "w", encoding="utf-8") as f:
        for st, en, sc, ls in tl:
            for a, b, ln in ls: f.write(ln.get("tts", "") + "\n")
    with open(os.path.join(out, "subtitles.srt"), "w", encoding="utf-8") as f:
        i = 1
        for st, en, sc, ls in tl:
            for a, b, ln in ls:
                f.write(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{re.sub(r'[{}]', '', ln.get('sub', ''))}\n\n"); i += 1
    nf = int(math.ceil(total * FPS))
    p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                          "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                          "-movflags", "+faststart", os.path.join(out, "video.mp4")], stdin=subprocess.PIPE)
    for i in range(nf):
        p.stdin.write(render_frame(spec, tl, i / FPS).tobytes())
        if i % 150 == 0: print(f"  {i}/{nf}", flush=True)
    p.stdin.close(); p.wait()
    print("done ->", os.path.join(out, "video.mp4"))

if __name__ == "__main__":
    main()
