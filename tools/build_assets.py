#!/usr/bin/env python3
"""Render every SVG the profile README shows, once per GitHub colour scheme.

The README picks a variant with <picture media="(prefers-color-scheme: ...)">.
Nothing is fetched when someone opens the profile: the workflow publishes the
output to the `output` branch, so an outage elsewhere can never blank it.

Stdlib only. GITHUB_TOKEN feeds the activity card's GraphQL calls; without it
every other asset still builds and the card is skipped.

Adapted from Chirudeva-Reddy's profile generator, with permission.
"""

import json
import os
import sys
import textwrap
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from icons import brand, glyph                              # noqa: E402

USER = os.environ.get("PROFILE_USER", "abdulaz06")
OUT = os.environ.get("OUT_DIR", "dist")
API = "https://api.github.com/graphql"

MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
SANS = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, "
        "Arial, sans-serif")

# Monospace advance is a fixed 0.6em, and every monospace run is pinned with
# textLength, so its width is exact in whatever font the reader has.
MONO_ADVANCE = 0.60

# Sans runs cannot be pinned without distorting them, so they are measured
# against Helvetica's AFM widths (also Arial's and Liberation Sans'), which run
# at or above Segoe UI and San Francisco. Used only to check that a run fits.
_HELV = (" !\"#$%&'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ"
         "[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~")
_HELV_REGULAR = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278,
    278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584,
    584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556,
    833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278,
    278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222,
    500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500,
    500, 334, 260, 334, 584]
_HELV_BOLD = [
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278,
    278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584,
    584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611,
    833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333,
    278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278,
    556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556,
    500, 389, 280, 389, 584]
_WIDTHS = {False: dict(zip(_HELV, _HELV_REGULAR)), True: dict(zip(_HELV, _HELV_BOLD))}
_EXTRA = {"·": 278, "–": 556, "—": 1000, "’": 278}


def sans_width(text, size, bold=False):
    table = _WIDTHS[bold]
    return sum(table.get(ch, _EXTRA.get(ch, 600)) for ch in text) * size / 1000.0


def mono_width(text, size):
    return len(text) * size * MONO_ADVANCE


def fit(text, size, room, bold=False, floor=0.7):
    """Largest font size, at most `size`, whose sans run fits in `room` px."""
    return max(round(min(size, size * room / sans_width(text, size, bold)), 2),
               size * floor)


# ---------------------------------------------------------------- palette --
# on_wave_muted is a step lighter than slate-300: under the wave's veils the
# role line fell to 4:1 against the teal end, and this keeps it above 4.5:1.

THEMES = {
    "": {  # light
        "accent": "#0F766E",
        "accent_soft": "#14B8A6",
        "text": "#1F2328",
        "muted": "#59636E",
        "line": "#D0D7DE",
        "surface": "#F6F8FA",
        "inset": "#FFFFFF",
        "page": "#FFFFFF",      # GitHub's page background, not drawn
        "wave_from": "#0B1F3A",
        "wave_to": "#0F766E",
        "on_wave": "#F8FAFC",
        "on_wave_muted": "#E2E8F0",
    },
    "-dark": {
        "accent": "#2DD4BF",
        "accent_soft": "#14B8A6",
        "text": "#E6EDF3",
        "muted": "#9198A1",
        "line": "#30363D",
        "surface": "#161B22",
        "inset": "#0D1117",
        "page": "#0D1117",
        "wave_from": "#07142A",
        "wave_to": "#0F766E",
        "on_wave": "#F8FAFC",
        "on_wave_muted": "#E2E8F0",
    },
}


def rgb(color):
    """(r, g, b) from #RGB or #RRGGBB."""
    h = color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        raise ValueError("not a hex colour: %r" % (color,))
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(color, other, amount):
    return "#{:02X}{:02X}{:02X}".format(
        *(int(round(a + (b - a) * amount)) for a, b in zip(rgb(color), rgb(other))))


def luminance(color):
    """WCAG 2 relative luminance."""
    def lin(c):
        c /= 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(c) for c in rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def legible(color, background, ratio=3.0):
    """color, moved toward white or black just far enough to reach `ratio`
    against background, so a brand keeps its hue wherever it can. 3:1 is
    WCAG's floor for graphics."""
    if contrast(color, background) >= ratio:
        return color.upper()
    toward = "#FFFFFF" if is_dark(background) else "#000000"
    for step in range(1, 41):
        moved = mix(color, toward, step / 40.0)
        if contrast(moved, background) >= ratio:
            return moved
    return toward


