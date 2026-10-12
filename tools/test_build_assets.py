#!/usr/bin/env python3
"""Checks for what build_assets can get wrong without anyone noticing: the
typing loop's timing, text that spills or fades into its background, the
stats maths, and a README or workflow that drifts from the assets it serves.

Run: python3 tools/test_build_assets.py   (pytest also collects it)
"""

import os
import re
import sys
import traceback
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import build_assets as b                                    # noqa: E402
import icons                                                # noqa: E402

RAW = "https://raw.githubusercontent.com/{0}/{0}/output/".format(b.USER)
TODAY = date(2026, 10, 12)
LIGHT, DARK = b.THEMES[""], b.THEMES["-dark"]

SAMPLE_STATS = {
    "total": 1204, "commits": 873, "stars": 12, "repos": 6, "prs": 41,
    "current": b.Streak(4, date(2026, 10, 9), TODAY),
    "longest": b.Streak(19, date(2025, 12, 28), date(2026, 1, 15)),
    "since": date(2024, 10, 15), "today": TODAY,
    "languages": [("TypeScript", 29.7, "#3178c6"), ("JavaScript", 27.0, "#f1e05a"),
                  ("Rust", 12.4, "#dea584"), ("CSS", 8.7, "#663399"),
                  ("Python", 7.4, "#3572A5")],
}


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def parse(doc):
    return ET.fromstring(doc)


def local(e):
    return e.tag.rsplit("}", 1)[-1]


def wrap(fragment):
    return parse('<svg xmlns="http://www.w3.org/2000/svg">' + fragment + "</svg>")


# ---------------------------------------------------------------- streaks --

def days_from(spec, end=TODAY):
    """spec is per-day counts, oldest first, ending on `end`."""
    return {(end - timedelta(days=len(spec) - 1 - i)).isoformat(): n
            for i, n in enumerate(spec)}


def test_streaks():
    def lengths(spec):
        cur, lon = b.streaks(days_from(spec), TODAY)
        return cur.length, lon.length

    assert b.streaks({}, TODAY) == (b.Streak(0), b.Streak(0))
    assert lengths([0, 1, 1, 1]) == (3, 3)          # active today
    assert lengths([1, 1, 1, 0]) == (3, 3)          # today still young
    assert lengths([1, 1, 1, 0, 0]) == (0, 3)       # two idle days: broken
    assert lengths([1, 1, 1, 1, 1, 0, 1, 1]) == (2, 5)
    assert lengths([1, 1, 0, 1, 1, 1]) == (3, 3)    # a gap is not bridged

    cur, lon = b.streaks(days_from([1, 1, 0, 1, 1]), TODAY)
    assert cur == b.Streak(2, TODAY - timedelta(days=1), TODAY), cur
    assert lon == cur, "a tie for longest should go to the recent run"

    # Missing dates, not just zero counts, must break a run.
    sparse = {"2026-10-01": 3, "2026-10-02": 1, "2026-10-05": 2}
    assert b.streaks(sparse, TODAY)[1] == b.Streak(2, date(2026, 10, 1),
                                                    date(2026, 10, 2))


def test_streak_captions():
    s = b.Streak
    assert b.span(s(0), TODAY) == "none running"
    assert b.span(s(1, TODAY, TODAY), TODAY) == "today"
    assert b.span(s(4, date(2026, 10, 9), TODAY), TODAY) == "9 Oct – today"
    assert b.span(s(5, date(2026, 6, 15), date(2026, 6, 19)), TODAY) == "15 – 19 Jun 2026"
    assert b.span(s(7, date(2026, 5, 28), date(2026, 6, 3)), TODAY) == "28 May – 3 Jun 2026"
    assert (b.span(s(19, date(2025, 12, 28), date(2026, 1, 15)), TODAY)
            == "28 Dec – 15 Jan 2026")
    assert b.span(s(19, date(2025, 12, 28), date(2026, 1, 15)), date(2026, 1, 15))         == "28 Dec – today"
    assert (b.span(s(400, date(2025, 9, 8), date(2026, 10, 12)), TODAY)
            == "8 Sep 2025 – today")


# ----------------------------------------------------------------- typing --

