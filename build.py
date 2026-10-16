#!/usr/bin/env python3
"""Build the GitHub profile: SVGs in assets/, README.md and preview.html.

Reads colors from palette.toml and all text from content.toml.
Standard library only (Python 3.11+).

    python build.py            # fetch GitHub data (uses $GITHUB_TOKEN if set)
    python build.py --offline  # reuse assets/.cache.json, no network
"""
import argparse
import json
import os
import sys
import textwrap
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"
CACHE = ASSETS / ".cache.json"

W = 800  # every SVG is 800 wide; GitHub scales it down on narrow screens
MONO = "Consolas, 'DejaVu Sans Mono', 'Liberation Mono', 'Courier New', monospace"
SANS = "Arial, Helvetica, 'Liberation Sans', sans-serif"
MONO_ADV = 0.6   # monospace advance per em; textLength pins every mono line to it
SANS_ADV = 0.5   # average Arial-class advance; used both to wrap prose and to decide when to squeeze


def esc(s):
    return escape(str(s), {'"': "&quot;"})


def load_toml(name):
    with open(ROOT / name, "rb") as f:
        return tomllib.load(f)


# ------------------------------------------------------------------ primitives

def rect(x, y, w, h, fill, extra=""):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}"{extra}/>'


def text(x, y, parts, size, *, fill=None, mono=True, bold=False, max_w=None, anchor=None, cls=""):
    """One line of text. `parts` is a string or a list of (text, fill[, bold]).

    Mono text gets an explicit textLength so ASCII art, columns and the cursor
    line up whatever monospace font the viewer has. Anything estimated wider
    than max_w is squeezed to fit instead of overflowing the window.
    """
    if isinstance(parts, str):
        parts = [(parts, fill)]
    n = sum(len(p[0]) for p in parts)
    width = n * (MONO_ADV if mono else SANS_ADV) * size
    classes = ["m" if mono else "s"] + (["b"] if bold else []) + ([cls] if cls else [])
    attrs = f'x="{x:g}" y="{y:g}" font-size="{size}" class="{" ".join(classes)}"'
    if fill:
        attrs += f' fill="{fill}"'
    if anchor:
        attrs += f' text-anchor="{anchor}"'
    if max_w and width > max_w:
        attrs += f' textLength="{max_w:g}" lengthAdjust="spacingAndGlyphs"'
    elif mono and n > 1:
        attrs += f' textLength="{width:g}" lengthAdjust="spacing"'
    spans = []
    for p in parts:
        t, f = p[0], p[1]
        b = len(p) > 2 and p[2]
        a = (f' fill="{f}"' if f else "") + (' font-weight="700"' if b else "")
        spans.append(f"<tspan{a}>{esc(t)}</tspan>" if a else esc(t))
    return f"<text {attrs}>{''.join(spans)}</text>"


def bevel(x, y, w, h, tl_out, br_out, tl_in, br_in, fill):
    """Two-pixel Win95 bevel built from stacked rects (crisp at any scale)."""
    return "".join([
        rect(x, y, w, h, br_out),
        rect(x, y, w - 1, h - 1, tl_out),
        rect(x + 1, y + 1, w - 2, h - 2, br_in),
        rect(x + 1, y + 1, w - 3, h - 3, tl_in),
        rect(x + 2, y + 2, w - 4, h - 4, fill),
    ])


def raised(P, x, y, w, h, fill=None):
    return bevel(x, y, w, h, P["hilite"], P["dkshadow"], P["light"], P["shadow"], fill or P["face"])


def sunken(P, x, y, w, h, fill):
    return bevel(x, y, w, h, P["shadow"], P["hilite"], P["dkshadow"], P["light"], fill)


def shallow(P, x, y, w, h):
    """One-pixel sunken field, used by status bars."""
    return rect(x, y, w, h, P["hilite"]) + rect(x, y, w - 1, h - 1, P["shadow"]) + rect(x + 1, y + 1, w - 2, h - 2, P["face"])


# 16x16 title-bar icons as pixel maps. Fixed colors: they always sit on navy.
ICON_COLORS = {"k": "#000000", "w": "#ffffff", "g": "#c0c0c0", "d": "#808080",
               "y": "#ffff00", "o": "#c0c000", "c": "#00a8a8", "G": "#00ff00",
               "l": "#0a66c2", "B": "#0080ff", "v": "#00a800"}
