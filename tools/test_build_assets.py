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
    "total": 1204, "repos": 6, "active": 187,
    "current": b.Streak(4, date(2026, 10, 9), TODAY),
    "longest": b.Streak(19, date(2025, 12, 28), date(2026, 1, 15)),
    "since": date(2024, 10, 15), "today": TODAY,
    "languages": [("TypeScript", 33.2, "#3178c6"), ("JavaScript", 30.3, "#f1e05a"),
                  ("Rust", 13.9, "#dea584"), ("Python", 8.3, "#3572A5"),
                  ("GLSL", 5.2, "#5686a5")],
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

def typing_groups(root):
    """(group, text, clip rect, caret) for each typing line, in order."""
    clips = {c.get("id"): c[0] for c in root.iter() if local(c) == "clipPath"}
    out = []
    for g in root.iter():
        if local(g) == "g" and g.get("class") == "typing-line":
            text = next(e for e in g if local(e) == "text")
            caret = next(e for e in g if local(e) == "rect")
            clip = clips[re.search(r"#([\w-]+)", text.get("clip-path")).group(1)]
            out.append((g, text, clip, caret))
    return out


def own(e, attr):
    """The <animate> directly on e for attr."""
    return next(a for a in e if local(a) == "animate" and a.get("attributeName") == attr)


def cycle_ms(anim):
    return float(anim.get("dur")[:-2])


def at(anim, t):
    """A discrete whole-cycle <animate>'s value t ms into the cycle."""
    times = [float(k) for k in anim.get("keyTimes").split(";")]
    values = [float(v) for v in anim.get("values").split(";")]
    f = (t % cycle_ms(anim)) / cycle_ms(anim)
    return values[max(i for i, k in enumerate(times) if k <= f + 1e-12)]


TYPED = ((b.build_banner, b.TYPING_LINES), (b.build_footer, b.FOOTER_LINES))


def test_typing_never_times_off_another_element():
    """GitHub's iOS app ignores syncbase begins and starts every line at 0s,
    which stacked them. So every animation in the banner and footer begins
    at 0s, shares one cycle, and places itself with keyTimes alone."""
    for build, lines in TYPED:
        for p in (LIGHT, DARK):
            root = parse(build(p))
            durs = set()
            for e in root.iter():
                for attr in ("begin", "end"):
                    v = e.get(attr)
                    assert v is None or re.fullmatch(r"\d+(\.\d+)?(ms|s)?", v), \
                        "%s=%r is timed off another element" % (attr, v)
                assert local(e) != "set", "<set> is not cycle-placed"
                if local(e) == "animate":
                    assert e.get("begin") == "0s" and e.get("repeatCount") == "indefinite"
                    assert e.get("id") is None
                    durs.add(e.get("dur"))
                    kt = [float(k) for k in e.get("keyTimes").split(";")]
                    assert kt[0] == 0 and kt[-1] == 1, kt
                    assert all(a < z for a, z in zip(kt, kt[1:])), "keyTimes must rise"
                    assert len(kt) == len(e.get("values").split(";"))
            assert len(durs) == 1, "animations do not share one cycle: %s" % durs
            assert len(typing_groups(root)) == len(lines)


def test_exactly_one_line_is_visible_at_a_time():
    for build, lines in TYPED:
        groups = typing_groups(parse(build(LIGHT)))
        cycle = cycle_ms(own(groups[0][0], "opacity"))
        seen, t = set(), 0.0
        while t < cycle:
            on = [i for i, (g, _, _, _) in enumerate(groups) if at(own(g, "opacity"), t) == 1]
            assert len(on) == 1, "%d lines visible at %.0f ms" % (len(on), t)
            seen.add(on[0])
            t += 5.0
        assert seen == set(range(len(lines))), "a line never got its slot"


def test_typing_reveals_whole_characters_with_the_caret_in_step():
    for build, lines in TYPED:
        for i, (g, text, clip, caret) in enumerate(typing_groups(parse(build(DARK)))):
            x, full = float(text.get("x")), float(text.get("textLength"))
            adv = full / len(lines[i])
            gate, width = own(g, "opacity"), own(clip, "width")
            follow, blink = own(caret, "x"), own(caret, "opacity")
            reached, t = False, 0.0
            while t < cycle_ms(gate):
                if at(gate, t) == 1:
                    w = at(width, t)
                    assert abs(w / adv - round(w / adv)) < 0.01, "half a glyph at %.0f ms" % t
                    assert abs(at(follow, t) - (x + w + 1)) < 0.02, \
                        "line %d caret off the text at %.0f ms" % (i, t)
                    if 0 < w < full - 0.01:
                        assert at(blink, t) == 1, "caret blinked mid-word at %.0f ms" % t
                    reached = reached or abs(w - full) < 0.01
                t += 5.0
            assert reached, "%r never typed in full" % lines[i]