class Timeline:
    """Just enough SMIL to play the typing loop: syncbase begins
    (id.begin, id.end+Nms), discrete values, set, freeze, and the sandwich
    rule that the latest-begun animation wins. Times are in ms."""

    def __init__(self, root):
        self.parent = {c: p for p in root.iter() for c in p}
        self.anims = [e for e in root.iter() if local(e) in ("animate", "set")]
        self.by_id = {a.get("id"): a for a in self.anims if a.get("id")}
        self.begin = {}
        for _ in range(len(self.anims) + 1):        # fixed point
            for a in self.anims:
                t = self._first_begin(a)
                if t is not None:
                    self.begin[a] = t
        missing = [a.get("id") or a.get("attributeName") for a in self.anims
                   if a not in self.begin]
        assert not missing, "never begins: %s" % missing
        # The loop closes where line 0 is re-triggered by the last erase.
        restart = [s for s in self.by_id["t0"].get("begin").split(";") if s != "0s"]
        assert len(restart) == 1, "line 0 must restart from the last line"
        self.period = self._at(restart[0])

    @staticmethod
    def dur(a):
        d = a.get("dur")
        if d is None:
            return float("inf")
        return float(d[:-2]) if d.endswith("ms") else float(d[:-1]) * 1000

    def _at(self, spec):
        if spec == "0s":
            return 0.0
        m = re.fullmatch(r"(\w+)\.(begin|end)(?:\+(\d+)ms)?", spec)
        assert m, "unsupported begin %r" % spec
        ref = self.by_id[m.group(1)]
        if ref not in self.begin:
            return None
        t = self.begin[ref] + (self.dur(ref) if m.group(2) == "end" else 0)
        return t + float(m.group(3) or 0)

    def _first_begin(self, a):
        times = [self._at(s) for s in a.get("begin", "0s").split(";")]
        times = [t for t in times if t is not None]
        return min(times) if times else None

    def value(self, target, attr, t):
        # Stable sort: at equal begin times, the later element wins.
        live = sorted(((self.begin[a], a) for a in self.anims
                       if self.parent[a] is target and a.get("attributeName") == attr
                       and self.begin[a] <= t), key=lambda pair: pair[0])
        for start, a in reversed(live):
            if local(a) == "set":
                if t < start + self.dur(a) or a.get("fill") == "freeze":
                    return float(a.get("to"))
                continue
            values = [float(v) for v in a.get("values").split(";")]
            elapsed, d = t - start, self.dur(a)
            if a.get("repeatCount") == "indefinite":
                elapsed %= d
            if elapsed < d:
                return values[int(elapsed / d * len(values))]
            if a.get("fill") == "freeze":
                return values[-1]
        return float(target.get(attr, "1" if attr == "opacity" else "0"))


def typing_parts(root):
    clips = {c.get("id"): c[0] for c in root.iter() if local(c) == "clipPath"
             and re.fullmatch(r"c\d+", c.get("id", ""))}
    texts = [t for t in root.iter() if local(t) == "text" and t.get("clip-path")]
    caret = next(r for r in root.iter() if local(r) == "rect" and r.get("width") == "2")
    lines = []
    for t in texts:
        clip = clips[re.search(r"#(c\d+)", t.get("clip-path")).group(1)]
        lines.append((t, clip, float(t.get("x")), float(t.get("textLength"))))
    return lines, caret


def check_typing(doc, lines_text, rest):
    root = parse(doc)
    lines, caret = typing_parts(root)
    assert [t.text for t, _, _, _ in lines] == lines_text
    tl = Timeline(root)
    assert tl.period > 0

    # Static frame: only the resting line, with the caret after it.
    for i, (_, clip, x, width) in enumerate(lines):
        base = float(clip.get("width"))
        assert (base >= width) if i == rest else base == 0, (i, base)
    rx, rw = lines[rest][2], lines[rest][3]
    assert abs(float(caret.get("x")) - (rx + rw + 1)) < 0.02

    full = [False] * len(lines)
    t = 0.0
    while t < tl.period:
        shown = []
        for i, (_, clip, x, width) in enumerate(lines):
            w = tl.value(clip, "width", t)
            assert -0.01 <= w <= width + 0.01, (i, t, w)
            if w > 0:
                shown.append((i, x, width, w))
            if abs(w - width) < 0.01:
                full[i] = True
        assert len(shown) <= 1, "two lines visible at %.0f ms: %s" % (t, shown)
        if shown:
            i, x, width, w = shown[0]
            cx = tl.value(caret, "x", t)
            assert abs(cx - (x + w + 1)) < 0.02, \
                "caret at %.1f, line %d ends at %.1f (t=%.0f)" % (cx, i, x + w, t)
            if 0 < w < width:
                assert tl.value(caret, "opacity", t) == 1, \
                    "caret blinked mid-word at %.0f ms" % t
        t += 7.0
    assert all(full), "lines never fully typed: %s" % [
        lines_text[i] for i, f in enumerate(full) if not f]


