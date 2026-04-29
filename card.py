from PIL import Image, ImageDraw, ImageFont
import aiohttp, io, math, re
from datetime import datetime


MONO_B = "fonts/Minecraft.ttf"
MONO   = "fonts/Minecraft.ttf"

def _f(size, bold=False):
    return ImageFont.truetype(MONO_B if bold else MONO, size)

FONT_TITLE = _f(22, bold=True)
FONT_LG    = _f(14, bold=True)
FONT       = _f(13)
FONT_SM    = _f(11)

# ── Palette ────────────────────────────────────────────────────────────────────
PANEL  = (28,  31,  38)
BORDER = (45,  49,  60)
WHITE  = (215, 220, 232)
GRAY   = (120, 128, 145)
GREEN  = (85,  255, 100)
GOLD   = (255, 175,  0)
RED    = (255,  75,  75)
YELLOW = (255, 215,  45)
CYAN   = (85,  255, 255)
DARK   = (55,  60,  72)


MC_COLORS = {
    '0':(0,0,0),       '1':(0,0,170),     '2':(0,170,0),     '3':(0,170,170),
    '4':(170,0,0),     '5':(170,0,170),   '6':(255,170,0),   '7':(170,170,170),
    '8':(85,85,85),    '9':(85,85,255),   'a':(85,255,85),   'b':(85,255,255),
    'c':(255,85,85),   'd':(255,85,255),  'e':(255,255,85),  'f':(255,255,255),
}

def _parse_mc(raw: str):
    """Return the RGB of the FIRST Minecraft color code found in raw."""
    m = re.search(r'[§&]([0-9a-fA-F])', raw or "")
    return MC_COLORS.get(m.group(1).lower()) if m else None


def _rank_label(p: dict) -> str:
    """Use the prefix field directly, e.g. '[VIP]'. Strip color codes."""
    rank   = p.get("rank") or {}
    prefix = rank.get("prefix", "").strip()

    prefix = re.sub(r'[§&][0-9a-fA-Flkmnor]', '', prefix).strip()
    return prefix 

def _rank_color(p: dict) -> tuple:
    """Parse color from prefixRaw (§a[VIP] → green). Fallback to gray."""
    rank = p.get("rank") or {}
    col  = _parse_mc(rank.get("prefixRaw", ""))
    if col and col != (0, 0, 0):
        return col
    # last resort fallback
    return (170, 170, 170)



BAR_FILL  = MC_COLORS['b']   
BAR_EMPTY = MC_COLORS['7']  
BAR_CHAR  = '■'

def _prestige_data(stars: int):
    """Return (color, symbol) based on Hypixel prestige tiers."""
    
    tier = (stars // 100) * 100

    PRESTIGE = {
        100:  (MC_COLORS['7'], '✫'),
        200:  (MC_COLORS['6'], '✫'),
        300:  (MC_COLORS['b'], '✫'),
        400:  (MC_COLORS['2'], '✫'),
        500:  (MC_COLORS['3'], '✫'),
        600:  (MC_COLORS['4'], '✫'),
        700:  (MC_COLORS['d'], '✫'),
        800:  (MC_COLORS['9'], '✫'),
        900:  (MC_COLORS['5'], '✫'),

        1000: (MC_COLORS['c'], '✪'),
        1100: (MC_COLORS['7'], '✪'),
        1200: (MC_COLORS['f'], '✪'),
        1300: (MC_COLORS['b'], '✪'),
        1400: (MC_COLORS['2'], '✪'),
        1500: (MC_COLORS['3'], '✪'),
        1600: (MC_COLORS['4'], '✪'),
        1700: (MC_COLORS['d'], '✪'),
        1800: (MC_COLORS['1'], '✪'),
        1900: (MC_COLORS['5'], '✪'),

        2000: (MC_COLORS['7'], '✪'),
        2100: (MC_COLORS['e'], '✪'),
        2200: (MC_COLORS['6'], '✪'),
        2300: (MC_COLORS['d'], '✪'),
        2400: (MC_COLORS['f'], '✪'),
        2500: (MC_COLORS['a'], '✪'),
        2600: (MC_COLORS['5'], '✪'),
        2700: (MC_COLORS['8'], '✪'),
        2800: (MC_COLORS['e'], '✪'),
        2900: (MC_COLORS['9'], '✪'),

        3000: (MC_COLORS['c'], '✪'),
        3100: (MC_COLORS['b'], '✪'),
        3200: (MC_COLORS['6'], '✪'),
        3300: (MC_COLORS['d'], '✪'),
        3400: (MC_COLORS['2'], '✪'),
        3500: (MC_COLORS['a'], '✪'),
        3600: (MC_COLORS['9'], '✪'),
        3700: (MC_COLORS['3'], '✪'),
        3800: (MC_COLORS['5'], '✪'),
        3900: (MC_COLORS['1'], '✪'),

        4000: (MC_COLORS['d'], '✪'),
        4100: (MC_COLORS['e'], '✪'),
        4200: (MC_COLORS['b'], '✪'),
        4300: (MC_COLORS['d'], '✪'),
        4400: (MC_COLORS['a'], '✪'),
        4500: (MC_COLORS['7'], '✪'),
        4600: (MC_COLORS['b'], '✪'),
        4700: (MC_COLORS['4'], '✪'),
        4800: (MC_COLORS['d'], '✪'),
        4900: (MC_COLORS['a'], '✪'),

        5000: (MC_COLORS['c'], '✪'),
    }


    keys = sorted(PRESTIGE.keys())
    for k in reversed(keys):
        if stars >= k:
            return PRESTIGE[k]

    return (MC_COLORS['7'], '✫')


def _ratio_color(val: float) -> tuple:
    if val >= 2.0: return GREEN
    if val >= 1.0: return YELLOW
    return RED


def _fmt(n) -> str:
    try:    return f"{int(n):,}"
    except: return str(n)

def _safe(a, b) -> float:
    return round(a / b, 2) if b else float(a)

def _rrect(d, x1, y1, x2, y2, r, fill, outline=None, ow=1):
    d.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=fill, outline=outline, width=ow)


