"""Build the tie-mark prototype page for #69.

The page is the Rated screen's wall drawn on the real frontend/src/styles.css - every token,
the grid, the stamp and the poster are the production ones - with the tie rules overridden
per treatment by treatments.css beside this file, and a prototype-only bar that switches
treatment, row edge, tone, theme and width. Two things the app does not have are added
here and never land: the dark token block is rewritten so [data-theme] can force a theme,
and the 640px media query is rewritten as a container query on a wrapper around the app,
which phone width sizes to 390px, so the production phone rules fire inside a frame on a
desktop screen exactly as they do on a phone.

    python3 build.py                       # tie-mark.html beside this file, posters hotlinked
    python3 build.py --embed posters.json --artifact --out /tmp/tie-mark-artifact.html

--embed inlines posters from a JSON map of title -> {"data": <data URI>} for the artifact
sandbox, which blocks images from other hosts; --artifact emits the body-only fragment the
Artifact tool wraps in its own document skeleton.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
STYLES = ROOT / "frontend" / "src" / "styles.css"
FONT = ROOT / "frontend" / "public" / "fonts" / "Geist-Variable.woff2"
TREATMENTS = HERE / "treatments.css"
IMAGE_BASE = "https://image.tmdb.org/t/p/w342"


@dataclass(frozen=True)
class Film:
    title: str
    year: int
    path: str
    anchor: bool = False
    provisional: bool = False
    flagged: bool = False


def F(title: str, year: int, path: str, **marks: bool) -> Film:
    return Film(title, year, path, **marks)


# The fixture. One band of eighteen with every case the ticket names - a tie of two, a tie
# that turns a row (six across at desktop, three on a phone), two groups back to back, a
# loose film between two groups - and a second band that is one whole-band seed group of
# thirty. Each band is a list of slots; a slot with more than one film is a tie.
CRAFTED: list[list[Film]] = [
    [F("Parasite", 2019, "/7IiTTgloJzvGI1TAYymCfbfl3vT.jpg", anchor=True)],
    [
        F("Whiplash", 2014, "/7fn624j5lj3xTme2SgiLCeuedmO.jpg"),
        F("The Social Network", 2010, "/n0ybibhJtQ5icDqTp8eRytcIHJx.jpg"),
    ],
    [F("In the Mood for Love", 2000, "/iYypPT4bhqXfq1b6EnmxvRt6b2Y.jpg")],
    [
        F("Zodiac", 2007, "/6YmeO4pB7XTh8P8F960O1uA14JO.jpg"),
        F("No Country for Old Men", 2007, "/6d5XOczc226jECq0LIX0siKtgHR.jpg"),
        F("There Will Be Blood", 2007, "/fa0RDkAlCec0STeMNAhPaF89q6U.jpg"),
        F("Chungking Express", 1994, "/43I9DcNoCzpyzK8JCkJYpHqHqGG.jpg", flagged=True),
    ],
    [
        F("Heat", 1995, "/gKaePbkEkaqvMtw74EyhhkfCKKh.jpg"),
        F("Arrival", 2016, "/pEzNVQfdzYDzVK0XqxERIw2x2se.jpg"),
        F("Portrait of a Lady on Fire", 2019, "/rUDuOKpkKBHxx41BScqKej72iT3.jpg"),
    ],
    [F("Blade Runner 2049", 2017, "/gajva2L0rPYkEWjzgFlBXCAVBE5.jpg")],
    [
        F("Aftersun", 2022, "/evKz85EKouVbIr51zy5fOtpNRPg.jpg", provisional=True),
        F("Past Lives", 2023, "/k3waqVXSnvCZWfJYNtdamTgTtTA.jpg"),
    ],
    [F("Mad Max: Fury Road", 2015, "/ulcAi4dKpAjHwYGS08vNyx9H6I9.jpg")],
    [F("Inception", 2010, "/xlaY2zyzMfkhk0HSC5VUwzoZPU1.jpg")],
    [F("Prisoners", 2013, "/uhviyknTT5cEQXbn6vWIqfM4vGm.jpg")],
    [F("The Grand Budapest Hotel", 2014, "/eWdyYQreja6JGCzqHWXpWHDrrPo.jpg")],
]

SEED: list[Film] = [
    F("Her", 2013, "/eCOtqtfvn7mxGl6nfmq4b1exJRc.jpg"),
    F("Dune", 2021, "/v1tRXZ4JtD2Iv6fjkPvT4GiwslV.jpg"),
    F("Sicario", 2015, "/lz8vNyXeidqqOdJW9ZjnDAMb5Vr.jpg"),
    F("Interstellar", 2014, "/yQvGrMoipbRoddT0ZR8tPoR7NfX.jpg"),
    F("Oppenheimer", 2023, "/8Gxv8gSFCU0XGDykEGv7zR1n2ua.jpg"),
    F("Get Out", 2017, "/tFXcEccSQMf3lfhfXKSU9iRBpa3.jpg"),
    F("La La Land", 2016, "/uDO8zWDhfWwoFdKS4fzkUJt0Rf0.jpg"),
    F("Tenet", 2020, "/aCIFMriQh8rvhxpN1IWGgvH0Tlg.jpg"),
    F("Nope", 2022, "/AcKVlWaNVVVFQwro3nLXqPljcYA.jpg"),
    F("Top Gun: Maverick", 2022, "/n0YuM4f5lvGAP6MAW2kBIzugXnc.jpg"),
    F("Joker", 2019, "/udDclJoHjfjb8Ekgsd4FDteOkCU.jpg"),
    F("Barbie", 2023, "/iuFNMS8U5cb6xfzi51Dbkovj7vM.jpg"),
    F("Avatar", 2009, "/gKY6q7SjCkAU6FqvqWybDYgUKIF.jpg"),
    F("Drive", 2011, "/602vevIURmpDfzbnv5Ubi6wIkQm.jpg"),
    F("Spirited Away", 2001, "/39wmItIWsg5sZMyRUHLkWBcuVCM.jpg"),
    F("The Truman Show", 1998, "/vuza0WqY239yBXOadKlGwJsZJFE.jpg"),
    F("The Godfather", 1972, "/3bhkrj58Vtu7enYsRolD1fZdja1.jpg"),
    F("Pulp Fiction", 1994, "/d5iIlFn5s0ImszYzBPb8JPIfbXD.jpg"),
    F("Goodfellas", 1990, "/aKuFiU82s5ISJpGZp7YkIr3kCUd.jpg"),
    F("Se7en", 1995, "/6yoghtyTpznpBik8EngEmJskVUO.jpg"),
    F("Alien", 1979, "/vfrQk5IPloGg1v9Rzbh2Eg3VGyM.jpg"),
    F("City of God", 2002, "/k7eYdWvhYQyRQoU2TB2A2Xu2TfD.jpg"),
    F("Oldboy", 2003, "/pWDtjs568ZfOTMbURQBYuT4Qxka.jpg"),
    F("Memento", 2000, "/yuNs09hvpHVU1cBTCAk9zxsL2oW.jpg"),
    F("Blade Runner", 1982, "/63N9uy8nd9j7Eog2axPQ8lbr3Wj.jpg"),
    F("Taxi Driver", 1976, "/ekstpH614fwDX8DUln1a2Opz0N8.jpg"),
    F("Mulholland Drive", 2001, "/tVxGt7uffLVhIIcwuldXOMpFBPX.jpg"),
    F("Eternal Sunshine of the Spotless Mind", 2004, "/5MwkWH9tYHv3mV9OdYTMR5qreIz.jpg"),
    F("Moonlight", 2016, "/4911T5FbJ9eD2Faz5Z8cT3SUhU3.jpg"),
    F("The Prestige", 2006, "/tRNlZbgNCNOpLpbPEz5L8G8A0JN.jpg"),
]

# A seed placement is provisional until comparisons pull the film out of its group
# (seeding.py), so every member of the seed band carries the settling mark.
SEED_SETTLING = True


def production_css() -> str:
    """styles.css as shipped, with two prototype-only edits: the font inlined, and the
    dark token block made switchable by [data-theme] as well as by the OS."""
    css = STYLES.read_text()

    font = base64.b64encode(FONT.read_bytes()).decode("ascii")
    font_url = 'url("/fonts/Geist-Variable.woff2")'
    assert css.count(font_url) == 1, "expected one Geist @font-face url"
    css = css.replace(font_url, f'url("data:font/woff2;base64,{font}")')

    opener = "@media (prefers-color-scheme: dark) {\n  :root {\n"
    assert css.count(opener) == 1, "expected one dark token block"
    start = css.index(opener)
    body_start = start + len(opener)
    body_end = css.index("\n  }\n}\n", body_start)
    tokens = css[body_start:body_end]
    switchable = (
        '@media (prefers-color-scheme: dark) {\n  :root:not([data-theme="light"]) {\n'
        + tokens
        + "\n  }\n}\n\n"
        + '/* Prototype only: the bar forces a theme through [data-theme]. */\n:root[data-theme="dark"] {\n'
        + tokens.replace("\n    ", "\n  ")
        + "\n}\n"
    )
    css = css[:start] + switchable + css[body_end + len("\n  }\n}\n") :]

    phone = "@media (max-width: 640px) {"
    assert css.count(phone) == 1, "expected one phone media query"
    return css.replace(
        phone,
        "/* Prototype only: the phone rules follow the frame's width, not the screen's. */\n"
        "@container proto (max-width: 640px) {",
    )


def poster(film: Film, embedded: dict[str, dict] | None) -> str:
    if embedded is not None:
        src = embedded[film.title]["data"]
    else:
        src = IMAGE_BASE + film.path
    return f'<img class="poster" src="{src}" alt="" loading="lazy">'


def cell(film: Film, position: int, tie: tuple[bool, bool] | None, embedded: dict | None) -> str:
    """One wall cell, class for class the markup of Rated.tsx's WallCell and OrderedFilm."""
    attrs = ' data-tie="true"' if tie else ""
    if tie and tie[0]:
        attrs += ' data-tie-start="true"'
    if tie and tie[1]:
        attrs += ' data-tie-end="true"'
    joint = (
        '<span class="visually-hidden">Joint </span><span aria-hidden="true">=</span>'
        if tie
        else ""
    )
    marks = ""
    if film.anchor:
        marks += '<span class="anchor-badge" title="The canonical 5.0 exemplar">Anchor</span>'
    if film.provisional:
        marks += (
            '<span class="provisional-mark" title="Still settling: fewer comparisons than usual">'
            "settling</span>"
        )
    if film.flagged:
        marks += '<span class="chip chip-flagged">Needs attention</span>'
    title = html.escape(film.title)
    return (
        f'<li class="ordering-slot"{attrs}>'
        f'<span class="ordering-rank">{joint}{position}</span>'
        '<div class="ordering-film">'
        f'<a class="poster-link" href="#" tabindex="-1" aria-hidden="true">{poster(film, embedded)}</a>'
        '<div class="ordering-film-body">'
        f'<a class="film-title" href="#">{title}</a>'
        f'<span class="film-year muted">{film.year}</span>'
        f"{marks}</div></div></li>"
    )