def test_static_frame_shows_line_zero_only():
    """With no SMIL at all the base attributes are the picture: line 0 typed
    out with its caret after it, every other line and caret hidden."""
    for build, lines in TYPED:
        for p in (LIGHT, DARK):
            for i, (g, text, clip, caret) in enumerate(typing_groups(parse(build(p)))):
                x, full = float(text.get("x")), float(text.get("textLength"))
                if i == 0:
                    assert g.get("opacity") in (None, "1")
                    assert float(clip.get("width")) >= full
                    assert abs(float(caret.get("x")) - (x + full + 1)) < 0.02
                else:
                    assert g.get("opacity") == "0", "line %d shows without SMIL" % i


def test_typing_lines_fit():
    for doc, width in ((b.build_banner(LIGHT), b.BANNER["width"]),
                       (b.build_footer(LIGHT), b.FOOTER["width"])):
        for _, t, _, _ in typing_groups(parse(doc)):
            x, w = float(t.get("x")), float(t.get("textLength"))
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


def test_every_glyph_reads_against_its_pill():
    """Every brand mark, in every built pill, in both themes, reaches the
    same 4.5:1 as text against that pill's own fill. A pale brand on a light
    pill may keep its colour only if its outline carries the contrast."""
    assert b.MARK_MIN >= b.TEXT_MIN == 4.5
    checked = 0
    for suffix, p in b.THEMES.items():
        docs = [b.build_stack(p)] + [b.build_social(p, label, slug)
                                     for _, label, slug in b.SOCIAL]
        for doc in docs:
            for g in chips(parse(doc)):
                pill = next(e for e in g if local(e) == "rect").get("fill")
                mark = next(e for e in g if local(e) == "path")
                fill, edge = mark.get("fill"), mark.get("stroke")
                ratio = b.contrast(edge or fill, pill)
                label = next(e for e in g if local(e) == "text").text
                assert ratio >= b.MARK_MIN, "%s%s: %s glyph is %.2f:1 on %s" % (
                    "dark " if suffix else "light ", label, edge or fill, ratio, pill)
                if edge:
                    assert not b.is_dark(pill), "%s: outlines are for light pills" % label
                    assert b.luminance(fill) >= b.PALE, "%s: only pale brands keep their fill" % label
                checked += 1
    assert checked == 2 * (sum(len(items) for _, items in b.STACK) + len(b.SOCIAL))
    assert b.mark_colors("#000000", DARK["inset"])[0] != "#000000", "Next.js vanished"
    assert b.mark_colors("#F7DF1E", LIGHT["inset"])[0] == "#F7DF1E", "JS lost its yellow"
    assert b.mark_colors("#3776AB", LIGHT["inset"]) == ("#3776AB", None), "a legible brand moved"


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