def _guild_tag(p: dict) -> str:
    guild = p.get("guild") or {}
    tag   = guild.get("tag", "").strip()
    return f"[{tag}]" if tag else "None"

# ── Skin fetch 
async def _fetch_skin(uuid: str):
    for url in [
        f"https://nmsr.nickac.dev/fullbody/{uuid}",
        f"https://crafatar.com/renders/body/{uuid}?overlay&scale=3",
    ]:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if r.status == 200:
                        return Image.open(io.BytesIO(await r.read())).convert("RGBA")
        except Exception:
            pass
    return None


def _resolve_ov(p: dict, mode: str = "overall") -> dict:
    gs = p.get("groupStats") or {}
    if mode == "overall":
        return gs.get("overall") or {
            "wins": p.get("wins", 0), "losses": p.get("losses", 0),
            "kills": p.get("kills", 0), "finalKills": p.get("finalKills", 0),
            "deaths": p.get("deaths", 0), "finalDeaths": p.get("finalDeaths", 0),
            "bedsBroken": p.get("bedsBroken", 0), "gamesPlayed": p.get("gamesPlayed", 0),
            "winstreak": 0, "highestWinstreak": 0,
            "wlr": p.get("wlr", 0), "fkdr": p.get("fkdr", 0), "kdr": p.get("kdr", 0),
        }
    return (gs.get("groups") or {}).get(mode)