def wall(slots: list[list[Film]], first_position: int, embedded: dict | None) -> str:
    cells = []
    position = first_position
    for slot in slots:
        for index, film in enumerate(slot):
            tie = (index == 0, index == len(slot) - 1) if len(slot) > 1 else None
            cells.append(cell(film, position, tie, embedded))
        position += len(slot)
    return '<ol class="ordering">' + "".join(cells) + "</ol>"


def band(value: str, stars: str, anchor: str | None, slots: list[list[Film]], first: int, embedded) -> str:
    control = (
        f'<button type="button" class="link-button">Anchor: {html.escape(anchor)}</button>'
        if anchor
        else '<button type="button" class="link-button">Set this band\'s anchor</button>'
    )
    return (
        f'<section class="band-group" id="band-{value.replace(".", "-")}" aria-label="{value} stars">'
        '<header class="band-header"><h3><span class="band">'
        f'<span class="band-stars" aria-hidden="true">{stars}</span>'
        f'<span class="band-value">{value}<span class="visually-hidden"> stars</span></span>'
        f"</span></h3>{control}</header>"
        + wall(slots, first, embedded)
        + "</section>"
    )


def select(label: str, options: list[str]) -> str:
    opts = "".join(f"<option>{html.escape(o)}</option>" for o in options)
    return f'<label class="field"><span>{label}</span><select>{opts}</select></label>'