def is_dark(background):
    return luminance(background) < 0.18


def mark_colors(color, background):
    """(fill, outline) for a brand mark on background.

    On a dark card a sunken mark (Rust, Angular, SQLite) is lifted toward
    white, the way those projects draw themselves on dark, and to 4:1 rather
    than 3:1: at the bare minimum Java and Flutter read as disabled next to
    the brighter marks. On a light card a pale mark (JavaScript, Linux, Tauri)
    keeps its own fill and gets a hairline in a darker shade of itself;
    pushing the fill to 3:1 turned the yellows olive, and the yellow is how
    people recognise them.
    """
    if is_dark(background):
        return legible(color, background, 4.0), None
    if contrast(color, background) >= 3.0:
        return color.upper(), None
    return color.upper(), legible(color, background)


# ---------------------------------------------------------------- content --

NAME = "Abdul Azeem Asharaf Ali"
ROLE = "Software Engineer · Python · Java · Rust · SQL"

TYPING_LINES = [
    "Abdul Azeem Asharaf Ali",
    "CS + Math at UIC",
    "Backend, systems, and data pipelines",
    "Ports and logistics taught me what reliable means",
    "Open to SWE internships and full-time roles",
]
TYPING_REST = 4     # the line a renderer without SMIL shows

FOOTER_LINES = [
    "Thanks for scrolling this far",
    "Open to SWE internships and full-time roles",
    "Backend, systems, and data tooling",
    "abdulazeemasharafali306@gmail.com",
]
FOOTER_REST = 3

# (file key, label, Simple Icons slug). The README wraps each in its link.
SOCIAL = [
    ("linkedin", "LinkedIn", "linkedin"),
    ("email", "Email", "gmail"),
]

# (heading, [(label, Simple Icons slug)]). Colours come from icons.py. SQL has
# no Simple Icons mark, so it is named in the prose rather than drawn here.
STACK = [
    ("Languages",
     [("Python", "python"), ("Java", "java"), ("Rust", "rust"),
      ("TypeScript", "typescript"), ("JavaScript", "javascript"),
      ("Dart", "dart")]),
    ("Frameworks",
     [("FastAPI", "fastapi"), ("Flutter", "flutter"), ("Angular", "angular"),
      ("Tauri", "tauri"), ("Frappe", "frappe")]),
    ("Data and tooling",
     [("PostgreSQL", "postgresql"), ("SQLite", "sqlite"), ("WebGL", "webgl"),
      ("Git", "git"), ("Linux", "linux"), ("GitHub Actions", "githubactions")]),
]


class Project(NamedTuple):
    key: str            # asset file name: project-<key>[-dark].svg
    name: str
    repo: str           # github.com/USER/<repo>; every card links there
    figure: str         # headline number, as the repo states or measures it
    figure_label: str
    desc: str
    tags: tuple


# Figures are checked against each repo: Sonora's FFT_SIZE and 16 ms analysis
# loop, the vessel dashboard's measured parse-plus-render time for its
# 50-vessel fixture, and the invoicing app's print_format fixtures.
PROJECTS = [
    Project("sonora", "Sonora", "sonora",
            "4,096-pt FFT", "Hann-windowed, ~60 analysis updates/s",
            "Captures system audio through WASAPI loopback, analyses it in "
            "Rust, and drives three WebGL 2 scenes with bloom.",
            ("Rust", "Tauri 2", "WebGL 2", "WASAPI")),
    Project("vessel-dashboard", "Vessel Intelligence Dashboard",
            "vessel-intelligence-dashboard",
            "< 2 s", "Excel workbook to board-ready dashboard",
            "One self-contained HTML file with zero network requests, so "
            "confidential fleet reports never leave the browser.",
            ("JavaScript", "SheetJS", "single-file")),
    Project("frappe-invoicing", "Frappe Invoicing Platform",
            "frappe-invoicing-platform",
            "3 print formats", "tax invoice, credit note and receipt",
            "ERPNext extension for service invoicing: VAT templates, reverse "
            "charge, and numbering that survives migrations.",
            ("Python", "Frappe", "ERPNext")),
]


