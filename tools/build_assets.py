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
import math
import os
import sys
import textwrap
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import NamedTuple, Optional

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


TEXT_MIN = 4.5      # WCAG AA for body text
MARK_MIN = TEXT_MIN
PALE = 0.4          # luminance above which a brand colour counts as pale


def mark_colors(color, background):
    """(fill, outline) for a brand mark on background, at MARK_MIN either way.

    Marks are held to the same 4.5:1 as text, not WCAG's 3:1 for graphics:
    most of these glyphs are thin line art (Java's cup, the PostgreSQL
    elephant, GitHub Actions' nodes), and below 4.5:1 on the dark pills they
    read as switched off.

    A colour that falls short is lifted toward white on a dark pill and
    darkened on a light one, except a pale brand on a light pill (JavaScript,
    Linux, Tauri, React): it keeps its own fill and gets a hairline in a
    darker shade of itself, because darkening the yellow to 4.5:1 makes it
    olive, and the yellow is how people recognise it.
    """
    if contrast(color, background) >= MARK_MIN:
        return color.upper(), None
    if is_dark(background) or luminance(color) < PALE:
        return legible(color, background, MARK_MIN), None
    return color.upper(), legible(color, background, MARK_MIN)


# ---------------------------------------------------------------- content --

NAME = "Abdul Azeem Asharaf Ali"
ROLE = "Software engineer building apps and tools"

TYPING_LINES = [
    "CS + Math at UIC",
    "I build apps and tools people actually use",
    "From port operations to real-time audio",
    "Open to SWE internships and full-time roles",
]

FOOTER_LINES = [
    "Thanks for scrolling this far",
    "Open to SWE internships and full-time roles",
    "Apps, tools, and everything behind them",
    "abdulazeemasharafali306@gmail.com",
]

# (file key, label, Simple Icons slug). The README wraps each in its link.
SOCIAL = [
    ("linkedin", "LinkedIn", "linkedin"),
    ("email", "Email", "gmail"),
]

# (heading, [(label, Simple Icons slug)]), apps first. Colours come from
# icons.py; anything without a Simple Icons mark is left out, not faked.
STACK = [
    ("Apps and interfaces",
     [("Flutter", "flutter"), ("React", "react"), ("Next.js", "nextdotjs"),
      ("Angular", "angular"), ("Tauri", "tauri"), ("WebGL", "webgl")]),
    ("Languages",
     [("TypeScript", "typescript"), ("Python", "python"), ("Java", "java"),
      ("Rust", "rust"), ("JavaScript", "javascript"), ("Dart", "dart")]),
    ("Data and services",
     [("FastAPI", "fastapi"), ("PostgreSQL", "postgresql"), ("SQLite", "sqlite"),
      ("Frappe", "frappe"), ("Git", "git"), ("Linux", "linux"),
      ("GitHub Actions", "githubactions")]),
]


class Project(NamedTuple):
    key: str                # asset file name: project-<key>[-dark].svg
    name: str
    repo: Optional[str]     # github.com/USER/<repo>; None for a private repo,
                            # whose card the README leaves unlinked
    status: Optional[str]   # pill in the corner in place of the link badge
    tagline: str            # one line, accent, the same size on every card
    what: str               # what the thing is, in a few words
    desc: str
    tags: tuple