def page_body(embedded: dict | None) -> str:
    seed_slot = [
        Film(f.title, f.year, f.path, provisional=SEED_SETTLING) for f in SEED
    ]
    crafted_count = sum(len(slot) for slot in CRAFTED)
    return (
        '<div class="proto-viewport"><div class="app">'
        '<nav class="nav" aria-label="Main">'
        '<a class="wordmark" href="#">Anchor</a>'
        '<a href="#">Watchlist</a><a href="#">Discovery</a>'
        '<a class="active" aria-current="page" href="#">Rated</a>'
        '<a href="#">Search</a><a href="#">Profile</a>'
        "</nav>"
        '<main class="main">'
        "<h1>Rated</h1>"
        '<div class="rated-controls"><div class="filters">'
        + select("Sort", ["Your ordering", "Recently rated", "Recently watched", "Title", "Release year"])
        + select("From", ["Best", "5.0", "4.5", "4.0"])
        + select("To", ["Worst", "1.0", "0.5"])
        + select("Genre", ["Any", "Crime", "Drama", "Science Fiction"])
        + select("Decade", ["Any", "2020s", "2010s", "2000s", "1990s"])
        + '<label class="field field-check"><input type="checkbox"><span>Needs attention</span></label>'
        "</div>"
        '<nav class="jump-to-band" aria-label="Jump to band"><span class="muted">Jump to</span>'
        '<a href="#band-5-0">5.0</a><a href="#band-4-5">4.5</a></nav></div>'
        '<section class="section" aria-labelledby="ordering-heading">'
        '<h2 id="ordering-heading">Your ordering</h2>'
        + band("5.0", "★★★★★", "Parasite", CRAFTED, 1, embedded)
        + band("4.5", "★★★★½", None, [seed_slot], crafted_count + 1, embedded)
        + "</section>"
        '<section class="section" aria-labelledby="rate-later-heading">'
        '<h2 id="rate-later-heading">Rate later</h2>'
        '<div class="empty"><p class="muted">Nothing waiting to be rated.</p></div>'
        "</section></main></div></div>"
    )