def test_stack_rows_fit_on_one_line():
    """Each heading's pills share one line; a wrapped row reads as a mistake."""
    for p in (LIGHT, DARK):
        rows = {}
        for g in chips(parse(b.build_stack(p))):
            rows.setdefault(g[0].get("y"), []).append(g)
        assert [len(r) for r in rows.values()] == [len(items) for _, items in b.STACK], \
            "a stack row wrapped: %s" % [len(r) for r in rows.values()]
    assert [h for h, _ in b.STACK][0] == "Apps and interfaces", "apps lead the panel"


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
        assert "1,204" in doc and "Other 9.1%" in doc and "6 public repos" in doc
        figures = [t for t in root.iter() if local(t) == "text" and t.get("font-size") == "30"]
        assert len(figures) == 4, "expected four headline figures"
        for gone in ("Stars", "Commits", "Pull requests"):
            assert gone not in doc, gone
        # Each caption fits its column.
        step = (b.PANEL_W - 2 * (b.PANEL_PAD + 2)) / 4.0
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
    """Pagination, one request for every year, public-only, the profile
    repository and markup languages left out of the mix, and the maths."""
    calls = []

    def repo(name, *langs):
        return {"name": name, "languages": {"edges": [
            {"size": size, "node": {"name": n, "color": c}} for n, size, c in langs]}}

    def day_list(year):
        start = date(year, 10, 8) if year == 2026 else date(year, 6, 1)
        return [{"date": (start + timedelta(days=i)).isoformat(),
                 "contributionCount": 1} for i in range(4 if year == 2026 else 9)]

    def fake(query, variables):
        calls.append(query)
        if "repositories" in query:
            assert "privacy: PUBLIC" in query and "isFork: false" in query
            assert "pullRequests" not in query and "stargazerCount" not in query
            first = variables["after"] is None
            return {"user": {
                "createdAt": "2024-10-15T21:22:45Z",
                "repositories": {
                    "totalCount": 4,
                    "pageInfo": {"hasNextPage": first, "endCursor": "abc"},
                    "nodes": ([repo("sonora", ("Rust", 300, "#dea584"), ("GLSL", 100, None),
                                    ("CSS", 5000, "#663399"), ("HTML", 900, "#e34c26")),
                               repo(b.USER, ("Python", 90000, "#3572A5"))]
                              if first else [repo("app", ("Python", 600, "#3572A5"),
                                                  ("SCSS", 70, "#c6538c"))])}}}
        years = re.findall(r"y(\d{4}): contributionsCollection", query)
        assert years == ["2024", "2025", "2026"], years
        assert '"2024-10-15T00:00:00Z"' in query and '"2026-10-12T23:59:59Z"' in query
        assert "totalCommitContributions" not in query
        return {"user": {"y" + y: {
            "contributionCalendar": {"totalContributions": 20, "weeks": [
                {"contributionDays": day_list(int(y))}]}} for y in years}}

    s = b.fetch_stats(fake, TODAY)
    assert len(calls) == 3, "expected 2 profile pages + 1 calendar request"
    assert (s["total"], s["repos"]) == (60, 4)
    assert s["active"] == 4, "only days inside the last year count"
    assert s["current"] == b.Streak(4, date(2026, 10, 8), date(2026, 10, 11))
    assert s["longest"].length == 9
    assert [n for n, _, _ in s["languages"]] == ["Python", "Rust", "GLSL"], \
        "markup must not reach the language mix"
    assert abs(s["languages"][0][1] - 60.0) < 1e-9, "shares are of code only"
    assert s["languages"][2][2] == "#8B949E", "a null colour needs a fallback"


# ---------------------------------------------------------------- projects --

def test_project_cards_fit():
    for project in b.PROJECTS:
        lines = b.textwrap.wrap(project.desc, b.DESC_COLS)
        assert 2 <= len(lines) <= b.DESC_LINES, "%s pitch is %d lines" % (project.key, len(lines))
        assert b.sans_width(project.what, 12.5) * b.TAGLINE_SLACK <= b.CARD_ROOM, project.what
        for p in (LIGHT, DARK):
            room = b.title_room(project, p)
            assert b.sans_width(project.name, b.TITLE_SIZE, True) * b.TAGLINE_SLACK <= room, \
                "%s runs into its corner badge" % project.name
            root = parse(b.build_project(p, project))
            tags = [r for r in root.iter() if local(r) == "rect" and r.get("height") == str(b.TAG_H)
                    and r.get("y") == str(b.TAG_Y)]
            assert len(tags) == len(project.tags), "%s dropped a tag" % project.key
            for r in tags:
                assert float(r.get("x")) + float(r.get("width")) <= b.CARD_W - b.CARD_PAD
            last_desc = b.DESC_Y + (len(lines) - 1) * 18
            assert float(tags[0].get("y")) - last_desc >= 16, "tags crowd the pitch"


def test_taglines_share_one_size_and_fit_one_line():
    """One size for all four, the largest (in half points) at which the
    longest tagline fits the card on a single line."""
    size, room = b.TAGLINE_SIZE, b.CARD_ROOM
    widths = [b.sans_width(pr.tagline, size, True) * b.TAGLINE_SLACK for pr in b.PROJECTS]
    assert max(widths) <= room, "a tagline overflows at %.1f px" % size
    assert max(b.sans_width(pr.tagline, size + 0.5, True) * b.TAGLINE_SLACK
               for pr in b.PROJECTS) > room, "%.1f px is not the largest that fits" % size
    assert 20 <= size <= 34, size
    for p in (LIGHT, DARK):
        for project in b.PROJECTS:
            root = parse(b.build_project(p, project))
            runs = [t for t in root.iter() if local(t) == "text" and t.text == project.tagline]
            assert len(runs) == 1 and not list(runs[0]), "%s tagline is split" % project.key
            assert float(runs[0].get("font-size")) == size, project.key
            assert runs[0].get("fill") == p["accent"] and runs[0].get("font-weight") == "700"