# ------------------------------------------------------------- svg basics --

def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def num(v):
    """A coordinate in its shortest faithful form: 12, 12.5, 12.35."""
    if isinstance(v, float):
        v = round(v, 2)
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def el(tag, body=None, **attrs):
    """One element. Keyword names map _ to - and __ to :, and drop a trailing
    _ (class_). None skips the attribute; body is inserted as given."""
    parts = [tag]
    for key, value in attrs.items():
        if value is None:
            continue
        name = key.rstrip("_").replace("__", ":").replace("_", "-")
        parts.append('{}="{}"'.format(name, esc(num(value))))
    head = " ".join(parts)
    return "<{}/>".format(head) if body is None else "<{}>{}</{}>".format(head, body, tag)


def svg(width, height, body, title):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            'viewBox="0 0 {w} {h}" role="img" aria-label="{t}">'
            '<title>{t}</title>{b}</svg>'.format(w=num(width), h=num(height),
                                                 t=esc(title), b=body))


def mono(x, y, text, size, color, weight=None, anchor=None):
    """A monospace run pinned to its computed width."""
    return el("text", esc(text), x=x, y=y, font_family=MONO, font_size=size,
              font_weight=weight, fill=color, text_anchor=anchor,
              textLength=mono_width(text, size), lengthAdjust="spacing",
              xml__space="preserve")


def sans(x, y, text, size, color, weight=None, anchor=None, spacing=None):
    return el("text", esc(text), x=x, y=y, font_family=SANS, font_size=size,
              font_weight=weight, fill=color, text_anchor=anchor,
              letter_spacing=spacing)


def eyebrow(x, y, text, p, anchor=None):
    """The small spaced capitals that head each panel."""
    return sans(x, y, text.upper(), 12, p["muted"], 700, anchor, "0.08em")


def arrow(cx, cy, size, color):
    """A north-east arrow, drawn rather than typed so it never depends on the
    reader's font having U+2197."""
    h = size / 2.0
    d = "M{},{}L{},{}M{},{}H{}V{}".format(
        num(cx - h), num(cy + h), num(cx + h), num(cy - h),
        num(cx - h * 0.2), num(cy - h), num(cx + h), num(cy + h * 0.2))
    return el("path", d=d, fill="none", stroke=color, stroke_width=1.6,
              stroke_linecap="round", stroke_linejoin="round")


# ----------------------------------------------------------------- typing --

def ms(v):
    return "{}ms".format(int(round(v)))