BAR_CSS = """
/* Prototype only: the switch bar and the phone frame. Deliberately not the design under
   review - a dark pill in the system font, the same in both themes. */
.proto-bar { position: fixed; left: 50%; bottom: 14px; transform: translateX(-50%); z-index: 1000;
  display: flex; flex-wrap: wrap; justify-content: center; align-items: center; gap: 6px 10px;
  max-width: calc(100vw - 24px); padding: 6px 10px; border-radius: 20px; background: #1b1b1f; color: #e9e9ec;
  font: 12px/1 -apple-system, system-ui, sans-serif; box-shadow: 0 8px 30px rgb(0 0 0 / 35%); }
.proto-bar .seg { display: flex; gap: 2px; padding: 2px; border-radius: 999px; background: #2a2a30; }
.proto-bar .seg[hidden] { display: none; }
.proto-bar button { all: unset; cursor: pointer; padding: 6px 10px; border-radius: 999px; color: #c7c7cc; white-space: nowrap; }
.proto-bar button:hover { color: #fff; }
.proto-bar button[aria-pressed="true"] { background: #f2f2f5; color: #111; }
.proto-bar button:focus-visible { outline: 2px solid #f0b94a; outline-offset: 1px; }
.proto-bar .lbl { opacity: 0.55; }
.proto-bar .lbl[hidden] { display: none; }
.proto-bar .variant { display: flex; align-items: center; gap: 2px; }
.proto-bar .variant .name { min-width: 13.5rem; text-align: center; font-weight: 600; color: #fff; }
.proto-bar .variant button { font-size: 14px; padding: 4px 8px; }
/* The wrapper the phone rules measure. At desktop it is as wide as the page; at phone
   width it is a 390px scrolling frame, and the tab bar sticks to the frame's bottom
   rather than the screen's. */
.proto-viewport { container: proto / inline-size; }
html[data-width="phone"] .proto-viewport { width: 390px; height: calc(100dvh - 60px); margin: 24px auto 0;
  overflow: auto; border: 1px solid rgb(128 128 128 / 40%); border-radius: 28px; background: var(--bg);
  box-shadow: 0 20px 60px rgb(0 0 0 / 25%); }
html[data-width="phone"] .nav { position: sticky; bottom: 0; }
@media (max-width: 700px) {
  .proto-bar { top: 8px; bottom: auto; font-size: 11px; gap: 5px 8px; padding: 5px 8px; }
  .proto-bar .variant .name { min-width: 0; }
  .proto-bar .seg-width, .proto-bar .lbl-width { display: none; }
}
"""

