"""크로스웨더 페이퍼 애니메이션 렌더러 (1080x1920, 30fps, 나레이션·자막대본 없음, 효과음+배경음악 내장)

사용법 (renderer 폴더에서):
  python3 paper_engine.py ../spec_paper.json ../out_paper --preview   # 장면별 마지막 프레임 -> preview.png, thumbnail.png
  python3 paper_engine.py ../spec_paper.json ../out_paper --frame 2 1.5   # 2번 장면 1.5초 프레임 -> frame_2_1.5.png
  python3 paper_engine.py ../spec_paper.json ../out_paper             # video.mp4 (BGM+효과음 포함, 약 2~4분), thumbnail.png
  (배경음악·효과음은 코드로 합성 -> 저작권 걱정 없음. 결과 오디오는 out/audio_bgm_sfx.wav 로도 남음)

스타일: 종이 오리기(cut-paper). 종이 질감·그림자, 12fps 스톱모션 흔들림, 장면 전환은 다음 장면 색 종이가 밀려 들어옴.
나레이션이 없으니 화면 글자(title/captions/label)만으로 내용이 다 전달돼야 함. 글자 {중괄호}는 강조색.

spec 형식:
{
 "brand": "크로스웨더", "date_label": "11.29 (일)", "thumb_date": "11월 29일 일요일",
 "note": "기상청 11/28 02시 발표 기준",             # hook 하단 작은 글씨
 "thumb": {"theme": "cold", "top": "중부 내일 아침", "big": "영하", "bottom": "{패딩} 꺼낼 때", "icon": "coat"},
 "scenes": [
  {"type": "hook", "dur": 4.5, "bg": [170,210,236], "top": "중부 내일 아침", "big": "영하", "bottom": "아침 최저 {-5~-1°}",
   "preview_t": 3.0},                                  # theme 생략 시 thumb.theme 사용
  {"type": "map_temp", "dur": 6.5, "bg": [246,238,222], "title": "내일 {아침 최저}", "show": "min",
   "cities": {"서울": {"min": -2, "max": 7, "sky": "sun"}, ...},      # 도시 키는 engine.CITY 참고
   "captions": ["춘천 {-5°} · 서울 -2°", "부산 1° · 제주 {8°}"], "cap_at": [2.6, 4.4]},
  {"type": "map_temp", "dur": 6.0, "title": "내일 {낮 최고}", "show": "max", "cities": {...}, "captions": [...], "cap_at": [2.4, 4.2]},
  {"type": "rain_map", "dur": 5.0, "bg": [226,232,240], "title": "내일 {비 소식}",
   "regions": {"서울": 60, "경기": 60, ...}, "captions": ["수도권 오후 {60%}"], "cap_at": [2.2]},   # 강수확률 0~100
  {"type": "map_region", "dur": 4.5, "bg": [226,238,222], "title": "내일 {미세먼지}",
   "regions": {"서울": "좋음", ...}, "captions": ["전국 {좋음} · 환기하기 좋아요"], "cap_at": [2.2]},   # 좋음|보통|나쁨|매우나쁨
  {"type": "models", "dur": 7.0, "bg": [214,186,146], "title": "서울 아침 {크로스체크}",
   "official": "기상청 예보 서울 최저 {-2°}", "unit": "°", "note": "서울 · 11/29(일) 아침 최저 · 모델 값은 참고용",
   "items": [{"name": "유럽", "sub": "ECMWF", "value": -2.4, "decimals": 1}, {"name": "미국", "sub": "GFS", "value": 0.9, "decimals": 1}],
   "captions": ["유럽은 영하, 미국은 {영상}"], "cap_at": [3.2]},
  {"type": "tip", "dur": 4.5, "bg": [170,210,236], "label": "내일의 한마디", "icon": "coat",
   "headline": "아침엔 {두툼한 외투}", "sub": "중부 일교차 {10도} 안팎"},
  {"type": "outro", "dur": 4.0, "bg": [58,72,120], "sources": ["기상청","에어코리아","ECMWF","NOAA GFS"], "cta": "매일 저녁 8시, {내일 날씨}", "preview_t": 2.5}
 ]
}
theme: cold|snow|rain|sunny|hot|dust|cloudy (배경·언덕색, 큰 글자색, 눈/비/먼지 입자, 해 표시가 바뀜. THEMES 참고)
  hook/thumb 배경은 THEMES[theme][0] 를 bg 로 쓰면 자연스러움. 이웃 장면 bg 는 서로 다르게(전환이 보이도록).
icon: coat|umbrella|mask|sun|cloud|rain|snow.  sky: sun|partly|cloud|rain|shower|snow.
map 장면 지역 키: 서울 인천 경기 강원 충북 충남 세종 대전 전북 전남 광주 경북 경남 대구 울산 부산 제주
글자 길이: title 12자, captions 18자, hook top 10자·big 4자·bottom 12자 안팎 (넘치면 --preview 로 확인하고 줄이기)
권장 총 길이 30~40초.
"""
import json, math, os, random, subprocess, sys, shutil
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine as E  # 지도 폴리곤·도시 좌표·폰트만 재사용

W, H, FPS, STEP_FPS = 1080, 1920, 30, 12
SR = 44100
font = E.font

# ---------- paper palette ----------
CREAM = (246, 238, 222); KRAFT = (214, 186, 146); SKY = (170, 210, 236); NIGHT = (58, 72, 120)
INK = (44, 50, 72); RED = (232, 96, 74); MUSTARD = (247, 198, 70); TEAL = (70, 168, 158)
COLD = (96, 142, 222); WHITE = (255, 253, 248); MINT = (150, 210, 180); PAPER_LAND = (234, 216, 184)
GRADE = {"좋음": (110, 160, 235), "보통": (110, 196, 140), "나쁨": (246, 170, 70), "매우나쁨": (226, 84, 74)}

def temp_paper(t):
    stops = [(-8, (92, 112, 210)), (-2, (110, 150, 232)), (3, (120, 196, 226)), (9, (130, 204, 160)),
             (15, (245, 200, 80)), (22, (244, 150, 70)), (30, (226, 84, 74))]
    if t <= stops[0][0]: return stops[0][1]
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if t <= b:
            k = (t - a) / (b - a); return tuple(int(ca[i] + (cb[i] - ca[i]) * k) for i in range(3))
    return stops[-1][1]

def clamp(x, a=0.0, b=1.0): return max(a, min(b, x))
def ease_out(x): x = clamp(x); return 1 - (1 - x) ** 3
def ease_back(x):
    x = clamp(x); c1 = 2.2; c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2