ICONS = {
    "doc": [
        "..kkkkkkkk......", "..kwwwwwwkk.....", "..kwwwwwwkwk....", "..kwkkkkwkkkk...",
        "..kwwwwwwwwwk...", "..kwkkkkkkwwk...", "..kwwwwwwwwwk...", "..kwkkkkkkwwk...",
        "..kwwwwwwwwwk...", "..kwkkkkkkwwk...", "..kwwwwwwwwwk...", "..kwkkkkwwwwk...",
        "..kwwwwwwwwwk...", "..kkkkkkkkkkk...",
    ],
    "folder": [
        "................", "..kkkkk.........", ".kyyyyyk........", "kyyyyyyykkkkkkk.",
        "kywwwwwwwwwwwok.", "kywyyyyyyyyyyok.", "kywyyyyyyyyyyok.", "kywyyyyyyyyyyok.",
        "kywyyyyyyyyyyok.", "kywyyyyyyyyyyok.", "kywyyyyyyyyyyok.", "kyoooooooooooook",
        "kkkkkkkkkkkkkkkk",
    ],
    "term": [
        "................", "kkkkkkkkkkkkkkkk", "kggggggggggggggk", "kgkkkkkkkkkkkkgk",
        "kgkGkkkkkkkkkkgk", "kgkkGkkkkkkkkkgk", "kgkGkkkkkkkkkkgk", "kgkkkkGGGGkkkkgk",
        "kgkkkkkkkkkkkkgk", "kggggggggggggggk", "kkkkkkkkkkkkkkkk", ".....kggggk.....",
        "...kkkkkkkkkk...",
    ],
    "db": [
        "....kkkkkkk.....", "..kkwwwwwwwkk...", ".kwwwwwwwwwwwk..", ".kkwwwwwwwwwkk..",
        ".kcckkkkkkkcck..", ".kccccccccccck..", ".kkccccccccckk..", ".kcckkkkkkkcck..",
        ".kccccccccccck..", ".kkccccccccckk..", ".kcckkkkkkkcck..", ".kccccccccccck..",
        "..kkccccccckk...", "....kkkkkkk.....",
    ],
    "linkedin": [
        ".llllllllllllll.", "llllllllllllllll", "llwwllllllllllll", "llwwllllllllllll",
        "llllllllllllllll", "llwwllwwlwwwllll", "llwwllwwwwwwwlll", "llwwllwwwllwwlll",
        "llwwllwwllllwwll", "llwwllwwllllwwll", "llwwllwwllllwwll", "llwwllwwllllwwll",
        "llllllllllllllll", "llllllllllllllll", ".llllllllllllll.",
    ],
    "web": [
        ".....kkkkkk.....", "...kkBBvvBBkk...", "..kBBvvvvBBBBk..", ".kBBBvvvBBBvvBk.",
        ".kBBBBvvBBvvvBk.", "kBBBBBvBBBvvBBBk", "kBvvBBBBBBvBBBBk", "kBvvvBBBBBBBBBBk",
        "kBBvvvBBBBBBvvBk", "kBBBvvBBBBBvvvBk", ".kBBBvBBBBBvvBk.", ".kBBBBBBBBBvBBk.",
        "..kBBBBBBBBBBk..", "...kkBBBBBBkk...", ".....kkkkkk.....",
    ],
}


def pixel_icon(name, x, y, scale=1):
    out = []
    for r, row in enumerate(ICONS[name]):
        c = 0
        while c < len(row):  # merge horizontal runs of one color into a single rect
            ch, run = row[c], 1
            while c + run < len(row) and row[c + run] == ch:
                run += 1
            if ch != ".":
                out.append(rect(x + c * scale, y + r * scale, run * scale, scale, ICON_COLORS[ch]))
            c += run
    return f'<g shape-rendering="crispEdges">{"".join(out)}</g>'