BAR_HTML = """
<div class="proto-bar" aria-label="Prototype controls">
  <span class="variant"><button type="button" data-step="-1" aria-label="Previous treatment">&#8249;</button><span class="name"></span><button type="button" data-step="1" aria-label="Next treatment">&#8250;</button></span>
  <span class="lbl lbl-edge">edge</span><span class="seg seg-edge"><button type="button" data-k="edge" data-v="clip">Clip</button><button type="button" data-k="edge" data-v="pad">Pad wall</button><button type="button" data-k="edge" data-v="overhang">Overhang</button></span>
  <span class="lbl lbl-tone">tone</span><span class="seg seg-tone"><button type="button" data-k="tone" data-v="surface">Surface</button><button type="button" data-k="tone" data-v="wash">Wash</button><button type="button" data-k="tone" data-v="lined">Lined</button></span>
  <span class="lbl lbl-place">rule</span><span class="seg seg-place"><button type="button" data-k="place" data-v="below">Below</button><button type="button" data-k="place" data-v="above">Above</button></span>
  <span class="lbl lbl-stamp">stamp</span><span class="seg seg-stamp"><button type="button" data-k="stamp" data-v="ringed">Ringed</button><button type="button" data-k="stamp" data-v="inverted">Inverted</button></span>
  <span class="lbl">theme</span><span class="seg"><button type="button" data-k="theme" data-v="light">Light</button><button type="button" data-k="theme" data-v="dark">Dark</button><button type="button" data-k="theme" data-v="auto">Auto</button></span>
  <span class="lbl lbl-width">width</span><span class="seg seg-width"><button type="button" data-k="width" data-v="desktop">Desktop</button><button type="button" data-k="width" data-v="phone">Phone</button></span>
</div>
"""