def typing(lines, cx, baseline, size, color, caret_color, rest=0,
           type_ms=58, erase_ms=24, hold_ms=2300, gap_ms=420):
    """A typewriter loop in SMIL, which <img>-embedded SVG runs (it never runs
    script).

    Each line is revealed by a clip rect that steps one whole character at a
    time (calcMode discrete), so a glyph is never shown half drawn, then
    erased the same way. The lines are chained by syncbase: line i starts
    when line i-1 finishes erasing, and line 0 also restarts after the last,
    so there is no shared keyTimes table to keep in sync. One caret follows
    the active line; it holds solid while typing and blinks while idle.

    Without SMIL (link previews, static rasterisers) the base attributes
    apply: line `rest` is shown in full with the caret after it, and every
    other line is clipped to nothing.
    """
    adv = size * MONO_ADVANCE
    top, tall = baseline - size * 0.84, size * 1.14
    last = len(lines) - 1
    clips, texts, moves = [], [], []
    for i, line in enumerate(lines):
        n = len(line)
        width = n * adv
        x = cx - width / 2.0
        steps = [num(k * adv) for k in range(n + 1)]
        reach = [num(x + k * adv + 1) for k in range(n + 1)]
        tdur, edur = (n + 1) * type_ms, (n + 1) * erase_ms
        start = ("0s;e{}.end+{}ms".format(last, gap_ms) if i == 0
                 else "e{}.end+{}ms".format(i - 1, gap_ms))
        anim = (el("animate", id="t%d" % i, attributeName="width", begin=start,
                   dur=ms(tdur), calcMode="discrete", values=";".join(steps),
                   fill="freeze")
                + el("animate", id="e%d" % i, attributeName="width",
                     begin="t%d.end+%dms" % (i, hold_ms), dur=ms(edur),
                     calcMode="discrete", values=";".join(steps[::-1]),
                     fill="freeze"))
        if i == rest and i != 0:
            # Hide the resting line until its own turn once SMIL is running.
            anim = el("set", attributeName="width", to="0", begin="0s",
                      fill="freeze") + anim
        clips.append(el("clipPath", el(
            "rect", anim, x=x, y=top, height=tall,
            width=width + adv if i == rest else 0), id="c%d" % i))
        texts.append(el("text", esc(line), x=x, y=baseline,
                        clip_path="url(#c%d)" % i, font_family=MONO,
                        font_size=size, font_weight=500, fill=color,
                        textLength=width, lengthAdjust="spacing",
                        xml__space="preserve"))
        for sync, dur, values in (("t%d" % i, tdur, reach),
                                  ("e%d" % i, edur, reach[::-1])):
            moves.append(el("animate", attributeName="x", begin=sync + ".begin",
                            dur=ms(dur), calcMode="discrete",
                            values=";".join(values), fill="freeze"))
            moves.append(el("set", attributeName="opacity", to="1",
                            begin=sync + ".begin", dur=ms(dur)))
    blink = el("animate", attributeName="opacity", values="1;0",
               calcMode="discrete", dur="1100ms", repeatCount="indefinite")
    rest_end = cx + mono_width(lines[rest], size) / 2.0 + 1
    caret = el("rect", blink + "".join(moves), x=rest_end, y=top, width=2,
               height=tall, rx=1, fill=caret_color)
    return el("defs", "".join(clips)) + "".join(texts) + caret


# ------------------------------------------------------------------- wave --

def gradient_at(p, x, y, width, height):
    """Colour of the wave gradient at (x, y). It runs corner to corner in user
    space, so this is exact; the contrast test samples it under the text."""
    t = (x * width + y * height) / float(width * width + height * height)
    return mix(p["wave_from"], p["wave_to"], min(1.0, max(0.0, t)))


WAVE_TINTS = (0.06, 0.04)   # white veils over the band, deepest first


def wave(p, width, height, flip=False, radius=16):
    """A gradient band whose free edge is a wave, with rounded corners on the
    straight edge and faint sonar rings in one corner.

    Drawn flat-topped; the footer passes flip and gets the same band turned
    over, so its wave faces the text above it.
    """
    def band(right, c1, c2, left):
        return "M0,0H{w}V{r}C{x1},{a} {x2},{b} 0,{l}Z".format(
            w=num(width), r=num(right * height), x1=num(width * 0.70),
            a=num(c1 * height), x2=num(width * 0.30), b=num(c2 * height),
            l=num(left * height))

    body = band(0.80, 1.05, 0.60, 0.90)
    rings = "".join(
        el("circle", cx=width * 0.88, cy=height * 0.16, r=r, fill="none",
           stroke=p["on_wave"], stroke_opacity=0.075)
        for r in range(28, 360, 30))
    defs = (el("linearGradient",
               el("stop", offset=0, stop_color=p["wave_from"])
               + el("stop", offset=1, stop_color=p["wave_to"]),
               id="wg", gradientUnits="userSpaceOnUse",
               x1=0, y1=0, x2=width, y2=height)
            + el("clipPath", el("rect", width=width, height=height * 1.2,
                                rx=radius), id="wr")
            + el("clipPath", el("path", d=body), id="wb"))
    turn = ("rotate(180 {} {})".format(num(width / 2.0), num(height / 2.0))
            if flip else None)
    return el("defs", defs) + el(
        "g",
        el("path", d=body, fill="url(#wg)")
        + el("path", d=band(0.62, 0.86, 0.46, 0.72), fill="#FFFFFF",
             fill_opacity=WAVE_TINTS[0])
        + el("path", d=band(0.44, 0.68, 0.30, 0.54), fill="#FFFFFF",
             fill_opacity=WAVE_TINTS[1])
        + el("g", rings, clip_path="url(#wb)"),
        clip_path="url(#wr)", transform=turn)


# ----------------------------------------------------------- banner/footer --