# Descriptions are checked against each repo's README and code. Caravan's
# README and case-study numbers give the this-or-that matchups, the
# organizer's overlap view and the 13-friend trip it began with; Sonora
# captures the default or a chosen output device; the dashboard makes no
# network requests; the invoicing app re-creates its naming rules on every
# migrate without resetting counters, and its reverse charge is a form helper.
PROJECTS = [
    Project("caravan", "Caravan", "caravan", None,
            "Plan the trip together", "Group-trip planner, live on the web",
            "Everyone builds their own version of the trip from this-or-that "
            "picks, and the organizer sees where the group overlaps. 13 "
            "friends used it for a real trip.",
            ("Next.js", "TypeScript", "Supabase", "Playwright")),
    Project("berthiq", "BerthIQ", None, "in progress",
            "Test-drive the port first", "Port-call operations tool",
            "Finds where vessels get stuck, predicts which ones will miss "
            "their window, and lets an analyst try a fix in a simulated port "
            "before the real one.",
            ("Next.js", "FastAPI", "OR-Tools", "SimPy")),
    Project("sonora", "Sonora", "sonora", None,
            "Your music, made visible", "Real-time desktop music visualizer",
            "Listens to whatever your PC is playing, breaks the sound down in "
            "Rust, and turns it into three live WebGL scenes.",
            ("Rust", "Tauri 2", "WebGL 2", "WASAPI")),
    Project("vessel-dashboard", "Vessel Intelligence Dashboard",
            "vessel-intelligence-dashboard", None,
            "Excel in, board report out", "Fleet dashboard in a single HTML file",
            "Turns a vessel-employment workbook into an interactive dashboard "
            "in seconds, with no server and no data ever leaving the browser.",
            ("JavaScript", "SheetJS", "single-file")),
    Project("frappe-invoicing", "Frappe Invoicing Platform",
            "frappe-invoicing-platform", None,
            "Invoices that do the tax math", "ERPNext invoicing extension",
            "Adds VAT templates, a reverse-charge helper, and clean invoice, "
            "credit-note and receipt layouts to ERPNext, with numbering that "
            "survives migrations.",
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


def frac(k):
    """A keyTime: five places, so a 24 ms step in a 20 s cycle survives."""
    return "{:.5f}".format(k).rstrip("0").rstrip(".") or "0"


def typing(lines, cx, baseline, size, color, caret_color,
           type_ms=58, erase_ms=24, hold_ms=2300, gap_ms=420, blink_ms=550):
    """A typewriter loop in SMIL, which <img>-embedded SVG runs (it never runs
    script).

    Every animation shares one cycle: the same dur, begin="0s" and
    repeatCount="indefinite", with each line placed in the cycle by keyTimes
    alone. Nothing is timed off another element: GitHub's iOS app ignores
    syncbase begins and starts every line at once, which stacked them.

    Each line is a group holding its clipped text and its own caret, and a
    discrete opacity gate shows the group only during that line's slot, so an
    idle line and its caret are fully hidden. The clip steps one whole
    character at a time, so a glyph is never half drawn; the caret follows
    it, holds solid while typing and erasing, and blinks while the line rests.

    Without SMIL the base attributes apply: line 0 in full with its caret
    after it, every other group hidden.
    """
    adv = size * MONO_ADVANCE
    top, tall = baseline - size * 0.84, size * 1.14
    spans = [(len(t) + 1) * (type_ms + erase_ms) + hold_ms + gap_ms for t in lines]
    cycle = float(sum(spans))

    def track(attr, frames):
        """One discrete animation over the whole cycle. frames are (ms, value)
        in time order, closed at the cycle's end; a later frame landing on the
        same rounded instant replaces the earlier one."""
        times, values = [], []
        for t, v in frames + [(cycle, frames[-1][1])]:
            k = round(t / cycle, 5)
            if times and k <= times[-1]:
                values[-1] = v
                continue
            times.append(k)
            values.append(v)
        return el("animate", attributeName=attr, begin="0s", dur=ms(cycle),
                  repeatCount="indefinite", calcMode="discrete",
                  keyTimes=";".join(frac(k) for k in times),
                  values=";".join(num(v) for v in values))

    groups, start = [], 0.0
    for i, line in enumerate(lines):
        n = len(line)
        width = n * adv
        x = cx - width / 2.0
        typed = start + (n + 1) * type_ms       # last character landed
        erase = typed + hold_ms
        erased = erase + (n + 1) * erase_ms
        end = start + spans[i]
        steps = ([(start + k * type_ms, k) for k in range(n + 1)]
                 + [(erase + j * erase_ms, n - j) for j in range(n + 1)])
        lead = [] if i == 0 else [(0.0, 0)]     # before this line's slot

        blink = [(0.0, 1), (start, 1)]
        for a, z in ((typed, erase), (erased, end)):
            t, on = a, 1
            while t < z:
                blink.append((t, on))
                on, t = 1 - on, t + blink_ms
            blink.append((z, 1))

        gate = [(0.0, 1 if i == 0 else 0)] + ([(start, 1)] if i else [])
        if end < cycle:
            gate.append((end, 0))

        clip = el("clipPath", el(
            "rect", track("width", lead + [(t, k * adv) for t, k in steps]),
            x=x, y=top, height=tall, width=width + adv if i == 0 else 0),
            id="c%d" % i)
        text = el("text", esc(line), x=x, y=baseline, clip_path="url(#c%d)" % i,
                  font_family=MONO, font_size=size, font_weight=500, fill=color,
                  textLength=width, lengthAdjust="spacing", xml__space="preserve")
        caret = el("rect",
                   track("x", [(t, x + 1) for t, _ in lead]
                         + [(t, x + k * adv + 1) for t, k in steps])
                   + track("opacity", blink),
                   x=x + (width if i == 0 else 0) + 1, y=top, width=2,
                   height=tall, rx=1, fill=caret_color)
        groups.append(el("g", track("opacity", gate) + clip + text + caret,
                         class_="typing-line", opacity=None if i == 0 else 0))
        start = end
    return "".join(groups)


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
               p["accent"], p["accent_soft"]),
    ]
    return svg(w, b["height"], "".join(body),
               "{}. {}. {}".format(NAME, ROLE, ". ".join(TYPING_LINES)))