BAR_JS = r"""
(function () {
  var root = document.documentElement;
  var VARIANTS = [
    { k: "A", name: "A. The plate, padded" },
    { k: "B", name: "B. A rule, not a fill" },
    { k: "C", name: "C. The stamp alone" },
    { k: "D", name: "D. The caption band" },
    { k: "today", name: "Today's plate, for reference" }
  ];
  var KEYS = ["variant", "edge", "tone", "place", "stamp", "theme", "width"];
  var state = { variant: "A", edge: "pad", tone: "surface", place: "below", stamp: "ringed",
                theme: root.getAttribute("data-theme") || "auto", width: "desktop" };
  var params = new URLSearchParams(location.search);
  KEYS.forEach(function (k) { if (params.get(k)) state[k] = params.get(k); });
  var bar = document.querySelector(".proto-bar");
  // On a real phone the frame is the screen, so the width switch has nothing to add.
  var narrow = function () { return window.innerWidth <= 700; };

  function apply() {
    if (narrow()) state.width = "desktop";
    KEYS.forEach(function (k) {
      if ((k === "theme" && state.theme === "auto") || (k === "width" && state.width === "desktop")) root.removeAttribute("data-" + k);
      else root.setAttribute("data-" + k, state[k]);
    });
    var v = VARIANTS.filter(function (x) { return x.k === state.variant; })[0] || VARIANTS[0];
    bar.querySelector(".variant .name").textContent = v.name;
    var fills = state.variant === "A" || state.variant === "D";
    var edged = fills || state.variant === "B";
    ["edge", "tone", "place", "stamp"].forEach(function (k) {
      var on = k === "edge" ? edged : k === "tone" ? fills : k === "place" ? state.variant === "B" : state.variant === "C";
      bar.querySelector(".seg-" + k).hidden = !on;
      bar.querySelector(".lbl-" + k).hidden = !on;
    });
    bar.querySelectorAll("button[data-k]").forEach(function (b) {
      b.setAttribute("aria-pressed", String(state[b.dataset.k] === b.dataset.v));
    });
    // Keep the state in the URL so a look is shareable - but only when the page owns its
    // URL. Framed (the artifact host, the phone iframe) the query string is not ours.
    if (window.top === window) {
      try {
        var q = new URLSearchParams();
        KEYS.forEach(function (k) { q.set(k, state[k]); });
        history.replaceState(null, "", "?" + q.toString());
      } catch (e) { /* a URL that cannot be rewritten */ }
    }
  }

  function step(delta) {
    var i = VARIANTS.findIndex(function (x) { return x.k === state.variant; });
    state.variant = VARIANTS[(i + delta + VARIANTS.length) % VARIANTS.length].k;
    apply();
  }

  bar.querySelectorAll("button[data-k]").forEach(function (b) {
    b.addEventListener("click", function () { state[b.dataset.k] = b.dataset.v; apply(); });
  });
  bar.querySelectorAll("button[data-step]").forEach(function (b) {
    b.addEventListener("click", function () { step(Number(b.dataset.step)); });
  });
  document.addEventListener("keydown", function (e) {
    var t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
    if (e.key === "ArrowLeft") step(-1);
    if (e.key === "ArrowRight") step(1);
  });
  window.addEventListener("resize", function () { if (narrow() && state.width === "phone") apply(); });
  apply();
})();
"""


def build(embedded: dict | None, artifact: bool) -> str:
    head = (
        "<title>Tie Mark Treatments</title>\n"
        "<style>\n" + production_css() + "\n</style>\n"
        "<style>\n" + TREATMENTS.read_text() + "\n</style>\n"
        "<style>" + BAR_CSS + "</style>\n"
    )
    body = page_body(embedded) + BAR_HTML + "<script>" + BAR_JS + "</script>\n"
    if artifact:
        return head + body
    return (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        + head
        + "</head>\n<body>\n"
        + body
        + "</body>\n</html>\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--embed", type=Path, help="JSON map of title -> {data: <data URI>} to inline posters")
    parser.add_argument("--artifact", action="store_true", help="emit the fragment the Artifact tool wraps")
    parser.add_argument("--out", type=Path, default=HERE / "tie-mark.html")
    args = parser.parse_args()

    embedded = None
    if args.embed:
        embedded = json.loads(args.embed.read_text())
        titles = {f.title for slot in CRAFTED for f in slot} | {f.title for f in SEED}
        missing = sorted(titles - set(embedded))
        assert not missing, f"no embedded poster for: {missing}"

    out = build(embedded, args.artifact)
    args.out.write_text(out)
    print(f"wrote {args.out} ({len(out.encode()) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