# ── Main card
async def build_card(p: dict, mode: str = "overall") -> io.BytesIO:
    W, H = 620, 450
    PAD  = 12

    ov = _resolve_ov(p, mode)
    if ov is None:
        ov   = _resolve_ov(p, "overall")
        mode = "overall"

    stars      = p.get("stars", 0)
    level      = p.get("level", 0)
    xp         = p.get("xp", 0)
    next_stars = stars + 1


    BAR_TOTAL  = 10
    BAR_FILLED = stars % BAR_TOTAL
    if BAR_FILLED == 0 and stars > 0:
        BAR_FILLED = BAR_TOTAL  # completed a group of 10

    rank_label = _rank_label(p)
    rank_col   = _rank_color(p)
    star_col, sym       = _prestige_data(stars)
    next_col, next_sym  = _prestige_data(next_stars)

    guild_tag  = _guild_tag(p)
    first      = p.get("firstPlay")
    first_s    = datetime.utcfromtimestamp(first / 1000).strftime("%m/%d/%y") if first else "N/A"
    online     = p.get("isOnline", False)
    status_col = (85, 255, 85) if online else (255, 85, 85)
    status_str = "Online" if online else "Offline"

    wlr  = ov.get("wlr",  _safe(ov.get("wins", 0),       ov.get("losses", 1)))
    fkdr = ov.get("fkdr", _safe(ov.get("finalKills", 0),  ov.get("finalDeaths", 1)))
    kdr  = ov.get("kdr",  _safe(ov.get("kills", 0),       ov.get("deaths", 1)))
    bblr = _safe(ov.get("bedsBroken", 0), ov.get("deaths", 1))

    # ── Canvas ────────────────────────────────────────────────────────────────
    img   = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d     = ImageDraw.Draw(img)
    PFILL = PANEL  + (240,)
    PBORD = BORDER + (255,)


    prefix = f"{rank_label} " if rank_label else ""
    uname  = p["username"]
    r_w    = d.textlength(prefix, font=FONT_TITLE) if prefix else 0
    n_w    = d.textlength(uname,  font=FONT_TITLE)
    hx     = int((W - r_w - n_w) // 2)
    if prefix:
        d.text((hx,       14), prefix, font=FONT_TITLE, fill=rank_col)
    d.text((hx + r_w, 14), uname, font=FONT_TITLE, fill=rank_col)


    if mode != "overall":
        ml  = f"[ {mode} ]"
        mlw = d.textlength(ml, font=FONT_SM)
        d.text((int((W - mlw) // 2), 40), ml, font=FONT_SM, fill=GRAY)

    # ── STAR BAR 
    bar_y = 48 if mode == "overall" else 56

    lbl_l = f"[{stars}{sym}]"
    lbl_r = f"[{next_stars}{next_sym}]"

    FONT_BAR = _f(18, bold=True)

    lbl_lw = d.textlength(lbl_l, font=FONT_BAR)
    lbl_rw = d.textlength(lbl_r, font=FONT_BAR)

    BAR_TOTAL = 20
    BAR_FILLED = stars % BAR_TOTAL
    if BAR_FILLED == 0 and stars > 0:
        BAR_FILLED = BAR_TOTAL


    BLOCK_W = 10
    BLOCK_H = 14
    BLOCK_GAP = 2

    bar_width = (BLOCK_W + BLOCK_GAP) * BAR_TOTAL

    gap = 12
    total_w = lbl_lw + gap + bar_width + gap + lbl_rw
    sx = int((W - total_w) // 2)

    # Left label
    d.text((sx, bar_y), lbl_l, font=FONT_BAR, fill=star_col)

    # Draw blocks
    bx = sx + lbl_lw + gap
    by = bar_y + 2  # small vertical align tweak

    for i in range(BAR_TOTAL):
        color = BAR_FILL if i < BAR_FILLED else BAR_EMPTY
        x1 = bx + i * (BLOCK_W + BLOCK_GAP)
        y1 = by
        x2 = x1 + BLOCK_W
        y2 = y1 + BLOCK_H

        d.rectangle([x1, y1, x2, y2], fill=color)

    # Right label
    d.text((bx + bar_width + gap, bar_y), lbl_r, font=FONT_BAR, fill=next_col)

    # ── BOX LAYOUT ────────────────────────────────────────────────────────────
    TOP   = 70
    SKIN  = (PAD,  TOP,   158, 400)
    RAT   = (165,  TOP,   348, 192)
    BIG   = (355,  TOP, W-PAD, 192)
    LVL   = (165,  200,   348, 295)
    WST   = (355,  200, W-PAD, 295)
    STATS = (165,  303, W-PAD, 400)
    F_L   = (PAD,  408,   190, 440)
    F_M   = (198,  408,   395, 440)
    F_R   = (403,  408, W-PAD, 440)

    for box in [SKIN, RAT, BIG, LVL, WST, STATS, F_L, F_M, F_R]:
        _rrect(d, *box, 7, PFILL, PBORD)

    def row(bx1, bx2, y, label, value, vcol=WHITE, fnt=FONT):
        d.text((bx1 + 10, y), label, font=fnt, fill=GRAY)
        vs = str(value)
        vw = d.textlength(vs, font=fnt)
        d.text((bx2 - vw - 10, y), vs, font=fnt, fill=vcol)

    # ── RATIOS ────────────────────────────────────────────────────────────────
    ry = RAT[1] + 10
    row(RAT[0], RAT[2], ry,     "WLR:",  f"{wlr:.2f}",  _ratio_color(wlr))
    row(RAT[0], RAT[2], ry+26,  "FKDR:", f"{fkdr:.2f}", _ratio_color(fkdr))
    row(RAT[0], RAT[2], ry+52,  "KDR:",  f"{kdr:.2f}",  _ratio_color(kdr))
    row(RAT[0], RAT[2], ry+78,  "BBLR:", f"{bblr:.2f}", _ratio_color(bblr))

    # ── BIG NUMBERS ───────────────────────────────────────────────────────────
    by2 = BIG[1] + 10
    row(BIG[0], BIG[2], by2,    "Wins:",   _fmt(ov.get("wins", 0)),       GREEN)
    row(BIG[0], BIG[2], by2+26, "Finals:", _fmt(ov.get("finalKills", 0)), GREEN)
    row(BIG[0], BIG[2], by2+52, "Kills:",  _fmt(ov.get("kills", 0)),      GOLD)
    row(BIG[0], BIG[2], by2+78, "Beds:",   _fmt(ov.get("bedsBroken", 0)), GOLD)

    # ── LEVEL / GUILD ─────────────────────────────────────────────────────────
    ly = LVL[1] + 10
    row(LVL[0], LVL[2], ly,     "Level:", str(level),   YELLOW)
    row(LVL[0], LVL[2], ly+26,  "XP:",    _fmt(xp),     WHITE)
    row(LVL[0], LVL[2], ly+52,  "Guild:", guild_tag,     CYAN)

    # ── WINSTREAKS ────────────────────────────────────────────────────────────
    d.text((WST[0]+10, WST[1]+10), "Winstreaks", font=FONT_LG, fill=WHITE)
    wy = WST[1] + 36
    row(WST[0], WST[2], wy,    "Current:", str(ov.get("winstreak", 0)),
        GREEN if ov.get("winstreak", 0) > 0 else GRAY)
    row(WST[0], WST[2], wy+26, "Best:",    str(ov.get("highestWinstreak", 0)), YELLOW)

    # ── BOTTOM STATS ──────────────────────────────────────────────────────────
    sty = STATS[1] + 10
    mid = STATS[0] + (STATS[2] - STATS[0]) // 2
    row(STATS[0], mid,      sty,    "Wins:",         _fmt(ov.get("wins", 0)),        GREEN)
    row(STATS[0], mid,      sty+26, "Final Kills:",  _fmt(ov.get("finalKills", 0)),  GREEN)
    row(STATS[0], mid,      sty+52, "Total Kills:",  _fmt(ov.get("kills", 0)),       WHITE)
    row(mid,      STATS[2], sty,    "Losses:",       _fmt(ov.get("losses", 0)),      RED)
    row(mid,      STATS[2], sty+26, "Final Deaths:", _fmt(ov.get("finalDeaths", 0)), RED)
    row(mid,      STATS[2], sty+52, "Beds Broken:",  _fmt(ov.get("bedsBroken", 0)),  GOLD)

    # ── FOOTER ────────────────────────────────────────────────────────────────
    d.text((F_L[0]+8, F_L[1]+4),  "HellCore", font=FONT, fill=GOLD)
    d.text((F_L[0]+8, F_L[1]+20), f"Rank: {rank_label or 'Member'}", font=FONT_SM, fill=rank_col)

    row(F_M[0], F_M[2], F_M[1]+4,  "Status:",      status_str, status_col, FONT_SM)
    row(F_M[0], F_M[2], F_M[1]+20, "First Login:",  first_s,    GRAY,       FONT_SM)

    row(F_R[0], F_R[2], F_R[1]+4,  "Games:",     _fmt(ov.get("gamesPlayed", 0)), GRAY, FONT_SM)
    row(F_R[0], F_R[2], F_R[1]+20, "Beds Lost:", "N/A",                          GRAY, FONT_SM)

    # ── SKIN ──────────────────────────────────────────────────────────────────
    skin = await _fetch_skin(p.get("uuid", ""))
    if skin:
        skin_h = SKIN[3] - SKIN[1] - 22
        skin_w = int(skin.width * skin_h / skin.height)
        skin   = skin.resize((skin_w, skin_h), Image.LANCZOS)
        sx2    = SKIN[0] + (SKIN[2] - SKIN[0] - skin_w) // 2
        img.paste(skin, (sx2, SKIN[1] + 6), skin)

    lbl = f"({rank_label or 'Member'})"
    lw2 = d.textlength(lbl, font=FONT_SM)
    d.text((SKIN[0] + (SKIN[2]-SKIN[0]-lw2)//2, SKIN[3]-18), lbl, font=FONT_SM, fill=rank_col)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