def title_buttons(P, right, y):
    """Minimize, maximize, close. Glyphs are rects/paths, not font characters."""
    bw, bh, g = 20, 18, P["glyph"]
    xc = right - bw
    xmax = xc - 2 - bw
    xmin = xmax - bw
    out = [raised(P, xmin, y, bw, bh), raised(P, xmax, y, bw, bh), raised(P, xc, y, bw, bh)]
    out.append(rect(xmin + 5, y + 11, 7, 2, g))
    out.append(rect(xmax + 4, y + 3, 11, 2, g) + rect(xmax + 4, y + 3, 1, 10, g)
               + rect(xmax + 14, y + 3, 1, 10, g) + rect(xmax + 4, y + 12, 11, 1, g))
    out.append(f'<path d="M{xc + 5} {y + 4}L{xc + 13} {y + 12}M{xc + 13} {y + 4}L{xc + 5} {y + 12}" '
               f'stroke="{g}" stroke-width="2"/>')
    return "".join(out)


def svg_doc(w, h, body, label, style=""):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{esc(label)}">\n'
        f"<title>{esc(label)}</title>\n"
        f"<style>text{{white-space:pre}}.m{{font-family:{MONO}}}.s{{font-family:{SANS}}}"
        f".b{{font-weight:700}}{style}</style>\n"
        f"{body}\n</svg>\n"
    )


def window(P, title, icon, body, *, body_bg=None, menu=None, address=None, status=None):
    """Win95 window around `body(ox, oy, iw) -> (svg, height)`. Returns (svg, total height)."""
    chrome, y = [], 4
    # title bar
    chrome.append(rect(4, 4, W - 8, 26, "url(#tb)"))
    chrome.append(pixel_icon(icon, 9, 9))
    chrome.append(text(31, 22, title, 14, fill=P["title_text"], mono=False, bold=True, max_w=W - 31 - 90))
    chrome.append(title_buttons(P, W - 8, 8))
    y = 32
    if menu:
        x = 12
        for item in menu:
            chrome.append(text(x, y + 16, item, 13, fill=P["glyph"], mono=False))
            chrome.append(rect(x, y + 18, 7, 1, P["glyph"]))  # Alt-key underline on the first letter
            x += int(len(item) * 7.5) + 18
        y += 22
    if address:
        chrome.append(text(12, y + 18, "Address", 13, fill=P["glyph"], mono=False))
        chrome.append(sunken(P, 72, y + 2, W - 84, 24, P["content_bg"]))
        chrome.append(pixel_icon("folder", 78, y + 7))
        chrome.append(text(100, y + 19, address, 13, fill=P["text"], mono=False, max_w=W - 120))
        y += 30
    y += 2
    content, ch = body(10, y + 2, W - 20)
    panel = sunken(P, 8, y, W - 16, ch + 4, body_bg or P["content_bg"])
    y += ch + 4
    if status:
        y += 3
        x, total = 8, W - 16
        widths = [total - 260, 258] if len(status) == 2 else [total]
        for seg, sw in zip(status, widths):
            chrome.append(shallow(P, x, y, sw, 22))
            chrome.append(text(x + 6, y + 16, seg, 13, fill=P["glyph"], mono=False, max_w=sw - 12))
            x += sw + 2
        y += 22
    h = y + 7
    # Win95 window frame: outer light/dkshadow, inner hilite/shadow.
    frame = bevel(0, 0, W, h, P["light"], P["dkshadow"], P["hilite"], P["shadow"], P["face"])
    defs = (f'<defs><linearGradient id="tb" x1="0" x2="1"><stop offset="0" stop-color="{P["title_a"]}"/>'
            f'<stop offset="1" stop-color="{P["title_b"]}"/></linearGradient></defs>')
    return defs + f'<g shape-rendering="crispEdges">{frame}{panel}</g>' + "".join(chrome) + content, h


def balanced_wrap(s, chars):
    """Wrap to the fewest lines, then narrow the width so lines come out even (no orphans)."""
    lines = textwrap.wrap(s, chars)
    while chars > 10 and len(textwrap.wrap(s, chars - 1)) == len(lines):
        chars -= 1
    return textwrap.wrap(s, chars)


def fit(s, chars):
    return s if len(s) <= chars else s[: max(chars - 1, 0)].rstrip() + "…"


# ---------------------------------------------------------------------- banner