# ---------- texture ----------
_rng = np.random.default_rng(7)
def _make_tex():
    n = _rng.normal(0, 1, (H // 2, W // 2)).astype(np.float32)
    im = Image.fromarray(np.uint8(np.clip(128 + n * 40, 0, 255))).filter(ImageFilter.GaussianBlur(1.2)).resize((W, H))
    a = np.asarray(im, np.float32) / 255.0
    fib = Image.new("L", (W, H), 0); d = ImageDraw.Draw(fib)
    r = random.Random(3)
    for _ in range(900):
        x, y = r.uniform(0, W), r.uniform(0, H); ang = r.uniform(0, math.pi); L = r.uniform(8, 40)
        d.line([(x, y), (x + math.cos(ang) * L, y + math.sin(ang) * L)], fill=r.randint(30, 80), width=1)
    fb = np.asarray(fib.filter(ImageFilter.GaussianBlur(0.6)), np.float32) / 255.0
    return (0.94 + 0.10 * (a - 0.5) - 0.05 * fb)[..., None]
TEX = _make_tex()

def stepf(t): return math.floor(t * STEP_FPS) / STEP_FPS
def jit(key, t, amp=2.5):
    r = random.Random(hash((key, int(t * STEP_FPS))) & 0xffffffff)
    return r.uniform(-amp, amp), r.uniform(-amp, amp)

# ---------- paper pieces ----------
def rough(poly, seed, amp=2.2, seg=14):
    r = random.Random(seed); out = []
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        L = math.hypot(x1 - x0, y1 - y0); k = max(1, int(L / seg))
        for j in range(k):
            u = j / k; out.append((x0 + (x1 - x0) * u + r.uniform(-amp, amp), y0 + (y1 - y0) * u + r.uniform(-amp, amp)))
    return out

def piece_from_mask(mask, color, shadow=(7, 11), sh_alpha=0.32, edge=True):
    """mask: L image. returns RGBA paper piece with drop shadow (canvas padded by 30)."""
    pad = 30; w, h = mask.size
    m = Image.new("L", (w + pad * 2, h + pad * 2), 0); m.paste(mask, (pad, pad))
    out = Image.new("RGBA", m.size, (0, 0, 0, 0))
    sh = m.filter(ImageFilter.GaussianBlur(7)).point(lambda v: int(v * sh_alpha))
    shl = Image.new("RGBA", m.size, (40, 30, 20, 0)); shl.putalpha(sh)
    out.alpha_composite(shl, (shadow[0], shadow[1])) if False else None
    tmp = Image.new("RGBA", m.size, (0, 0, 0, 0)); tmp.paste(shl, shadow, shl)
    out.alpha_composite(tmp)
    fill = Image.new("RGBA", m.size, color + (255,)); fill.putalpha(m)
    out.alpha_composite(fill)
    if edge:  # light cut edge
        er = m.filter(ImageFilter.MinFilter(3))
        ed = Image.eval(Image.fromarray(np.asarray(m, np.int16).clip(0) .astype(np.uint8)), lambda v: v)
        ring = Image.fromarray(np.clip(np.asarray(m, np.int16) - np.asarray(er, np.int16), 0, 255).astype(np.uint8))
        hl = Image.new("RGBA", m.size, (255, 255, 255, 0)); hl.putalpha(ring.point(lambda v: int(v * 0.45)))
        out.alpha_composite(hl)
    return out, pad

_PC = {}
def poly_piece(key, poly, color, ss=2, amp=2.2):
    k = (key, color)
    if k in _PC: return _PC[k]
    rp = rough(poly, hash(key) & 0xffff, amp)
    x0 = min(p[0] for p in rp); y0 = min(p[1] for p in rp); x1 = max(p[0] for p in rp); y1 = max(p[1] for p in rp)
    w, h = int(x1 - x0) + 4, int(y1 - y0) + 4
    m = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(m).polygon([((x - x0 + 2) * ss, (y - y0 + 2) * ss) for x, y in rp], fill=255)
    m = m.resize((w, h), Image.LANCZOS)
    pc, pad = piece_from_mask(m, color)
    _PC[k] = (pc, x0 - 2 - pad, y0 - 2 - pad)
    return _PC[k]

def ellipse_piece(key, r, color):
    s = int(r * 2 + 4); m = Image.new("L", (s * 2, s * 2), 0)
    pts = [(s + math.cos(a / 40 * 2 * math.pi) * r * 2, s + math.sin(a / 40 * 2 * math.pi) * r * 2) for a in range(40)]
    rr = rough(pts, hash(key) & 0xffff, 3, 10)
    ImageDraw.Draw(m).polygon(rr, fill=255)
    pc, pad = piece_from_mask(m.resize((s, s), Image.LANCZOS), color)
    return pc, pad + s / 2

def rect_poly(x0, y0, x1, y1, torn=False, seed=0):
    if not torn: return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    r = random.Random(seed); top = []; bot = []
    n = int((x1 - x0) / 22)
    for i in range(n + 1):
        x = x0 + (x1 - x0) * i / n; top.append((x, y0 + r.uniform(-7, 7))); bot.append((x, y1 + r.uniform(-7, 7)))
    return top + bot[::-1]

def paste(img, pc_info, dx=0.0, dy=0.0, scale=1.0, rot=0.0, alpha=1.0):
    pc, x, y = pc_info
    if scale != 1.0 or rot:
        cw, ch = pc.size
        p2 = pc.resize((max(1, int(cw * scale)), max(1, int(ch * scale))), Image.BILINEAR) if scale != 1.0 else pc
        if rot: p2 = p2.rotate(rot, resample=Image.BICUBIC, expand=True)
        x += (cw - p2.size[0]) / 2; y += (ch - p2.size[1]) / 2; pc = p2
    if alpha < 1:
        pc = pc.copy(); pc.putalpha(pc.split()[3].point(lambda v: int(v * alpha)))
    img.alpha_composite(pc, (int(round(x + dx)), int(round(y + dy))))

# ---------- text on paper ----------
def parse_hl(s):
    out, hl = [], False
    for part in __import__("re").split(r"([{}])", s):
        if part == "{": hl = True
        elif part == "}": hl = False
        elif part: out.append((part, hl))
    return out

_TC = {}
def text_img(s, size, color=INK, hl=RED, weight="Black", stroke=0, stroke_col=WHITE):
    k = (s, size, color, hl, weight, stroke, stroke_col)
    if k in _TC: return _TC[k]
    f = font(size, weight); segs = parse_hl(s)
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = sum(d0.textlength(t, font=f) for t, _ in segs)
    im = Image.new("RGBA", (int(tw) + stroke * 2 + 8, int(size * 1.45) + stroke * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(im); x = stroke + 4
    for t, h in segs:
        d.text((x, stroke), t, font=f, fill=(hl if h else color) + (255,), stroke_width=stroke, stroke_fill=stroke_col + (255,))
        x += d.textlength(t, font=f)
    _TC[k] = im
    return im

def put_text(img, s, size, cx, y, color=INK, hl=RED, weight="Black", dx=0, dy=0, alpha=1.0, stroke=0, rot=0, scale=1.0, stroke_col=WHITE):
    base = text_img(s, size, color, hl, weight, stroke, stroke_col); im = base
    if scale != 1.0: im = im.resize((max(1, int(im.size[0] * scale)), max(1, int(im.size[1] * scale))), Image.BILINEAR)
    if rot: im = im.rotate(rot, resample=Image.BICUBIC, expand=True)
    if alpha < 1: im = im.copy(); im.putalpha(im.split()[3].point(lambda v: int(v * alpha)))
    yc = y + base.size[1] / 2 - size * 0.12
    img.alpha_composite(im, (int(cx - im.size[0] / 2 + dx), int(yc - im.size[1] / 2 + dy)))
    return im.size

def label(img, key, s, size, cx, cy, bg, color=INK, hl=RED, padx=34, pady=18, t=1.0, rot=0.0, torn=True, tstep=0):
    """paper strip with text, pops in with t(0..1)"""
    if t <= 0: return
    tw = text_img(s, size, color, hl).size[0]
    w = tw + padx * 2; h = size * 1.25 + pady * 2
    pi = poly_piece(("label", key, s, size, bg), rect_poly(0, 0, w, h, torn, hash(key) & 0xff), bg, amp=1.5)
    sc = 0.4 + 0.6 * ease_back(t)
    jx, jy = jit(key, tstep, 1.6)
    pc, ox, oy = pi
    layer = Image.new("RGBA", pc.size, (0, 0, 0, 0)); layer.alpha_composite(pc)
    tim = text_img(s, size, color, hl)
    layer.alpha_composite(tim, (int(-ox + padx - 4), int(-oy + pady + h * 0.0 + (h - pady * 2 - tim.size[1]) / 2 - size * 0.06)))
    paste(img, (layer, cx - w / 2 + ox, cy - h / 2 + oy), jx, jy, sc, rot)

# ---------- doodles ----------
def sun_piece(r):
    k = ("sun", r)
    if k in _PC: return _PC[k]
    s = int(r * 4.2); L = Image.new("RGBA", (s, s), (0, 0, 0, 0)); c = s / 2
    pts = []
    for i in range(32):
        a = i / 32 * 2 * math.pi; rr = r * (1.75 if i % 2 == 0 else 1.25)
        pts.append((c + math.cos(a) * rr, c + math.sin(a) * rr))
    p1 = poly_piece(("sunray", r), pts, (250, 170, 60))
    p2 = poly_piece(("sundisk", r), [(c + math.cos(a / 30 * 6.283) * r, c + math.sin(a / 30 * 6.283) * r) for a in range(30)], MUSTARD)
    L2 = Image.new("RGBA", (s + 80, s + 80), (0, 0, 0, 0))
    paste(L2, (p1[0], p1[1] + 40, p1[2] + 40)); paste(L2, (p2[0], p2[1] + 40, p2[2] + 40))
    _PC[k] = (L2, -s / 2 - 40, -s / 2 - 40)
    return _PC[k]

def cloud_piece(w, color=WHITE):
    k = ("cloud", w, color)
    if k in _PC: return _PC[k]
    h = w * 0.5; ss = 2
    m = Image.new("L", (int(w * ss) + 40, int(h * ss) + 40), 0); d = ImageDraw.Draw(m)
    def E_(x, y, r): d.ellipse([(x - r) * ss + 20, (y - r) * ss + 20, (x + r) * ss + 20, (y + r) * ss + 20], fill=255)
    E_(w * 0.28, h * 0.62, h * 0.36); E_(w * 0.52, h * 0.42, h * 0.42); E_(w * 0.75, h * 0.62, h * 0.32)
    d.rounded_rectangle([w * 0.1 * ss + 20, h * 0.55 * ss + 20, w * 0.92 * ss + 20, h * 0.98 * ss + 20], radius=h * 0.2 * ss, fill=255)
    m = m.resize((m.size[0] // ss, m.size[1] // ss), Image.LANCZOS)
    pc, pad = piece_from_mask(m, color)
    _PC[k] = (pc, -pad - 10 - w / 2, -pad - 10 - h / 2)
    return _PC[k]

def snowflake_piece(r):
    k = ("flake", r)
    if k in _PC: return _PC[k]
    s = int(r * 2.6); m = Image.new("L", (s * 2, s * 2), 0); d = ImageDraw.Draw(m); c = s
    for i in range(6):
        a = i * math.pi / 3
        d.line([(c, c), (c + math.cos(a) * r * 2, c + math.sin(a) * r * 2)], fill=255, width=int(r * 0.45))
    d.ellipse([c - r * 0.5, c - r * 0.5, c + r * 0.5, c + r * 0.5], fill=255)
    pc, pad = piece_from_mask(m.resize((s, s), Image.LANCZOS), WHITE, shadow=(4, 6))
    _PC[k] = (pc, -pad - s / 2, -pad - s / 2)
    return _PC[k]

def coat_piece(S):
    k = ("coat", S)
    if k in _PC: return _PC[k]
    L = Image.new("RGBA", (S + 80, S + 80), (0, 0, 0, 0)); o = 40
    body = [(.3, .14), (.7, .14), (.92, .42), (.8, .5), (.72, .4), (.72, .9), (.28, .9), (.28, .4), (.2, .5), (.08, .42)]
    p = poly_piece(("coatb", S), [(o + x * S, o + y * S) for x, y in body], RED)
    paste(L, p)
    p = poly_piece(("coatc", S), [(o + x * S, o + y * S) for x, y in [(.38, .14), (.5, .34), (.62, .14)]], CREAM)
    paste(L, p)
    sc = [(.2, .2), (.8, .2), (.78, .32), (.22, .32)]
    p = poly_piece(("scarf", S), [(o + x * S, o + y * S) for x, y in sc], MUSTARD); paste(L, p)
    p = poly_piece(("scarf2", S), [(o + x * S, o + y * S) for x, y in [(.58, .3), (.7, .3), (.72, .62), (.6, .6)]], MUSTARD); paste(L, p)
    d = ImageDraw.Draw(L)
    for y in (.45, .6, .75): d.ellipse([o + .47 * S, o + y * S, o + .53 * S, o + (y + .06) * S], fill=INK + (255,))
    _PC[k] = (L, -S / 2 - o, -S / 2 - o)
    return _PC[k]

THEMES = {  # bg, hills(3), big-number color, particles, sun
    "cold":   ((170, 210, 236), [(150, 196, 228), (130, 180, 220), (110, 162, 210)], (96, 142, 222), "snow", True),
    "snow":   ((196, 212, 236), [(236, 240, 248), (220, 228, 242), (204, 216, 236)], (86, 120, 200), "snow", False),
    "rain":   ((156, 176, 200), [(128, 152, 186), (112, 138, 176), (96, 122, 162)], (52, 112, 214), "rain", False),
    "sunny":  ((252, 228, 170), [(168, 208, 150), (140, 192, 130), (118, 172, 116)], (238, 128, 40), None, True),
    "hot":    ((255, 204, 160), [(250, 172, 112), (240, 150, 96), (226, 124, 80)], (220, 70, 50), None, True),
    "dust":   ((228, 212, 176), [(212, 192, 152), (198, 176, 136), (182, 160, 122)], (196, 120, 40), "dust", True),
    "cloudy": ((200, 208, 220), [(176, 186, 202), (160, 172, 190), (146, 158, 178)], (84, 98, 136), None, False),
}
def theme(name): return THEMES.get(name or "sunny", THEMES["sunny"])

def particles(img, kind, tt, y_max=1500, n=12, seed=0, y_min=0):
    if not kind: return
    d = ImageDraw.Draw(img)
    for i in range(n):
        r = random.Random(i + seed * 100); x = r.uniform(40, W - 40)
        if kind == "snow":
            y = (r.uniform(-200, 1600) + tt * r.uniform(80, 160)) % (y_max + 200) - 100
            fp = snowflake_piece(r.uniform(10, 18)); paste(img, (fp[0], x + fp[1] + math.sin(tt * 2 + i) * 20, y + fp[2]))
        elif kind == "rain":
            for k in range(3):
                y = y_min + (r.uniform(0, 1800) + k * 600 + tt * 900) % (y_max - y_min)
                xx = x + k * 37 % 90
                d.line([(xx, y), (xx - 10, y + 46)], fill=(70, 130, 230, 230), width=8)
        elif kind == "dust":
            y = (r.uniform(0, 1600) + math.sin(tt + i) * 40) % y_max
            rr = r.uniform(6, 14); d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(170, 140, 100, 160))

def umbrella_piece(S):
    k = ("umb", S)
    if k in _PC: return _PC[k]
    L = Image.new("RGBA", (S + 80, S + 80), (0, 0, 0, 0)); o = 40
    canopy = [(o + S / 2 + math.cos(math.pi + a / 24 * math.pi) * S * .45, o + S * .5 + math.sin(math.pi + a / 24 * math.pi) * S * .4) for a in range(25)]
    paste(L, poly_piece(("umbc", S), canopy, TEAL))
    for j in range(3):
        cx = o + S * (.5 - .3 + j * .3)
        paste(L, poly_piece(("umbs", S, j), [(cx - S * .05, o + S * .5), (cx, o + S * .12), (cx + S * .05, o + S * .5)], (96, 196, 186)))
    d = ImageDraw.Draw(L); w = int(S * .045)
    d.line([(o + S * .5, o + S * .5), (o + S * .5, o + S * .86)], fill=INK + (255,), width=w)
    d.arc([o + S * .38, o + S * .78, o + S * .5, o + S * .92], 0, 180, fill=INK + (255,), width=w)
    _PC[k] = (L, -S / 2 - o, -S / 2 - o)
    return _PC[k]

def mask_piece(S):
    k = ("mask", S)
    if k in _PC: return _PC[k]
    L = Image.new("RGBA", (S + 80, S + 80), (0, 0, 0, 0)); o = 40
    d = ImageDraw.Draw(L); w = int(S * .04)
    d.arc([o + S * .02, o + S * .3, o + S * .32, o + S * .66], 90, 270, fill=WHITE + (255,), width=w)
    d.arc([o + S * .68, o + S * .3, o + S * .98, o + S * .66], 270, 90, fill=WHITE + (255,), width=w)
    paste(L, poly_piece(("maskb", S), rect_poly(o + S * .18, o + S * .3, o + S * .82, o + S * .7), (240, 246, 252)))
    for j, y in enumerate((.42, .5, .58)):
        paste(L, poly_piece(("maskl", S, j), rect_poly(o + S * .22, o + S * y, o + S * .78, o + S * (y + .025)), (176, 196, 220), amp=0.6))
    _PC[k] = (L, -S / 2 - o, -S / 2 - o)
    return _PC[k]

def icon_piece(kind, S):
    if kind == "coat": return coat_piece(S)
    if kind == "umbrella": return umbrella_piece(S)
    if kind == "mask": return mask_piece(S)
    if kind in ("rain", "shower", "cloud", "partly", "snow"): return cloud_piece(S, (236, 240, 246))
    return sun_piece(int(S * 0.26))

def hills(img, th, tt=None, y0=1420):
    for i, c in enumerate(th[1]):
        u = 1 if tt is None else ease_out(clamp((tt - i * 0.08) / 0.4))
        paste(img, poly_piece(("hill", i, c, y0), rect_poly(-40, y0 + i * 160, W + 40, H + 60, True, i + 11), c), 0, (1 - u) * 500)

# ---------- background ----------
_BG = {}
def bg(color):
    if color not in _BG:
        _BG[color] = Image.new("RGBA", (W, H), color + (255,))
    return _BG[color].copy()

def finish(img):
    a = np.asarray(img.convert("RGB"), np.float32) * TEX
    return np.clip(a, 0, 255).astype(np.uint8)

def header(img, spec, t, title=None, tcol=INK):
    tt = stepf(t)
    label(img, "brand", spec["brand"], 40, 190, 120, MUSTARD, INK, t=clamp(tt / 0.3), rot=-3, tstep=tt)
    label(img, "date", spec["date_label"], 40, 440, 122, WHITE, INK, t=clamp((tt - 0.1) / 0.3), rot=2, tstep=tt)
    if title:
        label(img, "title" + title, title, 66, W / 2, 262, WHITE, tcol, t=clamp((tt - 0.2) / 0.35), rot=-1.2, tstep=tt)

def caption(img, s, t, y=1560, bg_=INK, col=WHITE, hl=MUSTARD, key="cap", size=52):
    label(img, key + s, s, size, W / 2, y, bg_, col, hl, t=clamp(t / 0.35), rot=1.0, tstep=t)

# ---------- map ----------
MAP_DY = 70  # 지도를 아래로
def map_pieces(fills, dy=MAP_DY):
    out = []
    for key, polys in E.PROVS.items():
        col = fills.get(key, PAPER_LAND)
        for i, poly in enumerate(polys):
            if len(poly) < 6 and key != "제주": continue
            pp = [(x, y + dy) for x, y in poly]
            out.append((key, poly_piece(("prov", key, i), pp, col, amp=1.2)))
    return out

PROV_ORDER = ["제주", "전남", "광주", "경남", "부산", "울산", "전북", "대구", "경북", "충남", "대전", "세종", "충북", "경기", "서울", "인천", "강원"]
def draw_map(img, fills, t, drop=True, key="m"):
    pcs = map_pieces(fills)
    for name, p in pcs:
        idx = PROV_ORDER.index(name) if name in PROV_ORDER else 0
        u = clamp((t - idx * 0.035) / 0.3) if drop else 1
        if u <= 0: continue
        jx, jy = jit((key, name), t, 1.2)
        paste(img, p, jx, jy - (1 - ease_back(u)) * 90, alpha=clamp(u * 3))
    # inset box for 울릉도
    x0, y0, x1, y1 = E.INSET
    if t > 0.5:
        d = ImageDraw.Draw(img)
        for i in range(0, int(x1 - x0), 18):
            d.line([(x0 + i, y0 + MAP_DY), (x0 + i + 9, y0 + MAP_DY)], fill=INK + (150,), width=3)
            d.line([(x0 + i, y1 + MAP_DY), (x0 + i + 9, y1 + MAP_DY)], fill=INK + (150,), width=3)
        for i in range(0, int(y1 - y0), 18):
            d.line([(x0, y0 + MAP_DY + i), (x0, y0 + MAP_DY + i + 9)], fill=INK + (150,), width=3)
            d.line([(x1, y0 + MAP_DY + i), (x1, y0 + MAP_DY + i + 9)], fill=INK + (150,), width=3)

def temp_tag(img, name, temp, sky, cx, cy, u, t):
    if u <= 0: return
    col = temp_paper(temp)
    k = ("tag", name, col)
    if k not in _PC:
        L = Image.new("RGBA", (260, 200), (0, 0, 0, 0))
        pc, off = ellipse_piece(("tagc", name), 56, col)
        L.alpha_composite(pc, (int(130 - off), int(86 - off)))
        s = f"{int(temp)}°"
        ti = text_img(s, 50, WHITE if temp < 12 else INK, weight="Black")
        L.alpha_composite(ti, (int(130 - ti.size[0] / 2), int(86 - ti.size[1] / 2 - 4)))
        ni = text_img(name, 26, INK, weight="Bold")
        nb = poly_piece(("tagn", name), rect_poly(0, 0, ni.size[0] + 18, 40), WHITE, amp=1)
        paste(L, (nb[0], nb[1] + 130 - (ni.size[0] + 18) / 2, nb[2] + 150))
        L.alpha_composite(ni, (int(130 - ni.size[0] / 2), 150))
        _PC[k] = (L, -130, -86)
    jx, jy = jit(("t", name), t, 1.8)
    sc = 0.2 + 0.8 * ease_back(u)
    pc, ox, oy = _PC[k]
    paste(img, (pc, cx + ox, cy + oy), jx, jy, sc)
    if sky and u >= 1:
        ic = {"sun": None}.get(sky, 1)
        if sky == "sun":
            sp = sun_piece(13); pc2, ox2, oy2 = sp
            paste(img, (pc2, cx + 40 + ox2, cy - 50 + oy2), jx, jy, 1.0, rot=(t * 40) % 360 if False else 0)
        elif sky in ("rain", "shower", "cloud", "partly", "snow"):
            cp = cloud_piece(56, (225, 232, 240))
            paste(img, (cp[0], cx + 44 + cp[1], cy - 52 + cp[2]), jx, jy)
            if sky == "snow":
                for i in (-14, 14):
                    fp = snowflake_piece(7); paste(img, (fp[0], cx + 44 + i + fp[1], cy - 22 + fp[2]), jx, jy)
            if sky in ("rain", "shower"):
                d = ImageDraw.Draw(img)
                for i in (-12, 4, 20):
                    d.line([(cx + 44 + i, cy - 30 + jy), (cx + 38 + i, cy - 14 + jy)], fill=(70, 130, 230, 255), width=5)

def city_pos(name):
    lon, lat, m = E.CITY[name]
    return (m[0], m[1] + MAP_DY) if m else (E.proj(lon, lat)[0], E.proj(lon, lat)[1] + MAP_DY)

# ---------- scenes ----------
def sc_hook(img, sc, spec, t, T):
    tt = stepf(t)
    # sky paper strips
    th = theme(sc.get("theme") or spec.get("thumb", {}).get("theme"))
    if th[4]:  # sun rising behind hills
        sp = sun_piece(120); u = ease_out(clamp((tt - 0.25) / 1.2))
        paste(img, (sp[0], W / 2 + sp[1], 1520 + sp[2] + (1 - u) * 400), *jit("sun", tt, 2), rot=math.sin(tt * 2) * 6)
    hills(img, th, tt)
    # clouds sliding
    for i, (x, y, w, sp_) in enumerate([(200, 360, 260, 60), (920, 1000, 240, -50), (160, 1250, 200, 40)]):
        u = ease_out(clamp((tt - 0.3 - i * 0.12) / 0.6))
        cp = cloud_piece(w, WHITE if th[4] else (228, 234, 242))
        x2 = x + (1 - u) * (-700 if sp_ > 0 else 700) + sp_ * tt * 0.4
        paste(img, (cp[0], x2 + cp[1], y + cp[2]), *jit(("c", i), tt, 2))
    if tt > 0.8: particles(img, th[3], tt, 1450, 10)
    header(img, spec, t)
    label(img, "hk_top", sc["top"], 72, W / 2, 520, WHITE, INK, t=clamp((tt - 0.6) / 0.35), rot=-2, tstep=tt)
    u = clamp((tt - 0.9) / 0.4)
    if u > 0:
        put_text(img, sc["big"], 300, W / 2, 640, color=th[2], weight="Black", stroke=14, stroke_col=WHITE,
                 scale=0.3 + 0.7 * ease_back(u), dx=jit("big", tt, 3)[0], dy=jit("big", tt, 3)[1])
    label(img, "hk_bot", sc["bottom"], 64, W / 2, 1130, INK, WHITE, MUSTARD, t=clamp((tt - 1.5) / 0.35), rot=1.5, tstep=tt)
    if spec.get("note"):
        u = clamp((tt - 2.0) / 0.3)
        if u > 0: put_text(img, spec["note"], 34, W / 2, 1760, color=INK, weight="Bold", alpha=u)

def sc_map_temp(img, sc, spec, t, T):
    tt = stepf(t)
    header(img, spec, t, sc["title"])
    draw_map(img, {}, tt, key="mt" + sc["show"])
    names = list(sc["cities"].keys())
    for i, n in enumerate(names):
        c = sc["cities"][n]
        u = clamp((tt - 0.9 - i * 0.09) / 0.25)
        cx, cy = city_pos(n)
        temp_tag(img, n, c[sc["show"]], c.get("sky"), cx, cy, u, tt)
    for j, cap in enumerate(sc.get("captions", [])):
        start = sc.get("cap_at", [2.3, 4.2])[j]
        end = sc.get("cap_at", [2.3, 4.2])[j + 1] if j + 1 < len(sc["captions"]) else 99
        if start <= tt < end: caption(img, cap, tt - start, key=f"c{j}")

def sc_map_region(img, sc, spec, t, T):
    tt = stepf(t)
    header(img, spec, t, sc["title"])
    fills = {k: GRADE[v] for k, v in sc["regions"].items()}
    plain_u = clamp(tt / 0.01)
    draw_map(img, {}, tt, key="mr")
    # color sheets stamped region by region
    for i, name in enumerate(PROV_ORDER):
        u = clamp((tt - 1.0 - i * 0.05) / 0.2)
        if u <= 0: continue
        for j, poly in enumerate(E.PROVS.get(name, [])):
            if len(poly) < 6 and name != "제주": continue
            p = poly_piece(("prov", name, j), [(x, y + MAP_DY) for x, y in poly], fills.get(name, PAPER_LAND), amp=1.2)
            paste(img, p, *jit(("mr2", name), tt, 1.2), scale=0.85 + 0.15 * ease_back(u))
    # legend
    if tt > 1.8:
        x = 150
        for g in ["좋음", "보통", "나쁨", "매우나쁨"]:
            p = poly_piece(("lg", g), rect_poly(0, 0, 40, 40), GRADE[g], amp=1)
            paste(img, (p[0], x + p[1], 1400 + p[2]))
            ti = text_img(g, 34, INK, weight="Bold"); img.alpha_composite(ti, (x + 52, 1396))
            x += 70 + ti.size[0] + 20
    for j, cap in enumerate(sc.get("captions", [])):
        start = sc.get("cap_at", [2.2, 99])[j]; end = (sc.get("cap_at") or [2.2, 99] )[j + 1] if j + 1 < len(sc["captions"]) else 99
        if start <= tt < end: caption(img, cap, tt - start, key=f"r{j}")

def pop_paper(p):
    if p >= 80: return (52, 92, 200)
    if p >= 60: return (84, 132, 226)
    if p >= 30: return (150, 186, 232)
    return PAPER_LAND

def sc_rain_map(img, sc, spec, t, T):
    tt = stepf(t)
    header(img, spec, t, sc["title"])
    draw_map(img, {}, tt, key="rm")
    for i, name in enumerate(PROV_ORDER):
        u = clamp((tt - 1.0 - i * 0.05) / 0.2); p = sc["regions"].get(name, 0)
        if u <= 0 or p < 30: continue
        for j, poly in enumerate(E.PROVS.get(name, [])):
            if len(poly) < 6 and name != "제주": continue
            pp = poly_piece(("prov", name, j), [(x, y + MAP_DY) for x, y in poly], pop_paper(p), amp=1.2)
            paste(img, pp, *jit(("rm2", name), tt, 1.2), scale=0.85 + 0.15 * ease_back(u))
    if tt > 1.4: particles(img, "rain", tt, 1320, 8, seed=3, y_min=360)
    if tt > 1.8:
        x = 130
        for g, c in [("30%+", pop_paper(30)), ("60%+", pop_paper(60)), ("80%+", pop_paper(80))]:
            p = poly_piece(("lgp", g), rect_poly(0, 0, 40, 40), c, amp=1)
            paste(img, (p[0], x + p[1], 1400 + p[2]))
            ti = text_img(g, 34, INK, weight="Bold"); img.alpha_composite(ti, (x + 52, 1396)); x += 90 + ti.size[0] + 40
        ti = text_img("강수확률", 34, INK, weight="Bold"); img.alpha_composite(ti, (x, 1396))
    for j, cap in enumerate(sc.get("captions", [])):
        ca = sc.get("cap_at", [2.2, 99]); start = ca[j]; end = ca[j + 1] if j + 1 < len(sc["captions"]) else 99
        if start <= tt < end: caption(img, cap, tt - start, key=f"p{j}")

def sc_models(img, sc, spec, t, T):
    tt = stepf(t)
    header(img, spec, t, sc["title"])
    # official banner
    off = sc.get("official")
    if off:
        label(img, "off", off, 50, W / 2, 440, MUSTARD, INK, RED, t=clamp((tt - 0.4) / 0.35), rot=-1, tstep=tt)
    items = sc["items"]; n = len(items)
    unit = sc.get("unit", "°")
    vals = [it["value"] for it in items]
    lo = min(min(vals), 0) - 1.5; hi = max(max(vals), 0) + 1.5
    base_y, top_y = 1300, 650
    def Y(v): return base_y - (v - lo) / (hi - lo) * (base_y - top_y)
    # zero line (paper string)
    zy = Y(0)
    if tt > 0.8:
        d = ImageDraw.Draw(img)
        for x in range(120, W - 120, 26): d.line([(x, zy), (x + 14, zy)], fill=INK + (200,), width=4)
        ti = text_img("0°", 34, INK, weight="Bold"); img.alpha_composite(ti, (60, int(zy - 24)))
    cols = [COLD, RED, TEAL, MUSTARD]
    for i, it in enumerate(items):
        cx = W / 2 + (i - (n - 1) / 2) * 340
        u = ease_out(clamp((tt - 1.0 - i * 0.5) / 0.8))
        v = it["value"] * u
        y0, y1 = sorted([zy, Y(v)])
        if y1 - y0 > 4:
            p = poly_piece(("bar", i, int(y0), int(y1)), rect_poly(cx - 115, y0, cx + 115, y1), cols[i], amp=1.5)
            paste(img, p, *jit(("bar", i), tt, 1.5))
        if u > 0.05:
            dec = it.get("decimals", 0)
            s = f"{v:.{dec}f}{unit}"
            ty = (Y(v) - 110) if it["value"] >= 0 else (Y(v) + 20)
            put_text(img, s, 76, cx, ty, color=INK, weight="Black", stroke=6, stroke_col=WHITE, dx=jit(("bv", i), tt, 1.5)[0])
        lu = clamp((tt - 0.7 - i * 0.3) / 0.3)
        label(img, ("nm", i), it["name"], 52, cx, 1400, WHITE, INK, t=lu, rot=(-2 if i % 2 else 2), tstep=tt)
        if lu >= 1: put_text(img, it["sub"], 30, cx, 1450, color=INK, weight="Bold")
    if sc.get("note") and tt > 1.2:
        put_text(img, sc["note"], 32, W / 2, 1515, color=INK, weight="Bold", alpha=clamp((tt - 1.2) / 0.3))
    for j, cap in enumerate(sc.get("captions", [])):
        ca = sc.get("cap_at", [3.0, 99]); start = ca[j]; end = ca[j + 1] if j + 1 < len(sc["captions"]) else 99
        if start <= tt < end: caption(img, cap, tt - start, y=1640, key=f"m{j}")

def sc_tip(img, sc, spec, t, T):
    tt = stepf(t)
    header(img, spec, t)
    label(img, "tiplab", sc.get("label", "내일의 한마디"), 52, W / 2, 430, INK, WHITE, MUSTARD, t=clamp((tt - 0.2) / 0.3), rot=-2, tstep=tt)
    th = theme(sc.get("theme") or spec.get("thumb", {}).get("theme"))
    particles(img, th[3], tt, 1700, 8, seed=5)
    cp = icon_piece(sc.get("icon", "coat"), 420); u = ease_back(clamp((tt - 0.4) / 0.5))
    paste(img, (cp[0], W / 2 + cp[1], 860 + cp[2] + (1 - u) * 900), *jit("coat", tt, 2.5), rot=math.sin(tt * 3) * 3)
    label(img, "tiphead", sc["headline"], 72, W / 2, 1260, WHITE, INK, RED, t=clamp((tt - 1.0) / 0.35), rot=1, tstep=tt)
    if sc.get("sub"):
        label(img, "tipsub", sc["sub"], 46, W / 2, 1420, CREAM, INK, RED, t=clamp((tt - 1.7) / 0.35), rot=-1, tstep=tt)

def sc_outro(img, sc, spec, t, T):
    tt = stepf(t)
    sp = sun_piece(90); u = ease_back(clamp(tt / 0.6))
    paste(img, (sp[0], W / 2 + sp[1], 560 + sp[2] + (1 - u) * 600), *jit("osun", tt, 2), rot=tt * 12)
    label(img, "obrand", spec["brand"], 110, W / 2, 900, MUSTARD, INK, t=clamp((tt - 0.4) / 0.4), rot=-2, tstep=tt)
    label(img, "octa", sc["cta"], 50, W / 2, 1080, WHITE, INK, RED, t=clamp((tt - 0.8) / 0.35), rot=1, tstep=tt)
    if tt > 1.2:
        a = clamp((tt - 1.2) / 0.4)
        put_text(img, "자료: " + " · ".join(sc["sources"]), 34, W / 2, 1230, color=WHITE, weight="Bold", alpha=a)
        put_text(img, "모델 비교는 참고용 · 공식 예보는 기상청", 32, W / 2, 1290, color=WHITE, weight="Medium", alpha=a)

SCENES = {"rain_map": sc_rain_map, "hook": sc_hook, "map_temp": sc_map_temp, "map_region": sc_map_region, "models": sc_models, "tip": sc_tip, "outro": sc_outro}

TRANS = 0.4
def wipe(img, color, u, seed):
    """next scene's paper sheet sliding in from the right, torn leading edge"""
    if u <= 0: return
    x = W * (1 - ease_out(u)) - 20
    r = random.Random(seed); pts = [(W + 60, -60), (x, -60)]
    for i in range(0, H + 80, 40): pts.append((x + r.uniform(-18, 18), i))
    pts += [(x, H + 60), (W + 60, H + 60)]
    m = Image.new("L", (W, H), 0); ImageDraw.Draw(m).polygon(pts, fill=255)
    sh = m.filter(ImageFilter.GaussianBlur(10)).point(lambda v: int(v * 0.35))
    shl = Image.new("RGBA", (W, H), (40, 30, 20, 0)); shl.putalpha(sh)
    img.alpha_composite(shl, (-12, 0)) if False else img.paste(shl, (-12, 0), shl)
    sheet = Image.new("RGBA", (W, H), color + (255,)); sheet.putalpha(m)
    img.alpha_composite(sheet)

def scene_frame(spec, i, t):
    sc = spec["scenes"][i]; T = sc["dur"]
    img = bg(tuple(sc.get("bg", CREAM)))
    SCENES[sc["type"]](img, sc, spec, t, T)
    if i + 1 < len(spec["scenes"]) and t > T - TRANS:
        wipe(img, tuple(spec["scenes"][i + 1].get("bg", CREAM)), stepf(t - (T - TRANS)) / TRANS + 1 / (STEP_FPS * TRANS), i)
    return finish(img)

# ---------- thumbnail ----------
def make_thumb(spec, path):
    th = spec["thumb"]
    tm = theme(th.get("theme"))
    img = bg(tuple(th.get("bg", tm[0])))
    hills(img, tm, None, 1340)
    for i, (x, y, w) in enumerate([(200, 520, 280), (900, 1180, 240)]):
        cp = cloud_piece(w, WHITE if tm[4] else (228, 234, 242)); paste(img, (cp[0], x + cp[1], y + cp[2]))
    particles(img, tm[3], 0.7, 1500, 9, seed=9)
    label(img, "brand", spec["brand"], 48, W / 2, 120, MUSTARD, INK, rot=-2)
    label(img, "tdate", spec["thumb_date"], 72, W / 2, 260, WHITE, INK, rot=1.5)
    label(img, "ttop", th["top"], 96, W / 2, 470, INK, WHITE, MUSTARD, rot=-2)
    put_text(img, th["big"], 330, W / 2, 640, color=tm[2], weight="Black", stroke=18, stroke_col=WHITE)
    label(img, "tbot", th["bottom"], 104, W / 2, 1150, WHITE, INK, RED, rot=2)
    cp = icon_piece(th.get("icon", "sun"), 380); paste(img, (cp[0], W / 2 + cp[1], 1520 + cp[2]), rot=-6)
    Image.fromarray(finish(img)).save(path)

# ---------- audio ----------
def env_adsr(n, a, d, s, r_):
    e = np.ones(n) * s
    A, D, R = int(a * SR), int(d * SR), int(r_ * SR)
    e[:A] = np.linspace(0, 1, A) if A else e[:A]
    e[A:A + D] = np.linspace(1, s, len(e[A:A + D]))
    if R: e[-R:] *= np.linspace(1, 0, len(e[-R:]))
    return e

def pluck(freq, dur, bright=0.5, seed=0):
    n = int(dur * SR); N = int(SR / freq)
    rng = np.random.default_rng(seed)
    buf = np.convolve(rng.uniform(-1, 1, N + 8), np.hanning(7) / np.hanning(7).sum(), 'same')[4:N + 4] * 2.2
    out = np.zeros(n)
    for i in range(n):
        out[i] = buf[i % N]
        buf[i % N] = 0.5 * (buf[i % N] + buf[(i + 1) % N]) * (0.996 if bright > 0.4 else 0.99)
    r = int(0.004 * SR); out[:r] *= np.linspace(0, 1, r)
    return out

def make_bgm(total, bpm=96):
    """밝은 종이 인형극 느낌: 우쿨렐레풍 플럭 + 글로켄 멜로디 + 부드러운 베이스 + 셰이커"""
    beat = 60 / bpm; n = int((total + 6) * SR); mix = np.zeros(n)
    A4 = 440.0
    def f(m): return A4 * 2 ** ((m - 69) / 12)
    prog = [[60, 64, 67, 72], [55, 59, 62, 67], [57, 60, 64, 69], [53, 57, 60, 65]]  # C G Am F
    bass = [36, 43, 45, 41]
    mel = [76, 79, 81, 79, 76, 74, 72, 74, 76, 74, 72, 69, 67, 69, 72, 74]
    cache = {}
    def P(m, d, seed):
        k = (m, round(d, 3))
        if k not in cache: cache[k] = pluck(f(m), d, seed=seed)
        return cache[k]
    t = 0.0; bar = 0
    while t < total + 1:
        ch = prog[bar % 4]
        # strum pattern: down on 1, 2&, 3, 4
        for off in (0, 1.5, 2, 3):
            st = int((t + off * beat) * SR)
            for j, m in enumerate(ch):
                s = P(m, 1.2, j); a = st + int(j * 0.012 * SR)
                if a < n: mix[a:a + len(s)][:n - a] += s[:n - a] * 0.18
        # bass
        b = bass[bar % 4]; dur = beat * 4
        nn = int(dur * SR); tt_ = np.arange(nn) / SR
        bs = (np.sin(2 * np.pi * f(b) * tt_) + 0.3 * np.sin(4 * np.pi * f(b) * tt_)) * env_adsr(nn, 0.01, 0.3, 0.5, 0.2)
        st = int(t * SR); mix[st:st + nn][:n - st] += bs[:n - st] * 0.22
        # glock melody from bar 2
        if bar >= 1:
            for k in range(4):
                m = mel[(bar * 4 + k) % len(mel)]
                if (bar + k) % 3 == 2: continue
                nn = int(0.9 * SR); tt_ = np.arange(nn) / SR
                g = (np.sin(2 * np.pi * f(m) * tt_) + 0.25 * np.sin(2 * np.pi * f(m) * 2.76 * tt_)) * np.exp(-tt_ * 5)
                g[:132] *= np.linspace(0, 1, 132)
                st = int((t + k * beat) * SR); mix[st:st + nn][:n - st] += g[:n - st] * 0.10
        # shaker on 8ths, soft kick on 1 & 3
        for e in range(8):
            nn = int(0.06 * SR); sh = np.random.default_rng(bar * 8 + e).normal(0, 1, nn) * np.exp(-np.arange(nn) / SR * 60)
            sh = np.diff(sh, prepend=0)  # brighten
            st = int((t + e * beat / 2) * SR); mix[st:st + nn][:n - st] += np.convolve(sh, np.ones(3) / 3, 'same')[:n - st] * (0.02 if e % 2 else 0.012)
        for e in (0, 2):
            nn = int(0.25 * SR); tt_ = np.arange(nn) / SR
            k_ = np.sin(2 * np.pi * (50 + 60 * np.exp(-tt_ * 30)) * tt_) * np.exp(-tt_ * 14)
            st = int((t + e * beat) * SR); mix[st:st + nn][:n - st] += k_[:n - st] * 0.20
        t += beat * 4; bar += 1
    mix = mix[:int(total * SR)]
    fade = int(1.5 * SR); mix[-fade:] *= np.linspace(1, 0, fade); mix[:int(0.3 * SR)] *= np.linspace(0, 1, int(0.3 * SR))
    return mix

def sfx(kind, seed=0):
    rng = np.random.default_rng(seed)
    if kind == "swoosh":  # paper slide
        nn = int(0.45 * SR); tt_ = np.arange(nn) / SR
        no = rng.normal(0, 1, nn)
        # band-pass sweep via simple moving filters
        env = np.sin(np.pi * np.clip(tt_ / 0.45, 0, 1)) ** 2
        lp = np.convolve(no, np.ones(6) / 6, mode="same"); hp = no - np.convolve(no, np.ones(40) / 40, mode="same")
        mixk = np.clip(tt_ / 0.45, 0, 1)
        s = (lp * (1 - mixk) + hp * mixk * 0.6) * env
        # paper crackle
        cr = (rng.random(nn) > 0.996) * rng.normal(0, 3, nn); s += np.convolve(cr, np.hanning(30), "same") * env
        return s * 0.35
    if kind == "pop":  # paper tap / pop
        nn = int(0.12 * SR); tt_ = np.arange(nn) / SR
        s = np.sin(2 * np.pi * (300 + 500 * np.exp(-tt_ * 60)) * tt_) * np.exp(-tt_ * 45)
        s += rng.normal(0, 1, nn) * np.exp(-tt_ * 160) * 0.5
        return s * 0.35
    if kind == "tick":  # small paper tag
        nn = int(0.06 * SR); tt_ = np.arange(nn) / SR
        s = np.sin(2 * np.pi * 1400 * tt_) * np.exp(-tt_ * 120) + rng.normal(0, 1, nn) * np.exp(-tt_ * 300) * 0.4
        return s * 0.22
    if kind == "stamp":  # paper stamp thump
        nn = int(0.22 * SR); tt_ = np.arange(nn) / SR
        s = np.sin(2 * np.pi * (90 + 120 * np.exp(-tt_ * 40)) * tt_) * np.exp(-tt_ * 20)
        s += np.convolve(rng.normal(0, 1, nn), np.ones(8) / 8, "same") * np.exp(-tt_ * 50) * 0.8
        return s * 0.5
    if kind == "chime":
        nn = int(1.2 * SR); tt_ = np.arange(nn) / SR; s = np.zeros(nn)
        for k, fr in enumerate([1046.5, 1318.5, 1568.0, 2093.0]):
            st = int(k * 0.07 * SR); seg = tt_[:nn - st]
            s[st:] += np.sin(2 * np.pi * fr * seg) * np.exp(-seg * 4) * 0.25
        return s * 0.5
    if kind == "rise":  # bar growing
        nn = int(0.8 * SR); tt_ = np.arange(nn) / SR
        fr = 300 + 500 * tt_ / 0.8
        s = np.sign(np.sin(2 * np.pi * np.cumsum(fr) / SR)) * 0.15 * np.exp(-((tt_ - 0.4) ** 2) * 6)
        s = np.convolve(s, np.ones(12) / 12, "same")
        return s * 0.5
    if kind == "whoosh_low":
        nn = int(0.6 * SR); tt_ = np.arange(nn) / SR
        s = np.convolve(rng.normal(0, 1, nn), np.ones(25) / 25, "same") * np.sin(np.pi * tt_ / 0.6) ** 2
        return s * 0.9
    return np.zeros(10)

def sfx_events(spec):
    ev = []; t0 = 0.0
    for i, sc in enumerate(spec["scenes"]):
        T = sc["dur"]; ty = sc["type"]
        ev += [(t0 + 0.0, "tick"), (t0 + 0.1, "tick")]
        if ty == "hook":
            ev += [(t0 + 0.25, "whoosh_low"), (t0 + 0.6, "pop"), (t0 + 0.9, "stamp"), (t0 + 1.5, "pop"), (t0 + 1.3, "chime")]
        elif ty == "map_temp":
            ev += [(t0 + 0.2, "pop")] + [(t0 + 0.05 + k * 0.035 * 1.0, "tick") for k in range(0, 17, 3)]
            ev += [(t0 + 0.9 + k * 0.09, "pop") for k in range(len(sc["cities"]))]
            for c in sc.get("cap_at", [2.3, 4.2])[:len(sc.get("captions", []))]: ev.append((t0 + c, "swoosh"))
        elif ty in ("map_region", "rain_map"):
            ev += [(t0 + 0.2, "pop")] + [(t0 + 1.0 + k * 0.05 * 2, "tick") for k in range(9)]
            ev += [(t0 + 1.1, "chime")]
            for c in sc.get("cap_at", [2.2])[:len(sc.get("captions", []))]: ev.append((t0 + c, "swoosh"))
        elif ty == "models":
            ev += [(t0 + 0.2, "pop"), (t0 + 0.4, "stamp")] + [(t0 + 1.0 + k * 0.5, "rise") for k in range(len(sc["items"]))]
            ev += [(t0 + 0.7 + k * 0.3, "pop") for k in range(len(sc["items"]))]
            for c in sc.get("cap_at", [3.0])[:len(sc.get("captions", []))]: ev.append((t0 + c, "swoosh"))
        elif ty == "tip":
            ev += [(t0 + 0.2, "pop"), (t0 + 0.4, "whoosh_low"), (t0 + 1.0, "stamp"), (t0 + 1.7, "pop")]
        elif ty == "outro":
            ev += [(t0 + 0.1, "whoosh_low"), (t0 + 0.4, "stamp"), (t0 + 0.8, "pop"), (t0 + 1.0, "chime")]
        if i + 1 < len(spec["scenes"]): ev.append((t0 + T - TRANS, "swoosh"))
        t0 += T
    return ev

def make_audio(spec, path):
    total = sum(s["dur"] for s in spec["scenes"])
    bgm = make_bgm(total)
    fx = np.zeros_like(bgm)
    for k, (t, kind) in enumerate(sfx_events(spec)):
        s = sfx(kind, k); a = int(t * SR)
        if a >= len(fx): continue
        fx[a:a + len(s)] += s[:len(fx) - a]
    bgm = bgm / (np.max(np.abs(bgm)) + 1e-9) * 0.42
    mix = bgm + fx * 0.9
    mix = np.tanh(mix * 1.1) * 0.9
    L = mix; R = np.roll(mix, int(0.004 * SR)) * 0.97
    st = np.stack([L, R], 1)
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(st, -1, 1) * 32000).astype("<i2").tobytes())

# ---------- main ----------
def main():
    spec = json.load(open(sys.argv[1], encoding="utf-8")); out = sys.argv[2]; os.makedirs(out, exist_ok=True)
    make_thumb(spec, os.path.join(out, "thumbnail.png"))
    if "--preview" in sys.argv:
        frames = []
        for i, sc in enumerate(spec["scenes"]):
            frames.append(Image.fromarray(scene_frame(spec, i, sc.get("preview_t", sc["dur"] - TRANS - 0.1))).resize((360, 640)))
        cols = 4; rows = math.ceil(len(frames) / cols)
        sheet = Image.new("RGB", (cols * 370 + 10, rows * 650 + 10), (30, 30, 30))
        for k, f in enumerate(frames): sheet.paste(f, (10 + (k % cols) * 370, 10 + (k // cols) * 650))
        sheet.save(os.path.join(out, "preview.png")); print("preview ->", out); return
    if "--frame" in sys.argv:  # --frame i t
        k = sys.argv.index("--frame"); i, t = int(sys.argv[k + 1]), float(sys.argv[k + 2])
        Image.fromarray(scene_frame(spec, i, t)).save(os.path.join(out, f"frame_{i}_{t}.png")); return
    tmp = os.path.join(out, "_scenes"); os.makedirs(tmp, exist_ok=True); parts = []
    for i, sc in enumerate(spec["scenes"]):
        nf = int(round(sc["dur"] * FPS)); fn = os.path.join(tmp, f"s{i:02d}.mp4"); tfn = os.path.join(tmp, f"s{i:02d}.tmp.mp4")
        p = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                              "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                              "-pix_fmt", "yuv420p", "-r", str(FPS), tfn], stdin=subprocess.PIPE)
        last_key, last = None, None
        for f in range(nf):
            t = f / FPS; key = math.floor(t * STEP_FPS)
            if key != last_key: last = scene_frame(spec, i, t).tobytes(); last_key = key
            p.stdin.write(last)
        p.stdin.close()
        if p.wait() != 0: raise SystemExit(f"scene {i} encode failed")
        os.replace(tfn, fn); parts.append(fn); print(f"  scene {i} ok ({sc['type']})", flush=True)
    with open(os.path.join(tmp, "list.txt"), "w") as f:
        for p_ in parts: f.write(f"file '{os.path.abspath(p_)}'\n")
    silent = os.path.join(tmp, "silent.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", os.path.join(tmp, "list.txt"), "-c", "copy", silent], check=True)
    wav = os.path.join(out, "audio_bgm_sfx.wav"); make_audio(spec, wav)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", silent, "-i", wav, "-map", "0:v", "-map", "1:a", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart", os.path.join(out, "video.mp4")], check=True)
    print("done ->", os.path.join(out, "video.mp4"))

if __name__ == "__main__":
    main()