def test_banner_typing_loop():
    check_typing(b.build_banner(LIGHT), b.TYPING_LINES, b.TYPING_REST)


def test_footer_typing_loop():
    check_typing(b.build_footer(DARK), b.FOOTER_LINES, b.FOOTER_REST)


def test_typing_lines_fit():
    for doc, width in ((b.build_banner(LIGHT), b.BANNER["width"]),
                       (b.build_footer(LIGHT), b.FOOTER["width"])):
        for t, _, x, w in typing_parts(parse(doc))[0]:
            assert x >= 40 and x + w + 3 <= width - 40, \
                "%r runs from %.0f to %.0f" % (t.text, x, x + w)


def test_title_and_role_fit_the_band():
    room = b.BANNER["width"] - 140
    size = b.fit(b.NAME, b.BANNER["title_size"], room, bold=True)
    assert size == b.BANNER["title_size"], "the name had to shrink to %.1f" % size
    assert b.sans_width(b.NAME, size, True) * 1.08 <= room
    assert b.sans_width(b.ROLE, b.BANNER["role_size"]) * 1.12 <= room


# --------------------------------------------------------------- contrast --

def veiled(color):
    """color under every white veil the wave lays over it."""
    for alpha in b.WAVE_TINTS:
        color = b.mix(color, "#FFFFFF", alpha)
    return color


def test_text_contrast_meets_aa():
    pairs = [("text", "surface"), ("text", "inset"), ("muted", "surface"),
             ("muted", "inset"), ("muted", "page"), ("accent", "surface"),
             ("accent", "inset"), ("accent", "page"), ("text", "page")]
    for name, p in (("light", LIGHT), ("dark", DARK)):
        for fg, bg in pairs:
            ratio = b.contrast(p[fg], p[bg])
            assert ratio >= 4.5, "%s %s on %s is %.2f:1" % (name, fg, bg, ratio)


def test_text_on_the_wave_meets_aa():
    """The name and role sit on a gradient under white veils. Sample the
    worst point under each run, assuming every veil covers it."""
    w, band = b.BANNER["width"], b.BANNER["band"]
    runs = [(b.NAME, b.BANNER["title_y"], b.BANNER["title_size"], True, "on_wave"),
            (b.ROLE, b.BANNER["role_y"], b.BANNER["role_size"], False, "on_wave_muted")]
    for name, p in (("light", LIGHT), ("dark", DARK)):
        for text, y, size, bold, fg in runs:
            half = b.sans_width(text, size, bold) * 1.08 / 2
            worst = min(
                b.contrast(p[fg], veiled(b.gradient_at(p, x, yy, w, band)))
                for x in range(int(w / 2 - half), int(w / 2 + half) + 1, 6)
                for yy in (y - size * 0.75, y, y + size * 0.2))
            assert worst >= 4.5, "%s %r drops to %.2f:1" % (name, text, worst)


def test_brand_glyphs_reach_three_to_one():
    """Every mark's fill or its outline reaches 3:1 against its pill, and the
    outline is only used to rescue a pale brand on a light card."""
    slugs = [s for _, items in b.STACK for _, s in items] + [s for _, _, s in b.SOCIAL]
    for p in (LIGHT, DARK):
        for bg in (p["inset"], p["surface"]):
            for slug in slugs:
                fill, outline = b.mark_colors(icons.brand(slug), bg)
                best = max(b.contrast(fill, bg), b.contrast(outline or fill, bg))
                assert best >= 3.0, (slug, bg, fill, outline)
                if outline:
                    assert not b.is_dark(bg) and fill == icons.brand(slug), slug
    assert b.mark_colors("#000000", DARK["inset"])[0] != "#000000", "Rust vanished"
    assert b.mark_colors("#F7DF1E", LIGHT["inset"])[0] == "#F7DF1E", "JS lost its yellow"
    assert b.mark_colors("#3776AB", LIGHT["inset"]) == ("#3776AB", None), "a legible brand moved"
    # The outline is drawn, not just computed.
    frag, _ = b.chip(0, 0, "JavaScript", "javascript", LIGHT, LIGHT["inset"])
    assert 'stroke="#' in frag and "non-scaling-stroke" in frag