def test_status_pill_or_link_badge():
    """A private, unfinished project says so in the corner; every other card
    shows the arrow that says it opens the repository."""
    for p in (LIGHT, DARK):
        for project in b.PROJECTS:
            doc = b.build_project(p, project)
            root = parse(doc)
            badge = [c for c in root.iter() if local(c) == "circle" and c.get("r") == str(b.BADGE_R)]
            if project.status:
                assert ">{}<".format(project.status) in doc, project.key
                assert not badge, "%s has both a pill and a badge" % project.key
            else:
                assert project.repo and len(badge) == 1, project.key
    berthiq = next(pr for pr in b.PROJECTS if pr.key == "berthiq")
    assert berthiq.repo is None and berthiq.status == "in progress"
    assert [pr.key for pr in b.PROJECTS] == ["caravan", "berthiq", "sonora",
                                             "vessel-dashboard", "frappe-invoicing"]


# ------------------------------------------------------------------ assets --

def built():
    return dict(b.assets(SAMPLE_STATS))


def test_assets_are_wellformed_and_labelled():
    docs = built()
    for name, doc in docs.items():
        root = parse(doc)
        assert local(root) == "svg" and root.get("viewBox"), name
        assert not re.search(r"&(?!amp;|lt;|gt;|quot;|#)", doc), "%s has a raw &" % name
        assert root.get("role") == "img" and root.get("aria-label"), name
        assert any(local(e) == "title" and e.text for e in root), name


def test_nothing_starts_invisible():
    """No entry animations from zero: whatever a renderer shows before (or
    without) animation must already be the finished picture. The typing
    lines are the one exception, gated by design; the static frame test
    holds line 0 visible."""
    for name, doc in built().items():
        for e in parse(doc).iter():
            if local(e) in ("animate", "set") or e.get("class") == "typing-line":
                continue
            for attr in ("opacity", "fill-opacity", "stroke-opacity"):
                assert e.get(attr) not in ("0", "0.0"), "%s: %s=0 on <%s>" % (name, attr, local(e))


def test_every_themed_asset_has_both_variants():
    names = set(built())
    for name in names:
        if name.endswith("-dark.svg"):
            assert name.replace("-dark.svg", ".svg") in names, name
        else:
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
        assert len(img["alt"]) >= 10, "alt text too thin: %r" % img["alt"]
    for project in b.PROJECTS:
        card = RAW + "project-%s.svg" % project.key
        alts = [img["alt"] for img in page.imgs if img["src"] == card]
        assert alts == [b.card_alt(project)], "%s alt text drifted from the card" % project.key
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
        card = "project-%s.svg" % project.key
        line = next(ln for ln in readme.splitlines() if card in ln)
        if project.repo is None:
            assert "<a " not in line and "</a>" not in line, \
                "%s is private; its card must not link anywhere" % project.key
            continue
        href = "https://github.com/{}/{}".format(b.USER, project.repo)
        m = re.search(r'<a href="%s"><picture>.*?</picture></a>' % re.escape(href), line)
        assert m and card in m.group(0), "%s card is not linked to its repository" % project.key


def test_cards_sit_in_their_rows():
    """Two pairs, then Frappe centred on its own row."""
    readme = read("README.md")
    grid = readme[readme.index("## Featured work"):readme.index("## Tools I use")]
    rows = re.findall(r"<p align=\"center\">(.*?)</p>", grid, re.S)
    keys = [re.findall(r'<img [^>]*src="[^"]*/project-([\w-]+)\.svg"', row) for row in rows]
    assert keys == [["caravan", "sonora"], ["berthiq", "vessel-dashboard"],
                    ["frappe-invoicing"]], keys
    assert sorted(k for row in keys for k in row) == sorted(pr.key for pr in b.PROJECTS)
    assert "spacer" not in readme


def test_copy_leads_with_apps_and_names_no_ai_tools():
    """The reframe holds everywhere a reader sees words: the README and the
    text inside every asset, including their accessible names."""
    texts = [read("README.md")]
    for doc in built().values():
        root = parse(doc)
        texts += [root.get("aria-label") or ""]
        texts += [e.text or "" for e in root.iter() if local(e) in ("text", "title")]
    blob = "\n".join(texts)
    for phrase in ("backend", "data tooling", "data pipelines", "data-heavy"):
        assert phrase not in blob.lower(), "still framed around %r" % phrase
    assert not re.search(r"\b(AI|Claude|ChatGPT|Copilot|GPT|LLM)\b", blob), \
        "the profile mentions an AI tool"
    assert b.ROLE == "Software engineer building apps and tools"


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