FONT = {
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".####", "#....", "#....", "#....", "#....", "#....", ".####"],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "F": ["#####", "#....", "#....", "####.", "#....", "#....", "#...."],
    "G": [".####", "#....", "#....", "#..##", "#...#", "#...#", ".####"],
    "H": ["#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "I": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"],
    "J": ["..###", "...#.", "...#.", "...#.", "...#.", "#..#.", ".##.."],
    "K": ["#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"],
    "L": ["#....", "#....", "#....", "#....", "#....", "#....", "#####"],
    "M": ["#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"],
    "N": ["#...#", "##..#", "##..#", "#.#.#", "#..##", "#..##", "#...#"],
    "O": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "P": ["####.", "#...#", "#...#", "####.", "#....", "#....", "#...."],
    "Q": [".###.", "#...#", "#...#", "#...#", "#.#.#", "#..#.", ".##.#"],
    "R": ["####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"],
    "S": [".####", "#....", "#....", ".###.", "....#", "....#", "####."],
    "T": ["#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "U": ["#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "V": ["#...#", "#...#", "#...#", "#...#", ".#.#.", ".#.#.", "..#.."],
    "W": ["#...#", "#...#", "#...#", "#.#.#", "#.#.#", "##.##", "#...#"],
    "X": ["#...#", "#...#", ".#.#.", "..#..", ".#.#.", "#...#", "#...#"],
    "Y": ["#...#", "#...#", ".#.#.", "..#..", "..#..", "..#..", "..#.."],
    "Z": ["#####", "....#", "...#.", "..#..", ".#...", "#....", "#####"],
    " ": ["...", "...", "...", "...", "...", "...", "..."],
}