# ------------------------------------------------------------------ chips --

def chips(root):
    return [g for g in root.iter() if local(g) == "g" and g.get("class") == "chip"]


def test_every_icon_slug_is_vendored():
    used = [s for _, items in b.STACK for _, s in items] + [s for _, _, s in b.SOCIAL]
    missing = [s for s in used if s not in icons.ICONS]
    assert not missing, "no vendored glyph for %s" % missing
    for slug in used:
        assert re.fullmatch(r"#[0-9A-F]{6}", icons.brand(slug)), slug
        wrap(icons.glyph(slug, 0, 0, 18, "#FFFFFF"))
    assert icons.glyph("not-a-brand", 0, 0, 18, "#FFF") == ""
    assert set(icons.SOURCES) == set(icons.ICONS), "SOURCES and ICONS disagree"


def test_chip_text_stays_inside_its_pill():
    cases = [(label, slug, False) for _, items in b.STACK for label, slug in items]
    cases += [(label, slug, True) for _, label, slug in b.SOCIAL]
    for label, slug, link in cases:
        frag, w = b.chip(0, 0, label, slug, DARK, DARK["inset"], link)
        g = chips(wrap(frag))[0]
        rect = next(e for e in g if local(e) == "rect")
        text = next(e for e in g if local(e) == "text")
        assert abs(float(rect.get("width")) - w) < 0.01, label
        end = float(text.get("x")) + float(text.get("textLength"))
        assert w - end >= (b.CHIP_ARROW + 6 if link else 12), \
            "%s: %.1f px after the label" % (label, w - end)
        assert any(local(e) == "path" for e in g), "%s lost its glyph" % label


def test_stack_pills_do_not_overlap_or_leave_the_card():
    for p in (LIGHT, DARK):
        root = parse(b.build_stack(p))
        w, h = float(root.get("width")), float(root.get("height"))
        boxes = []
        for g in chips(root):
            r = g[0]
            x, y = float(r.get("x")), float(r.get("y"))
            bw, bh = float(r.get("width")), float(r.get("height"))
            assert b.PANEL_PAD <= x and x + bw <= w - b.PANEL_PAD + 0.01, (x, bw)
            assert y + bh <= h - b.PANEL_PAD + 0.01, (y, bh, h)
            boxes.append((x, y, bw, bh))
        for i, (ax, ay, aw, ah) in enumerate(boxes):
            for bx, by, bw, bh in boxes[i + 1:]:
                assert not (ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah), \
                    "pills overlap at (%.0f,%.0f) and (%.0f,%.0f)" % (ax, ay, bx, by)
        assert len(boxes) == sum(len(items) for _, items in b.STACK)


# --------------------------------------------------------------- activity --

def test_activity_card_fits():
    for p in (LIGHT, DARK):
        doc = b.build_activity(p, SAMPLE_STATS)
        root = parse(doc)
        right = b.PANEL_W - b.PANEL_PAD - 2
        for t in root.iter():
            if local(t) == "text" and t.get("textLength") and t.get("text-anchor") != "end":
                end = float(t.get("x")) + float(t.get("textLength"))
                assert end <= right + 0.01, "legend runs to %.1f" % end
        bars = [r for r in root.iter() if local(r) == "rect" and r.get("height") == "10"]
        last = bars[-1]
        assert float(last.get("x")) + float(last.get("width")) >= right - 4, \
            "the language bar stops short of the card"
        assert "1,204" in doc and "Other 14.8%" in doc
        # Each caption fits its column.
        step = (b.PANEL_W - 2 * (b.PANEL_PAD + 2)) / 6.0
        for t in root.iter():
            if local(t) == "text" and t.get("font-size") == "11":
                assert b.sans_width(t.text, 11) * 1.08 <= step - 8, t.text

    long_names = dict(SAMPLE_STATS, languages=[("LanguageWithAVeryLongName%d" % i,
                                                19.0, "#FFFFFF") for i in range(5)])
    root = parse(b.build_activity(LIGHT, long_names))
    for t in root.iter():
        if local(t) == "text" and t.get("textLength") and t.get("text-anchor") != "end":
            assert float(t.get("x")) + float(t.get("textLength")) <= b.PANEL_W - b.PANEL_PAD