BANNER = dict(width=900, height=250, band=168, title_y=80, title_size=44,
              role_y=114, role_size=15, typing_y=214, typing_size=19)
FOOTER = dict(width=900, height=186, band=122, typing_y=36, typing_size=15.5)


def build_banner(p):
    b = BANNER
    w = b["width"]
    title_size = fit(NAME, b["title_size"], w - 140, bold=True)
    body = [
        wave(p, w, b["band"]),
        sans(w / 2.0, b["title_y"], NAME, title_size, p["on_wave"], 700,
             "middle"),
        sans(w / 2.0, b["role_y"], ROLE, b["role_size"], p["on_wave_muted"],
             500, "middle", "0.02em"),
        typing(TYPING_LINES, w / 2.0, b["typing_y"], b["typing_size"],
               p["accent"], p["accent_soft"], rest=TYPING_REST),
    ]
    return svg(w, b["height"], "".join(body),
               "{}, software engineer. {}".format(NAME, ". ".join(TYPING_LINES[1:])))


def build_footer(p):
    f = FOOTER
    w = f["width"]
    body = [
        typing(FOOTER_LINES, w / 2.0, f["typing_y"], f["typing_size"],
               p["muted"], p["accent_soft"], rest=FOOTER_REST),
        el("g", wave(p, w, f["band"], flip=True),
           transform="translate(0,{})".format(f["height"] - f["band"])),
    ]
    return svg(w, f["height"], "".join(body), ". ".join(FOOTER_LINES[1:]))


# ------------------------------------------------------------------ chips --

CHIP_SIZE = 14.5    # label font size
CHIP_H = 38
CHIP_ICON = 19
CHIP_PAD = 14
CHIP_LEAD = CHIP_PAD + CHIP_ICON + 10    # glyph plus the gap before the label
CHIP_TAIL = 16
CHIP_ARROW = 18     # extra room for the link arrow on social pills
CHIP_GAP = 10
CHIP_ROW = CHIP_H + 10


def chip_width(label, link=False):
    return CHIP_LEAD + mono_width(label, CHIP_SIZE) + CHIP_TAIL + (CHIP_ARROW if link else 0)


def chip(x, y, label, slug, p, fill, link=False):
    """A rounded pill: brand glyph, label, and for links a trailing arrow.
    Returns (svg, width)."""
    w = chip_width(label, link)
    parts = [
        el("rect", x=x, y=y, width=w, height=CHIP_H, rx=CHIP_H / 2.0, fill=fill,
           stroke=p["line"]),
        glyph(slug, x + CHIP_PAD, y + (CHIP_H - CHIP_ICON) / 2.0, CHIP_ICON,
              *mark_colors(brand(slug), fill)),
        mono(x + CHIP_LEAD, y + CHIP_H / 2.0 + CHIP_SIZE * 0.36, label,
             CHIP_SIZE, p["text"]),
    ]
    if link:
        parts.append(arrow(x + w - CHIP_TAIL - 4, y + CHIP_H / 2.0, 8, p["muted"]))
    return el("g", "".join(parts), class_="chip"), w


def build_social(p, label, slug):
    """One pill per link, each its own file so the README can wrap it in an
    <a>; an <img>-embedded SVG cannot carry clickable regions of its own."""
    frag, w = chip(1, 1, label, slug, p, p["surface"], link=True)
    return svg(w + 2, CHIP_H + 2, frag, label)


PANEL_W = 900
PANEL_PAD = 26


def panel(w, h, p):
    return el("rect", x=0.5, y=0.5, width=w - 1, height=h - 1, rx=14,
              fill=p["surface"], stroke=p["line"])


def build_stack(p):
    w, pad = PANEL_W, PANEL_PAD
    y, body = pad + 12, []
    for heading, items in STACK:
        body.append(eyebrow(pad, y, heading, p))
        y += 14
        x = pad
        for label, slug in items:
            if x > pad and x + chip_width(label) > w - pad:   # wrap first
                x, y = pad, y + CHIP_ROW
            piece, cw = chip(x, y, label, slug, p, p["inset"])
            body.append(piece)
            x += cw + CHIP_GAP
        y += CHIP_H + 34
    h = y - 34 + pad
    names = [label for _, items in STACK for label, _ in items]
    return svg(w, h, panel(w, h, p) + "".join(body),
               "Tools I use: " + ", ".join(names))