def build_footer(p):
    f = FOOTER
    w = f["width"]
    body = [
        typing(FOOTER_LINES, w / 2.0, f["typing_y"], f["typing_size"],
               p["muted"], p["accent_soft"]),
        el("g", wave(p, w, f["band"], flip=True),
           transform="translate(0,{})".format(f["height"] - f["band"])),
    ]
    return svg(w, f["height"], "".join(body), ". ".join(FOOTER_LINES[1:]))


# ------------------------------------------------------------------ chips --

# Sized so the longest stack row (seven pills, "Data and services") fits on
# one line of the panel; test_stack_rows_fit_on_one_line holds it there.
CHIP_SIZE = 13.5    # label font size
CHIP_H = 36
CHIP_ICON = 18
CHIP_PAD = 12
CHIP_LEAD = CHIP_PAD + CHIP_ICON + 10    # glyph plus the gap before the label
CHIP_TAIL = 14
CHIP_ARROW = 18     # extra room for the link arrow on social pills
CHIP_GAP = 8
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
    return svg(w, h, panel(w, h, p) + "".join(body), "Tools I use. " + " ".join(
        "{}: {}.".format(heading, ", ".join(label for label, _ in items))
        for heading, items in STACK))


# --------------------------------------------------------------- activity --

PROFILE_Q = """
query($login: String!, $after: String) {
  user(login: $login) {
    createdAt
    repositories(first: 100, after: $after, ownerAffiliations: OWNER,
                 isFork: false, privacy: PUBLIC) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
}
"""

YEAR_FIELDS = ("{ contributionCalendar { totalContributions weeks { "
               "contributionDays { date contributionCount } } } }")