def test_fetch_stats_offline():
    """Pagination, one request for every year, public-only, and the maths."""
    calls = []

    def repo(stars, *langs):
        return {"stargazerCount": stars, "languages": {"edges": [
            {"size": size, "node": {"name": n, "color": c}} for n, size, c in langs]}}

    def day_list(year):
        start = date(year, 10, 8) if year == 2026 else date(year, 6, 1)
        return [{"date": (start + timedelta(days=i)).isoformat(),
                 "contributionCount": 1} for i in range(4 if year == 2026 else 9)]

    def fake(query, variables):
        calls.append(query)
        if "repositories" in query:
            assert "privacy: PUBLIC" in query and "isFork: false" in query
            first = variables["after"] is None
            return {"user": {
                "createdAt": "2024-10-15T21:22:45Z",
                "pullRequests": {"totalCount": 5},
                "repositories": {
                    "totalCount": 3,
                    "pageInfo": {"hasNextPage": first, "endCursor": "abc"},
                    "nodes": ([repo(2, ("Rust", 300, "#dea584"), ("GLSL", 100, None))]
                              if first else [repo(1, ("Python", 600, "#3572A5"))])}}}
        years = re.findall(r"y(\d{4}): contributionsCollection", query)
        assert years == ["2024", "2025", "2026"], years
        assert '"2024-10-15T00:00:00Z"' in query and '"2026-10-12T23:59:59Z"' in query
        return {"user": {"y" + y: {
            "totalCommitContributions": 10,
            "contributionCalendar": {"totalContributions": 20, "weeks": [
                {"contributionDays": day_list(int(y))}]}} for y in years}}

    s = b.fetch_stats(fake, TODAY)
    assert len(calls) == 3, "expected 2 profile pages + 1 calendar request"
    assert (s["total"], s["commits"], s["stars"], s["prs"], s["repos"]) == (60, 30, 3, 5, 3)
    assert s["current"] == b.Streak(4, date(2026, 10, 8), date(2026, 10, 11))
    assert s["longest"].length == 9
    assert [n for n, _, _ in s["languages"]] == ["Python", "Rust", "GLSL"]
    assert abs(s["languages"][0][1] - 60.0) < 1e-9
    assert s["languages"][2][2] == "#8B949E", "a null colour needs a fallback"


# ---------------------------------------------------------------- projects --

def test_project_cards_fit():
    for project in b.PROJECTS:
        lines = b.textwrap.wrap(project.desc, b.DESC_COLS)
        assert len(lines) <= b.DESC_LINES, "%s pitch is %d lines" % (project.key, len(lines))
        assert b.sans_width(project.name, b.TITLE_SIZE, True) * 1.08 <= b.TITLE_ROOM, project.name
        assert b.sans_width(project.figure, 34, True) <= b.CARD_W - 2 * b.CARD_PAD
        assert b.sans_width(project.figure_label, 12.5) * 1.08 <= b.CARD_W - 2 * b.CARD_PAD
        for p in (LIGHT, DARK):
            root = parse(b.build_project(p, project))
            tags = [r for r in root.iter() if local(r) == "rect" and r.get("height") == str(b.TAG_H)]
            assert len(tags) == len(project.tags), "%s dropped a tag" % project.key
            for r in tags:
                assert float(r.get("x")) + float(r.get("width")) <= b.CARD_W - b.CARD_PAD
            last_desc = b.DESC_Y + (len(lines) - 1) * 18
            assert float(tags[0].get("y")) - last_desc >= 16, "tags crowd the pitch"


# ------------------------------------------------------------------ assets --

def built():
    return dict(b.assets(SAMPLE_STATS))


def test_assets_are_wellformed_and_labelled():
    docs = built()
    for name, doc in docs.items():
        root = parse(doc)
        assert local(root) == "svg" and root.get("viewBox"), name
        assert not re.search(r"&(?!amp;|lt;|gt;|quot;|#)", doc), "%s has a raw &" % name
        if name == "project-spacer.svg":
            assert root.get("aria-hidden") == "true"
            continue
        assert root.get("role") == "img" and root.get("aria-label"), name
        assert any(local(e) == "title" and e.text for e in root), name


def test_nothing_starts_invisible():
    """No entry animations from zero: whatever a renderer shows before (or
    without) animation must already be the finished picture."""
    for name, doc in built().items():
        for e in parse(doc).iter():
            if local(e) in ("animate", "set"):
                continue
            for attr in ("opacity", "fill-opacity", "stroke-opacity"):
                assert e.get(attr) not in ("0", "0.0"), "%s: %s=0 on <%s>" % (name, attr, local(e))