# --------------------------------------------------------------- activity --

PROFILE_Q = """
query($login: String!, $after: String) {
  user(login: $login) {
    createdAt
    pullRequests { totalCount }
    repositories(first: 100, after: $after, ownerAffiliations: OWNER,
                 isFork: false, privacy: PUBLIC) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        stargazerCount
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

YEAR_FIELDS = ("{ totalCommitContributions contributionCalendar { "
               "totalContributions weeks { contributionDays { date "
               "contributionCount } } } }")


def graphql(query, variables):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set")
    req = urllib.request.Request(
        API, data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": "bearer " + token,
                 "Content-Type": "application/json",
                 "User-Agent": "profile-asset-builder"})
    with urllib.request.urlopen(req, timeout=45) as r:
        payload = json.load(r)
    if payload.get("errors"):
        raise RuntimeError("GraphQL: " + json.dumps(payload["errors"])[:400])
    return payload["data"]


def years_query(created, today):
    """Every calendar year since the account opened, as aliased fields of one
    request. contributionsCollection spans at most a year per call."""
    fields = []
    for year in range(created.year, today.year + 1):
        frm, to = max(created, date(year, 1, 1)), min(today, date(year, 12, 31))
        fields.append('y{}: contributionsCollection(from: "{}T00:00:00Z", '
                      'to: "{}T23:59:59Z") {}'.format(year, frm, to, YEAR_FIELDS))
    return "query($login: String!) { user(login: $login) { %s } }" % " ".join(fields)


class Streak(NamedTuple):
    length: int
    start: date = None
    end: date = None


def streaks(days, today=None):
    """(current, longest) runs of consecutive active days.

    Today counts once it has a contribution, but an empty today does not end
    the current run: the day is not over yet. A tie for longest goes to the
    more recent run.
    """
    today = today or date.today()
    longest, start, prev = Streak(0), None, None
    for d in sorted(date.fromisoformat(k) for k, n in days.items() if n > 0):
        if prev is None or d - prev != timedelta(days=1):
            start = d
        prev = d
        if (d - start).days + 1 >= longest.length:
            longest = Streak((d - start).days + 1, start, d)

    end = today if days.get(today.isoformat(), 0) > 0 else today - timedelta(days=1)
    cursor = end
    while days.get(cursor.isoformat(), 0) > 0:
        cursor -= timedelta(days=1)
    length = (end - cursor).days
    return (Streak(length, cursor + timedelta(days=1), end) if length else Streak(0)), longest


def fetch_stats(query=graphql, today=None):
    """Totals, streaks, stars and language mix for USER's public work.

    privacy: PUBLIC keeps private repositories out whoever's token runs this,
    so a local build shows what the workflow will publish. Two requests in
    the common case: the profile, then every year's calendar at once.
    """
    today = today or date.today()
    repos, after = [], None
    while True:
        user = query(PROFILE_Q, {"login": USER, "after": after})["user"]
        page = user["repositories"]
        repos += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
    created = date.fromisoformat(user["createdAt"][:10])

    years = query(years_query(created, today), {"login": USER})["user"]
    days, total, commits = {}, 0, 0
    for cc in years.values():
        total += cc["contributionCalendar"]["totalContributions"]
        commits += cc["totalCommitContributions"]
        for week in cc["contributionCalendar"]["weeks"]:
            for d in week["contributionDays"]:
                days[d["date"]] = d["contributionCount"]

    sizes, colors = {}, {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            sizes[name] = sizes.get(name, 0) + edge["size"]
            colors[name] = edge["node"]["color"] or "#8B949E"
    grand = sum(sizes.values()) or 1
    top = sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    current, longest = streaks(days, today)
    return {
        "total": total, "commits": commits,
        "stars": sum(r["stargazerCount"] for r in repos),
        "repos": page["totalCount"],
        "prs": user["pullRequests"]["totalCount"],
        "current": current, "longest": longest, "since": created,
        "today": today,
        "languages": [(n, s * 100.0 / grand, colors[n]) for n, s in top],
    }


def day(d, month=True, year=True):
    out = str(d.day)
    if month:
        out += d.strftime(" %b")
    if year:
        out += d.strftime(" %Y")
    return out


def span(s, today):
    """A streak as a compact date range: 3 – 9 Jun 2026, 28 May –
    3 Jun 2026, 28 Dec – 3 Jan 2026, or 4 Oct – today.

    The start's year is implied by the end's unless the run is long enough
    for the month order to be ambiguous, which keeps the caption inside its
    column.
    """
    if not s.length:
        return "none running"
    if s.length == 1:
        return "today" if s.end == today else day(s.end)
    if s.end == today:
        return "{} – today".format(day(s.start, year=s.length > 300))
    same_month = (s.start.year, s.start.month) == (s.end.year, s.end.month)
    return "{} – {}".format(
        day(s.start, month=not same_month, year=s.length > 300), day(s.end))


ACTIVITY_H = 262
METRIC_Y = 98


def build_activity(p, s):
    w, h, pad = PANEL_W, ACTIVITY_H, PANEL_PAD + 2
    body = [panel(w, h, p), eyebrow(pad, 40, "Contributions and streaks", p),
            mono(w - pad, 40, "@" + USER, 12.5, p["muted"], anchor="end")]

    cells = [
        (s["total"], "Contributions", "since " + day(s["since"]), True),
        (s["current"].length, "Current streak", span(s["current"], s["today"]), True),
        (s["longest"].length, "Longest streak", span(s["longest"], s["today"]), False),
        (s["commits"], "Commits", "all time", False),
        (s["stars"], "Stars earned", "across {} public repos".format(s["repos"]), False),
        (s["prs"], "Pull requests", "opened", False),
    ]
    step = (w - 2 * pad) / float(len(cells))
    for i, (value, label, note, hot) in enumerate(cells):
        cx = pad + step * (i + 0.5)
        body.append(sans(cx, METRIC_Y, "{:,}".format(value), 28,
                         p["accent"] if hot else p["text"], 700, "middle"))
        body.append(sans(cx, METRIC_Y + 22, label, 12.5, p["text"], 500, "middle"))
        body.append(sans(cx, METRIC_Y + 40, note, 11, p["muted"], None, "middle"))

    body.append(el("line", x1=pad, y1=164.5, x2=w - pad, y2=164.5,
                   stroke=p["line"]))
    body.append(eyebrow(pad, 196, "Top languages", p))
    bar_w, bar_y, x = w - 2 * pad, 210, pad
    # GitHub draws language colours raw on white in every repository sidebar,
    # so the light card keeps them unless one is near-white; on the dark card
    # several (C's #555555, CSS's #663399) sink, so they are lifted to 3:1.
    floor = 3.0 if is_dark(p["surface"]) else 1.25
    langs = [(n, pct, legible(c, p["surface"], floor)) for n, pct, c in s["languages"]]
    rest = 100.0 - sum(pct for _, pct, _ in langs)
    if rest >= 0.05:
        # The long tail gets a neutral segment, so the bar always spans the
        # card instead of stopping short for no visible reason.
        langs.append(("Other", rest, legible(p["line"], p["surface"], 1.6)))
    for name, pct, color in langs:
        seg = bar_w * pct / 100.0
        body.append(el("rect", x=x, y=bar_y, width=max(seg - 3, 2), height=10,
                       rx=5, fill=color))
        x += seg
    # Legend runs are monospace and pinned, so their widths are exact and the
    # tail is dropped rather than drawn past the card.
    lx = pad
    for name, pct, color in langs:
        label = "{} {:.1f}%".format(name, pct)
        tw = mono_width(label, 12)
        if lx + 14 + tw > w - pad:
            break
        body.append(el("circle", cx=lx + 4, cy=239, r=4, fill=color))
        body.append(mono(lx + 14, 243, label, 12, p["text"]))
        lx += 14 + tw + 24
    return svg(w, h, "".join(body),
               "GitHub activity for {}: {:,} contributions, {} day current "
               "streak, top languages {}".format(
                   USER, s["total"], s["current"].length,
                   ", ".join(n for n, _, _ in s["languages"])))


# ---------------------------------------------------------------- projects --

CARD_W, CARD_H = 440, 262
CARD_PAD = 28
BADGE_R = 13
TITLE_SIZE = 19
DESC_SIZE = 12
DESC_COLS = int((CARD_W - 2 * CARD_PAD) / (DESC_SIZE * MONO_ADVANCE))
DESC_LINES = 3
DESC_Y = 158
TAG_SIZE = 11
TAG_H = 22
TAG_Y = CARD_H - CARD_PAD - TAG_H + 4
TITLE_ROOM = CARD_W - 2 * CARD_PAD - 2 * BADGE_R - 12


def build_project(p, project):
    """One featured-work card: a machined shell round an inset panel, the
    headline figure in the accent, the pitch, then the tags. A badge in the
    corner says the card opens the repository.

    No entry animation: a rise-in that starts at opacity 0 leaves the card
    blank wherever animation does not run, and a blank card is worse than a
    still one.
    """
    w, h, x0 = CARD_W, CARD_H, CARD_PAD
    bx, by = w - x0 - BADGE_R, 42
    body = [
        el("rect", x=0.5, y=0.5, width=w - 1, height=h - 1, rx=18,
           fill=p["surface"], stroke=p["line"]),
        el("rect", x=6, y=6, width=w - 12, height=h - 12, rx=13,
           fill=p["inset"], stroke=p["line"], stroke_opacity=0.6),
        sans(x0, 49, project.name, fit(project.name, TITLE_SIZE, TITLE_ROOM, True),
             p["text"], 700),
        el("circle", cx=bx, cy=by, r=BADGE_R, fill="none", stroke=p["accent"],
           stroke_opacity=0.55),
        arrow(bx, by, 8, p["accent"]),
        sans(x0, 104, project.figure, 34, p["accent"], 700, spacing="-0.02em"),
        sans(x0, 126, project.figure_label, 12.5, p["muted"]),
    ]
    for i, line in enumerate(textwrap.wrap(project.desc, DESC_COLS)[:DESC_LINES]):
        body.append(mono(x0, DESC_Y + i * 18, line, DESC_SIZE, p["text"]))

    tx = x0
    for tag in project.tags:
        tw = mono_width(tag, TAG_SIZE) + 20
        if tx + tw > w - x0:
            break       # drop the tail rather than spill past the card
        body.append(el("rect", x=tx, y=TAG_Y, width=tw, height=TAG_H,
                       rx=TAG_H / 2.0, fill=p["surface"], stroke=p["line"]))
        body.append(mono(tx + 10, TAG_Y + TAG_H / 2.0 + TAG_SIZE * 0.36, tag,
                         TAG_SIZE, p["muted"]))
        tx += tw + 8
    return svg(w, h, "".join(body), "{}: {}, {}. {}".format(
        project.name, project.figure, project.figure_label, project.desc))


def build_spacer():
    """Holds the empty grid slot beside an odd card. One pixel tall, so when
    the cards stack on a phone it leaves no visible gap."""
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="{}" height="1" '
            'viewBox="0 0 {} 1" aria-hidden="true"/>'.format(CARD_W, CARD_W))


# -------------------------------------------------------------------- main --

def assets(stats):
    """Every (file name, svg) the README needs. stats may be None."""
    yield "project-spacer.svg", build_spacer()
    for suffix, p in THEMES.items():
        yield "banner{}.svg".format(suffix), build_banner(p)
        yield "footer{}.svg".format(suffix), build_footer(p)
        yield "stack{}.svg".format(suffix), build_stack(p)
        for project in PROJECTS:
            yield "project-{}{}.svg".format(project.key, suffix), build_project(p, project)
        for key, label, slug in SOCIAL:
            yield "social-{}{}.svg".format(key, suffix), build_social(p, label, slug)
        if stats:
            yield "activity{}.svg".format(suffix), build_activity(p, stats)


def main():
    os.makedirs(OUT, exist_ok=True)
    try:
        stats = fetch_stats()
    except (RuntimeError, urllib.error.URLError, OSError, KeyError, TypeError,
            ValueError) as exc:
        # The workflow restores the last published activity card when this
        # happens, so an API hiccup never blanks the section.
        print("stats unavailable, skipping activity card: {}".format(exc),
              file=sys.stderr)
        stats = None
    for name, doc in assets(stats):
        path = os.path.join(OUT, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(doc)
        print("wrote {} ({:,} bytes)".format(path, len(doc)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
