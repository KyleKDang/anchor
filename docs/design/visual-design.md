# Visual design

Fixes the visual direction and the rules that hold it together, so every screen built from here looks like it belongs to the same product.
Decided at [Visual design foundation (#50)](https://github.com/KyleKDang/anchor/issues/50) by prototyping three directions as real pages over the app's own markup and picking one; the ticket thread carries the references the owner gave, the three directions, and the reasoning behind the choice.
Vocabulary follows [CONTEXT.md](../../CONTEXT.md); this doc fixes the look, while [screens-and-flows.md](screens-and-flows.md) still fixes behavior, content, and information architecture.

The whole direction lives in `frontend/src/styles.css`: a token layer, then the primitives, then the screens.
Nothing below is decoration for its own sake - each rule exists because Anchor is a rating instrument, and the instrument's job is to make a judgment easy to read.

## The thesis

**A precise tool for a film mind.**
Cool graphite neutrals, hairlines instead of boxes, tabular figures, dense but never cramped.
The reference points the owner named were Linear for precision and Letterboxd for poster-heaviness; what was ruled out was the generic SaaS starter look, anything resembling a streaming service, and a Letterboxd clone.

**Ratings lead; posters make them recognisable.**
A poster is recognised faster than a title, so the ordering is a wall of posters and every list row carries one.
But the poster is never the subject: the band, the rank, and the anchor badge are what the screen is actually saying.

## The one-amber rule

There is exactly one accent, and it means *a rating*.

Amber is used by the stars, the anchor badge, the mark's ring, the nudge's rule, and the focus ring, and by nothing else.
The one exception is the landing page's eyebrow, the word naming each destination on the tour, decided with the page itself on [#113](https://github.com/KyleKDang/anchor/issues/113): the page has no ratings to spend the accent on outside its specimen, and the eyebrow is its one mark of emphasis.
It is the only screen a visitor sees before signing in, so the rule holds everywhere the product is actually used.
Every action on every screen is monochrome: the primary button is inverted ink, the secondary is a hairline outline, the quiet one is an underlined link.
The consequence is that no button can ever compete with a star for attention, which is the point - the ordering is the product, and the verbs are how you feed it.

Red is reserved for destruction (delete, retire) and for errors, and it is never a background.

## Type

Geist, self-hosted, with `system-ui` behind it, at a seven-step scale from `--text-xs` to `--text-2xl`.
One variable file covers the whole weight axis, and it is served from Anchor's own origin rather than a font CDN, so a render-blocking asset never depends on a third party.
It is preloaded, since the stylesheet would otherwise not ask for it until it had been parsed.
No italic face is shipped: one label is set in italic and the browser's synthesised oblique is enough for it.
The font is SIL OFL and its licence travels beside it in `frontend/public/fonts`.
Weights are 400, 500 for controls and marks, and 600 for headings and figures; there is no bold body text.

Anything whose digits line up gets `font-variant-numeric: tabular-nums`: ranks, band values, years, readiness figures.
A rank column that jitters as the digits change is the exact opposite of a precise instrument.

Band values are the number first and the stars second (`4.5 ★★★★½`), because counting five stars at a glance is not something anyone should have to do; the stars are the shape of the value, not the value.

## Structure

**Hairlines over cards.**
A rule under a heading, a rule between rows.
Cards are for the few things that are genuinely a discrete object: the rating panel on a film page, a readiness stage, the auth card.

**Two elevations and no more.**
`--shadow-1` lifts a resting surface a hair off the background; `--shadow-2` is for something floating over the page (a dialog, a poster under the pointer, and the landing page's screen frames, which are pictures of the app held above it rather than surfaces of it).

**Radii climb with the size of the thing.**
4px on marks and thumbnails, 6px on controls, 10px on cards and boxes, 14px on the largest panels, full round on pills.

## The wall-versus-rows rule

The ordering is always a **wall**: posters in a grid, the rank within the band stamped on each one, grouped under a sticky band header.
Every film has its own cell and its own rank; nothing is ever drawn around a group of films, because the column count follows the viewport and any mark spanning cells would be cut wherever it meets the end of a row.
The wall stays one grid.
In edit mode the wall keeps its grid: the dragged poster lifts on `--shadow-2`, the gap it would fill opens in the row under the pointer, the anchor toggle sits on each poster where the badge sits, and the band headers stay sticky so a cross-band drag always has its target row's label in view.
A film just landed by the picker is ringed in amber until the owner moves it or leaves, since it is the one film on the wall whose place is a rating and not yet a judgment.
At three hundred films the wall is *shorter* than the same films as rows - about fifty rows of six posters against three hundred rows - so the wall scales better than the list it replaces, and there is deliberately no toggle between them: two layouts would be two layouts to keep correct in every state forever, and choosing between them is not a decision the owner should have to make.
Sorted any way but by position the wall goes flat and each poster carries its own band underneath, because a band header over a sequence that is not in band order would be a heading over nothing.

Anything whose items carry inline verbs is a **row**: the rate-later queue, search results, the backlog.
A row is poster, body, actions, and its actions stack rather than lining up, because three controls in a line push the row past the screen edge on a phone.

## Layout and the phone

One column, `64rem` at its widest, with the destinations on a sticky rail at the top.

At 640px and under the rail becomes a bottom tab bar under the thumb and the wordmark gives up its space to the five destinations that go somewhere.
The wall goes to three posters across; the film page puts the poster beside the title and gives everything below it the full width.
There is one breakpoint, deliberately: a second one is a second layout to keep true.

## Theme

Light and dark, both defined as tokens, switched by `prefers-color-scheme` alone.
Anchor has no theme switch, so there is no `[data-theme]` override - a selector nothing can activate is a maintenance lie.
Adding a switch later means adding that guard beside the media query and nothing else.

## The accessibility floor

- **Text meets WCAG AA (4.5:1)** on every surface it sits on, in both themes, including the tinted badges and flags where the tint is composited over the surface beneath it.
- **`--border-control` is the boundary of anything interactive** and is held at the 3:1 non-text floor, separately from the decorative `--border-strong`, because a control identified only by its outline needs that outline to be visible.
- **Focus is always visible**: a 2px amber ring, offset, on every focusable element.
  A field draws the ring on itself rather than on the control inside it, since a border quietly changing shade is not an indicator anyone can see.
- **Motion is small and optional**: 120ms for state, 180ms for movement, and every transition is dropped under `prefers-reduced-motion`.

The contrast floor is arithmetic, not judgment, so it is checked by computing every text-tone-on-surface pair rather than by eye.

## Primitives

The shared vocabulary, all defined in the token layer: `button` (primary, `secondary`, `danger`, and `link-button`), `field`, `chip`, `card`, `wordmark`, `film-row`, the badges (`anchor-badge`, `state-flag`), `band`, `empty`, `nudge`, `notice`, `scrim` and `dialog`, `spoiler`, `poster`, `neighbours`, and `actions`.

A screen composes these and adds only what is genuinely its own.
When a screen wants something a primitive nearly does, the primitive grows; a screen that grows its own copy is how 936 lines of ad-hoc CSS happened the first time.

`dialog` was defined ahead of its first use, so that whatever needed it first would inherit the direction instead of improvising.
That first use is the demo account's read-only intercept ([#42](https://github.com/KyleKDang/anchor/issues/42)), which brought `scrim` with it - the cover behind a dialog, and the only thing in Anchor that ever covers the screen.
It is allowed to interrupt because it is answering a press the visitor just made, which is the one shape of interruption [ADR 0011](../adr/0011-no-nagging-surfacing-policy.md) leaves open.
The scrim is a literal dark rather than a mix of `--ink`, which inverts between themes: a scrim that went pale in the dark theme would lift the page towards the dialog instead of dropping it away.

### The mark

Anchor's mark is an anchor reduced to ring, stock, shank, and flukes, chosen on [#142](https://github.com/KyleKDang/anchor/issues/142) because it is the one sketch that reads at 16px and says the name without a word.
It is a 24-unit drawing: a circle of radius 3.5 at (12, 5.5), and the path `M12 9v12M8 11.5h8M4 14a8 8 0 0 0 16 0` stroked at 2 with round caps.
`Mark.tsx` and `public/favicon.svg` are the two copies of it, and each names the other.

It is two colors and nothing else: the ring is `--star` and the lines are `currentColor`.
The ring is the dot the wordmark used to carry, so the one-amber inventory did not grow.
Because the lines take the wordmark's ink, the mark inverts with the theme and needs no dark rule of its own.
There is no gradient, no third color, and no shape behind it.

The mark sits inside every `wordmark`, hidden from assistive tech, so the wordmark's name stays the word "Anchor".
It is sized in `em` on the primitive (`1em`), so a wordmark set smaller gets a smaller mark without a rule of its own.
The wordmark aligns on the baseline, which puts the flukes on the word's baseline and lifts the ring just above its cap height; centered, the flukes hung two to four pixels below the word at every size from `1em` to `1.2em`.
The one exception is the landing frame narrow enough to drop the word, where the wordmark's font size goes to zero and the mark is pinned to the size it had beside the text.

As a favicon, the SVG carries literal colors and its own `prefers-color-scheme` query, since a tab strip has no tokens to read.
The PNG fallbacks (32px for Safari, 180px for the iOS home screen) are the one place the mark gets a shape behind it: an opaque tile of the light ground `#f5f5f6`.
A raster icon cannot follow the theme, and a transparent one vanishes on a dark tab strip.

## What is not styled yet

The prototypes rendered the densest version of every screen so the direction could be judged against it, including surfaces that did not exist then: watch and judgment history, log-a-rewatch, and a rated film's position among its neighbours.
Those arrived with [#29](https://github.com/KyleKDang/anchor/issues/29) and its siblings; the band picker, the wall's edit mode, and the criteria session arrive with the direct-ordering tickets ([ADR 0013](../adr/0013-the-ordering-is-edited-by-hand.md)), and they use these primitives when they do rather than adding rules beside them.