# Markup, styling and templating stay out of the language mix: a page's HTML
# and CSS say little about what was built, and they crowd out the code.
MARKUP = frozenset({
    "HTML", "CSS", "SCSS", "Sass", "Less", "Stylus", "PostCSS", "Markdown",
    "MDX", "XML", "XSLT", "SVG", "TeX", "reStructuredText", "AsciiDoc",
    "Handlebars", "Mustache", "EJS", "Pug", "Haml", "Slim", "Jinja", "Twig",
    "Liquid", "Nunjucks", "Smarty", "Blade",
})


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
    """Totals, streaks, active days and language mix for USER's public work.

    privacy: PUBLIC keeps private repositories out whoever's token runs this,
    so a local build shows what the workflow will publish. Two requests in
    the common case: the profile, then every year's calendar at once.

    There is no pull-request count: GitHub's total includes PRs in private
    repositories for some tokens and not others, so the card would disagree
    with itself. Active days come from the calendar already fetched.
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
    days, total = {}, 0
    for cc in years.values():
        total += cc["contributionCalendar"]["totalContributions"]
        for week in cc["contributionCalendar"]["weeks"]:
            for d in week["contributionDays"]:
                days[d["date"]] = d["contributionCount"]

    sizes, colors = {}, {}
    for repo in repos:
        if repo["name"].lower() == USER.lower():
            continue    # this repository's own tooling is not the work it shows
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            if name in MARKUP:
                continue
            sizes[name] = sizes.get(name, 0) + edge["size"]
            colors[name] = edge["node"]["color"] or "#8B949E"
    grand = sum(sizes.values()) or 1
    top = sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

    current, longest = streaks(days, today)
    year_ago = (today - timedelta(days=364)).isoformat()
    return {
        "total": total, "repos": page["totalCount"],
        "active": sum(1 for d, n in days.items() if n > 0 and d >= year_ago),
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
METRIC_Y = 100


def build_activity(p, s):
    """Four contribution figures over the top languages.

    Four rather than six: stars and all-time commits were small numbers that
    disagreed with the contribution total beside them. The public repository
    count rides in the header instead of taking a tile.
    """
    w, h, pad = PANEL_W, ACTIVITY_H, PANEL_PAD + 2
    body = [panel(w, h, p), eyebrow(pad, 40, "Contributions and streaks", p),
            mono(w - pad, 40, "@{} \u00b7 {} public repos".format(USER, s["repos"]),
                 12.5, p["muted"], anchor="end")]

    cells = [
        (s["total"], "Contributions", "since " + day(s["since"]), True),
        (s["active"], "Active days", "in the last year", True),
        (s["current"].length, "Current streak", span(s["current"], s["today"]), False),
        (s["longest"].length, "Longest streak", span(s["longest"], s["today"]), False),
    ]
    step = (w - 2 * pad) / float(len(cells))
    for i, (value, label, note, hot) in enumerate(cells):
        cx = pad + step * (i + 0.5)
        body.append(sans(cx, METRIC_Y, "{:,}".format(value), 30,
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
               "GitHub activity for {}: {:,} contributions, {} active days in "
               "the last year, top languages {}".format(
                   USER, s["total"], s["active"],
                   ", ".join(n for n, _, _ in s["languages"])))


# ---------------------------------------------------------------- projects --

CARD_W, CARD_H = 440, 262
CARD_PAD = 28
CARD_ROOM = CARD_W - 2 * CARD_PAD
BADGE_R = 13
PILL_SIZE = 11
PILL_H = 22
TITLE_SIZE = 19
TITLE_Y = 49
TAGLINE_Y = 96
WHAT_Y = 119
DESC_SIZE = 12
DESC_COLS = int(CARD_ROOM / (DESC_SIZE * MONO_ADVANCE))
DESC_LINES = 3
DESC_Y = 153
TAG_SIZE = 11
TAG_H = 22
TAG_Y = CARD_H - CARD_PAD - TAG_H + 4

# Helvetica widths run at or above Segoe UI and San Francisco; the slack
# covers wider fallbacks such as Noto Sans.
TAGLINE_SLACK = 1.08


def tagline_size(projects):
    """The one tagline size for every card: the largest, in half points, at
    which the longest tagline still fits on a single line. A card never wraps
    or shrinks its own, so the four read as a set."""
    widest = max(sans_width(pr.tagline, 1, bold=True) for pr in projects)
    return math.floor(CARD_ROOM / (widest * TAGLINE_SLACK) * 2) / 2.0


TAGLINE_SIZE = tagline_size(PROJECTS)


def status_pill(right, cy, text, p):
    """A dot and a word in an outlined pill, right-aligned at `right`.
    Returns (svg, width)."""
    w = 11 + 6 + 6 + mono_width(text, PILL_SIZE) + 11
    x = right - w
    return (el("rect", x=x, y=cy - PILL_H / 2.0, width=w, height=PILL_H,
               rx=PILL_H / 2.0, fill="none", stroke=p["accent"], stroke_opacity=0.55)
            + el("circle", cx=x + 14, cy=cy, r=3, fill=p["accent"])
            + mono(x + 23, cy + PILL_SIZE * 0.36, text, PILL_SIZE, p["accent"], 600),
            w)


def corner(project, p):
    """The status pill, or for a linked card the arrow badge. Returns
    (svg, width) so the title knows how much room it has."""
    right, cy = CARD_W - CARD_PAD, 42
    if project.status:
        return status_pill(right, cy, project.status, p)
    bx = right - BADGE_R
    return (el("circle", cx=bx, cy=cy, r=BADGE_R, fill="none", stroke=p["accent"],
               stroke_opacity=0.55) + arrow(bx, cy, 8, p["accent"]), 2 * BADGE_R)


def title_room(project, p):
    return CARD_ROOM - corner(project, p)[1] - 12


def build_project(p, project):
    """One featured-work card: a machined shell round an inset panel, the
    name with its status or link badge, the tagline in the accent, what the
    thing is, the pitch, then the tags.

    No entry animation: a rise-in that starts at opacity 0 leaves the card
    blank wherever animation does not run, and a blank card is worse than a
    still one.
    """
    w, h, x0 = CARD_W, CARD_H, CARD_PAD
    badge, _ = corner(project, p)
    body = [
        el("rect", x=0.5, y=0.5, width=w - 1, height=h - 1, rx=18,
           fill=p["surface"], stroke=p["line"]),
        el("rect", x=6, y=6, width=w - 12, height=h - 12, rx=13,
           fill=p["inset"], stroke=p["line"], stroke_opacity=0.6),
        sans(x0, TITLE_Y, project.name,
             fit(project.name, TITLE_SIZE, title_room(project, p) / TAGLINE_SLACK, True),
             p["text"], 700),
        badge,
        sans(x0, TAGLINE_Y, project.tagline, TAGLINE_SIZE, p["accent"], 700),
        sans(x0, WHAT_Y, project.what, 12.5, p["muted"]),
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
    return svg(w, h, "".join(body), card_alt(project))


def card_alt(project):
    """The card's accessible name, which the README's alt text repeats."""
    return "{}: {}. {}".format(project.name, project.tagline, project.desc)


# -------------------------------------------------------------------- main --

def assets(stats):
    """Every (file name, svg) the README needs. stats may be None."""
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