def test_every_themed_asset_has_both_variants():
    names = set(built())
    for name in names:
        if name.endswith("-dark.svg"):
            assert name.replace("-dark.svg", ".svg") in names, name
        elif name != "project-spacer.svg":
            assert name.replace(".svg", "-dark.svg") in names, name


# ------------------------------------------------------ README and workflow --

class Html(HTMLParser):
    def __init__(self):
        super().__init__()
        self.imgs, self.pictures, self._pic = [], [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "picture":
            self._pic = {"sources": [], "img": None}
        elif tag == "source" and self._pic is not None:
            self._pic["sources"].append(a)
        elif tag == "img":
            self.imgs.append(a)
            if self._pic is not None:
                self._pic["img"] = a

    handle_startendtag = handle_starttag

    def handle_endtag(self, tag):
        if tag == "picture":
            self.pictures.append(self._pic)
            self._pic = None


def snake_outputs():
    return re.findall(r"^\s+dist/([\w-]+\.svg)", read(".github", "workflows", "profile.yml"),
                      re.M)


def test_readme_points_only_at_published_files():
    readme = read("README.md")
    used = set(re.findall(re.escape(RAW) + r"([\w.-]+)", readme))
    published = set(built()) | set(snake_outputs())
    assert used, "no raw asset URLs in the README"
    assert not used - published, "README links unpublished files: %s" % sorted(used - published)
    assert not published - used, "built but never shown: %s" % sorted(published - used)
    assert "raw.githubusercontent.com" not in readme.replace(RAW, ""), \
        "an asset URL points somewhere other than this repo's output branch"


def test_readme_images_have_alt_text_and_both_themes():
    page = Html()
    page.feed(read("README.md"))
    for img in page.imgs:
        assert "alt" in img, "an image has no alt attribute: %s" % img.get("src")
        if not img["src"].endswith("project-spacer.svg"):
            assert len(img["alt"]) >= 10, "alt text too thin: %r" % img["alt"]
    for pic in page.pictures:
        media = {s["media"]: s["srcset"] for s in pic["sources"]}
        dark, light = media["(prefers-color-scheme: dark)"], media["(prefers-color-scheme: light)"]
        assert dark == light.replace(".svg", "-dark.svg"), (light, dark)
        assert pic["img"]["src"] == light, "fallback <img> should be the light file"


def test_readme_links_have_no_stray_whitespace():
    """Whitespace inside an <a> renders as an underlined gap beside the
    image, so every linked picture is written tight."""
    readme = read("README.md")
    assert not re.search(r"<a [^>]*>\s+<picture", readme)
    assert not re.search(r"</picture>\s+</a>", readme)
    for project in b.PROJECTS:
        href = "https://github.com/{}/{}".format(b.USER, project.repo)
        m = re.search(r'<a href="%s"><picture>.*?</picture></a>' % re.escape(href), readme, re.S)
        assert m, "%s card is not linked to its repository" % project.key
        assert "project-%s.svg" % project.key in m.group(0)


def test_readme_section_order():
    readme = read("README.md")
    marks = ["banner.svg", "social-linkedin.svg", "## About me", "## Featured work",
             "## Tools I use", "## GitHub activity", "## Let's build something",
             "footer.svg"]
    at = [readme.find(m) for m in marks]
    assert all(i >= 0 for i in at), dict(zip(marks, at))
    assert at == sorted(at), "README sections are out of order"


def test_workflow():
    wf = read(".github", "workflows", "profile.yml")
    assert re.search(r"^permissions:\s*\n\s+contents: write", wf, re.M)
    for trigger in ("schedule:", "workflow_dispatch:", "push:"):
        assert trigger in wf, trigger
    assert wf.index("tools/test_build_assets.py") < wf.index("tools/build_assets.py"), \
        "tests must run before the build"
    assert sorted(snake_outputs()) == ["snake-dark.svg", "snake.svg"]
    assert "color_snake=#F59E0B" in wf
    assert "#CCFBF1,#99F6E4,#5EEAD4,#14B8A6,#0F766E" in wf
    assert "#0B1F3A,#134E4A,#0F766E,#14B8A6,#5EEAD4" in wf
    assert "publish_branch: output" in wf
    assert re.search(r"FETCH_HEAD:\$f", wf), "the stale-activity fallback is gone"


def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("ok    " + name)
        except Exception:                                   # noqa: BLE001
            failed += 1
            print("FAIL  " + name)
            traceback.print_exc()
    print("{} passed, {} failed".format(len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