def banner(C, P, T):
    word = C["profile"]["banner"].upper()
    glyphs = [FONT[ch] for ch in word if ch in FONT]
    cols = sum(len(g[0]) for g in glyphs) + len(glyphs) - 1
    cell = min(12, (W - 64) // cols)
    x0 = (W - cols * cell) // 2
    y0 = 30
    shadow, fg = [], []
    x = x0
    for g in glyphs:
        for r, row in enumerate(g):
            for c, on in enumerate(row):
                if on == "#":
                    px, py = x + c * cell, y0 + r * cell
                    # Each "pixel" is a block character with a 1px gutter, so the
                    # letters read as ASCII blocks rather than a solid font.
                    shadow.append(rect(px + 3, py + 3, cell - 1, cell - 1, P["banner_shadow"]))
                    fg.append(rect(px, py, cell - 1, cell - 1, P["banner_fg"]))
        x += (len(g[0]) + 1) * cell
    letters_bottom = y0 + 7 * cell
    sub_y = letters_bottom + 36
    web_y = sub_y + 28
    h = web_y + 24 + 36
    tb_y = h - 32
    parts = [
        rect(0, 0, W, h, P["desktop"]),
        f'<g shape-rendering="crispEdges">{"".join(shadow)}{"".join(fg)}</g>',
        text(W / 2, sub_y, C["profile"]["tagline"], 16, fill=P["banner_fg"], anchor="middle", max_w=W - 64),
        text(W / 2, web_y, C["profile"]["website"], 16, fill=P["banner_fg"], bold=True, anchor="middle"),
        # taskbar, as on the site
        f'<g shape-rendering="crispEdges">{rect(0, tb_y, W, 32, P["face"])}{rect(0, tb_y, W, 1, P["light"])}'
        f'{rect(0, tb_y + 1, W, 1, P["hilite"])}{raised(P, 4, tb_y + 4, 70, 24)}'
        f'{rect(10, tb_y + 10, 5, 5, "#ff0000")}{rect(16, tb_y + 10, 5, 5, "#00a800")}'
        f'{rect(10, tb_y + 16, 5, 5, "#0000ff")}{rect(16, tb_y + 16, 5, 5, "#ffff00")}'
        f'{shallow(P, W - 124, tb_y + 5, 120, 22)}</g>',
        text(26, tb_y + 21, "Start", 13, fill=P["glyph"], mono=False, bold=True),
        text(W - 64, tb_y + 21, "@" + C["profile"]["github"], 13, fill=P["glyph"], mono=False, anchor="middle"),
    ]
    label = f'{C["profile"]["name"]}: {C["profile"]["tagline"]}'
    return svg_doc(W, h, "".join(parts), label)


# -------------------------------------------------------------------- terminal

def terminal(C, P, T, data):
    prof, term = C["profile"], C["terminal"]
    fs, lh = 17, 24
    adv = MONO_ADV * fs
    logo = [l.rstrip() for l in term["logo"].strip("\n").split("\n")]
    values = {
        "repos": data.get("repos", "?"),
        "pinned": ", ".join(data.get("pinned", [])) or "-",
        "last_push": (f'{data["last_repo"]} ({data["last_date"]})' if data.get("last_repo") else "-"),
        "last_msg": data.get("last_msg") or "-",
    }

    def prompt(cmd=""):
        return [(f'{prof["user"]}@{prof["host"]}', T["key"], True), (":", T["fg"]),
                ("~", T["path"], True), ("$ ", T["fg"])] + ([(cmd, T["fg"])] if cmd else [])

    def body(ox, oy, iw):
        pad = 18
        x, y = ox + pad, oy + pad + 14
        out = [text(x, y, prompt("neofetch"), fs)]
        y += lh * 2
        top = y
        for i, line in enumerate(logo):
            out.append(text(x, top + i * lh, line, fs, fill=T["key"], bold=True))
        ix = x + (max(len(l) for l in logo) + 3) * adv
        room = int((ox + iw - pad - ix) / adv)
        head = f'{prof["user"]}@{prof["host"]}'
        out.append(text(ix, y, [(prof["user"], T["key"], True), ("@", T["fg"]), (prof["host"], T["key"], True)], fs))
        y += lh
        out.append(text(ix, y, "-" * len(head), fs, fill=T["muted"]))
        for key, val in term["rows"]:
            y += lh
            val = fit(val.format(**values), room - len(key) - 2)
            out.append(text(ix, y, [(key, T["key"], True), (": " + val, T["fg"])], fs, max_w=room * adv))
        y += lh
        for row in range(2):
            y += 4
            for i in range(8):
                out.append(rect(round(ix + i * 3 * adv), y + row * 20, round(3 * adv), 20, T["ansi"][row * 8 + i]))
        y += 40 + lh + 14
        y = max(y, top + len(logo) * lh + lh)
        p = prompt()
        out.append(text(x, y, p, fs))
        cx = x + sum(len(s[0]) for s in p) * adv
        out.append(rect(round(cx), y - 16, round(adv), 20, T["cursor"], ' class="cursor"'))
        return "".join(out), y + pad - oy

    svg, h = window(P, term["title"], "term", body, body_bg=T["bg"])
    style = ("@keyframes blink{50%{opacity:0}}.cursor{animation:blink 1.1s steps(1) infinite}"
             "@media (prefers-reduced-motion:reduce){.cursor{animation:none}}")
    rows = "; ".join(f"{k}: {v.format(**values)}" for k, v in term["rows"])
    return svg_doc(W, h, svg, f"neofetch for {prof['name']}. {rows}", style)


# ----------------------------------------------------------------------- about

def about(C, P, T):
    prof, ab = C["profile"], C["about"]

    def body(ox, oy, iw):
        pad = 22
        x, y, w = ox + pad, oy + pad, iw - 2 * pad
        out = []
        y += 24
        out.append(text(x, y, prof["name"], 28, fill=P["text"], mono=False, bold=True))
        y += 30
        out.append(text(x, y, f'{prof["role"]} · {prof["location"]}', 17, fill=P["accent"], max_w=w))
        y += 18
        out.append(rect(x, y, w, 1, P["rule"]))
        y += 8
        per_line = int(w / (SANS_ADV * 18))
        for para in ab["paragraphs"]:
            for line in balanced_wrap(para, per_line):
                y += 28
                out.append(text(x, y, line, 18, fill=P["text"], mono=False, max_w=w))
            y += 10
        y += 26
        out.append(text(x, y, "focus:", 17, fill=P["muted"]))
        for item in ab["focus"]:
            y += 27
            out.append(text(x, y, [("  > ", P["accent"], True), (item, P["text"])], 17, max_w=w))
        return "".join(out), y + pad - oy + 4

    svg, h = window(P, ab["title"], "doc", body, menu=["File", "Edit", "Search", "Help"])
    label = f'About {prof["name"]}. ' + " ".join(ab["paragraphs"])
    return svg_doc(W, h, svg, label)


# ----------------------------------------------------------------------- stack

def stack(C, P, T):
    st = C["stack"]
    fs, nfs, lh = 18, 17, 31
    adv = MONO_ADV * fs

    def body(ox, oy, iw):
        pad = 22
        x, y = ox + pad, oy + pad + 14
        out = [text(x, y, st["root"], fs, fill=P["text"], bold=True)]
        rows = []
        groups = st["groups"]
        for gi, g in enumerate(groups):
            glast = gi == len(groups) - 1
            rows.append(("├── " if not glast else "└── ", g["name"] + "/", None, "group"))
            for ii, it in enumerate(g["items"]):
                ilast = ii == len(g["items"]) - 1
                pre = ("    " if glast else "│   ") + ("└── " if ilast else "├── ")
                rows.append((pre, it["name"], it["note"], "item"))
        name_cols = max(len(r[0]) + len(r[1]) for r in rows)
        note_x = x + (name_cols + 4) * adv
        for pre, name, note, kind in rows:
            y += lh
            color = P["accent"] if kind == "group" else P["text"]
            out.append(text(x, y, [(pre, P["muted"]), (name, color, True)], fs))
            if note:
                end = x + (len(pre) + len(name)) * adv + 8
                out.append(f'<line x1="{end:g}" y1="{y - 4}" x2="{note_x - 10:g}" y2="{y - 4}" '
                           f'stroke="{P["muted"]}" stroke-width="1.5" stroke-dasharray="1.5 5"/>')
                out.append(text(note_x, y, note, nfs, fill=P["muted"], max_w=ox + iw - pad - note_x))
        return "".join(out), y + pad - oy + 4

    svg, h = window(P, st["title"], "db", body)
    names = [it["name"] for g in st["groups"] for it in g["items"]]
    return svg_doc(W, h, svg, "Stack: " + ", ".join(names))


# -------------------------------------------------------------------- projects

def projects(C, P, T):
    pr = C["projects"]
    items = pr["items"]
    cols = [("Name", 250), ("Type", 120), ("Description", None)]

    def body(ox, oy, iw):
        out = []
        x = ox
        widths = [c[1] or iw - sum(w for _, w in cols if w) for c in cols]
        for (label, _), cw in zip(cols, widths):
            out.append(f'<g shape-rendering="crispEdges">{raised(P, x, oy, cw, 22)}</g>')
            out.append(text(x + 8, oy + 16, label, 13, fill=P["glyph"], mono=False))
            x += cw
        y = oy + 22
        for it in items:
            y += 36
            cx = ox + 8
            out.append(pixel_icon("folder", cx, y - 18))
            out.append(text(cx + 24, y - 4, fit(it["name"], int((widths[0] - 40) / (SANS_ADV * 17))),
                            17, fill=P["text"], mono=False))
            cx = ox + widths[0] + 8
            out.append(text(cx, y - 4, it["kind"], 16, fill=P["muted"], mono=False, max_w=widths[1] - 16))
            cx += widths[1]
            out.append(text(cx, y - 4, fit(it["about"], int((widths[2] - 20) / (SANS_ADV * 16))),
                            16, fill=P["text"], mono=False))
        return "".join(out), y - oy + 14

    status = [f"{len(items)} object(s)", f'github.com/{C["profile"]["github"]}']
    svg, h = window(P, pr["title"], "folder", body, menu=["File", "Edit", "View", "Help"],
                    address=pr["title"], status=status)
    label = "Projects: " + "; ".join(f'{i["name"]} ({i["kind"]}): {i["about"]}' for i in items)
    return svg_doc(W, h, svg, label)


# ---------------------------------------------------------------- desktop icon

ICON_W = 150


def desktop_icon(P, link):
    """A Win95 desktop shortcut: 48px pixel icon with its label underneath."""
    h, size = 96, 48
    body = (pixel_icon(link["icon"], (ICON_W - size) // 2, 6, scale=3)
            + text(ICON_W / 2, 80, link["label"], 14, fill=P["text"], mono=False, anchor="middle", max_w=ICON_W - 8))
    return svg_doc(ICON_W, h, body, link["label"])


# ------------------------------------------------------------------ GitHub data

def gh(url, token, payload=None):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "profile-readme-build"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def fetch_github(user, token):
    api = "https://api.github.com"
    out = {"repos": gh(f"{api}/users/{user}", token)["public_repos"]}
    repos = gh(f"{api}/users/{user}/repos?type=owner&sort=pushed&per_page=100", token)
    # Skip forks and this profile repo: the daily bot commit would always win "last push".
    own = [r for r in repos if not r["fork"] and r["name"].lower() != user.lower()]

    pinned = []
    if token:  # pinned items are only exposed through GraphQL, which needs a token
        q = ("query($u:String!){user(login:$u){pinnedItems(first:6,types:REPOSITORY)"
             "{nodes{... on Repository{name}}}}}")
        res = gh(f"{api}/graphql", token, {"query": q, "variables": {"u": user}})
        nodes = (((res.get("data") or {}).get("user") or {}).get("pinnedItems") or {}).get("nodes") or []
        pinned = [n["name"] for n in nodes if n]
    if not pinned:
        pinned = [r["name"] for r in sorted(own, key=lambda r: (-r["stargazers_count"], r["name"].lower()))]
    out["pinned"] = pinned[:3]

    if own:
        last = own[0]
        commits = gh(f'{api}/repos/{last["full_name"]}/commits?per_page=1', token)
        out["last_repo"] = last["name"]
        if commits:
            c = commits[0]["commit"]
            out["last_date"] = c["committer"]["date"][:10]
            out["last_msg"] = c["message"].splitlines()[0]
        else:
            out["last_date"] = last["pushed_at"][:10]
    return out


def github_data(user, offline):
    cached = json.loads(CACHE.read_text("utf-8")) if CACHE.exists() else {}
    if offline:
        return cached
    try:
        data = fetch_github(user, os.environ.get("GITHUB_TOKEN"))
    except (urllib.error.URLError, KeyError, TimeoutError, json.JSONDecodeError) as e:
        print(f"warning: GitHub fetch failed ({e}); using cached data", file=sys.stderr)
        return cached
    CACHE.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n", "utf-8")
    return data


# --------------------------------------------------------- README and preview

SECTIONS = ["banner", "terminal", "about", "stack", "projects"]


def picture(name, alt, width=W):
    return (f'<picture>\n'
            f'  <source media="(prefers-color-scheme: dark)" srcset="assets/{name}-dark.svg">\n'
            f'  <source media="(prefers-color-scheme: light)" srcset="assets/{name}-light.svg">\n'
            f'  <img src="assets/{name}-light.svg" width="{width}" alt="{esc(alt)}">\n'
            f'</picture>')


def readme(C, alts):
    links = " · ".join(f'<a href="{esc(l["url"])}">{esc(l["label"])}</a>' for l in C["links"])
    proj = " · ".join(f'<a href="{esc(p["url"])}">{esc(p["name"])}</a>' for p in C["projects"]["items"])
    icons = "\n".join(f'<a href="{esc(l["url"])}">{picture("icon-" + l["icon"], alts["icon-" + l["icon"]], ICON_W)}</a>'
                      for l in C["links"] if l.get("icon"))
    blocks = [
        "<!-- Generated by build.py from content.toml and palette.toml. Edit those, not this file. -->",
        '<div align="center">',
        picture("banner", alts["banner"]),
        f"<p>{links}</p>",
        picture("terminal", alts["terminal"]),
        picture("about", alts["about"]),
        picture("stack", alts["stack"]),
        picture("projects", alts["projects"]),
        f"<p>{proj}</p>",
        f"<p>\n{icons}\n</p>",
        "</div>",
    ]
    return "\n\n".join(blocks) + "\n"


PREVIEW = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Profile preview</title>
<style>
  :root { --bg: #ffffff; --fg: #1f2328; --line: #d1d9e0; }
  body[data-theme="dark"] { --bg: #0d1117; --fg: #f0f6fc; --line: #3d444d; }
  body { margin: 0; background: var(--bg); color: var(--fg);
         font: 14px/1.5 -apple-system, "Segoe UI", Arial, sans-serif; }
  header { position: sticky; top: 0; display: flex; flex-wrap: wrap; gap: 16px; align-items: center;
           padding: 12px 16px; background: var(--bg); border-bottom: 1px solid var(--line); }
  h1 { font-size: 16px; margin: 0 8px 0 0; }
  fieldset { display: flex; gap: 4px; border: 0; margin: 0; padding: 0; }
  legend { float: left; margin-right: 8px; padding-top: 4px; }
  button { font: inherit; padding: 4px 12px; border: 1px solid var(--line); border-radius: 6px;
           background: transparent; color: var(--fg); cursor: pointer; }
  button[aria-pressed="true"] { background: var(--fg); color: var(--bg); }
  main { margin: 24px auto; padding: 0 16px; max-width: 830px; }
  main.narrow { max-width: 358px; }
  main img { display: block; width: 100%; height: auto; margin: 0 auto 16px; }
  .icons { display: flex; justify-content: center; gap: 16px; }
  .icons img { width: 150px; margin: 0; }
</style>
</head>
<body data-theme="light">
<header>
  <h1>Profile preview</h1>
  <fieldset><legend>Theme</legend>
    <button type="button" data-theme="light" aria-pressed="true">Light</button>
    <button type="button" data-theme="dark" aria-pressed="false">Dark</button>
  </fieldset>
  <fieldset><legend>Width</legend>
    <button type="button" data-width="wide" aria-pressed="true">Desktop</button>
    <button type="button" data-width="narrow" aria-pressed="false">Phone (390px)</button>
  </fieldset>
</header>
<main>
__IMAGES__
</main>
<script>
  const main = document.querySelector("main");
  function press(attr, value) {
    document.querySelectorAll(`button[${attr}]`).forEach(b =>
      b.setAttribute("aria-pressed", String(b.getAttribute(attr) === value)));
  }
  document.querySelectorAll("button[data-theme]").forEach(b => b.addEventListener("click", () => {
    const t = b.dataset.theme;
    document.body.dataset.theme = t;
    document.querySelectorAll("main img").forEach(img => { img.src = `assets/${img.dataset.name}-${t}.svg`; });
    press("data-theme", t);
  }));
  document.querySelectorAll("button[data-width]").forEach(b => b.addEventListener("click", () => {
    main.classList.toggle("narrow", b.dataset.width === "narrow");
    press("data-width", b.dataset.width);
  }));
</script>
</body>
</html>
"""


def preview(alts):
    img = lambda n: f'<img data-name="{n}" src="assets/{n}-light.svg" alt="{esc(alts[n])}">'
    icons = "".join(img(n) for n in alts if n.startswith("icon-"))
    imgs = "\n".join(img(n) for n in SECTIONS) + f'\n<div class="icons">{icons}</div>'
    return PREVIEW.replace("__IMAGES__", imgs)


# ------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="skip the GitHub API, use the cache")
    args = ap.parse_args()

    C = load_toml("content.toml")
    pal = load_toml("palette.toml")
    T = pal["terminal"]
    ASSETS.mkdir(exist_ok=True)
    data = github_data(C["profile"]["github"], args.offline)

    builders = {
        "banner": lambda P: banner(C, P, T),
        "terminal": lambda P: terminal(C, P, T, data),
        "about": lambda P: about(C, P, T),
        "stack": lambda P: stack(C, P, T),
        "projects": lambda P: projects(C, P, T),
    }
    for link in C["links"]:
        if link.get("icon"):
            builders["icon-" + link["icon"]] = lambda P, link=link: desktop_icon(P, link)
    alts = {}
    for name, build in builders.items():
        for theme in ("light", "dark"):
            svg = build(pal[theme])
            (ASSETS / f"{name}-{theme}.svg").write_text(svg, "utf-8", newline="\n")
        alts[name] = svg.split("<title>", 1)[1].split("</title>", 1)[0]
        alts[name] = alts[name].replace("&amp;", "&").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">")

    (ROOT / "README.md").write_text(readme(C, alts), "utf-8", newline="\n")
    (ROOT / "preview.html").write_text(preview(alts), "utf-8", newline="\n")
    print(f"built {len(builders) * 2} SVGs, README.md and preview.html")


if __name__ == "__main__":
    main()
