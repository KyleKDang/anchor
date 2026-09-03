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

Amber is used by the stars, the anchor badge, the wordmark's dot, the nudge's rule, and the focus ring, and by nothing else.
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
A film the dividers cannot place says "Rating pending" rather than showing nothing, because an empty space reads as a bug and this is the design working as intended.

## Structure

**Hairlines over cards.**
A rule under a heading, a rule between rows.
Cards are for the few things that are genuinely a discrete object: the rating panel on a film page, a readiness stage, the auth card.

**Two elevations and no more.**
`--shadow-1` lifts a resting surface a hair off the background; `--shadow-2` is for something floating over the page (a dialog, a poster under the pointer).

**Radii climb with the size of the thing.**
4px on marks and thumbnails, 6px on controls, 10px on cards and boxes, 14px on the largest panels, full round on pills.

## The wall-versus-rows rule

The ordering is always a **wall**: posters in a grid, the rank stamped on each one, grouped under a sticky band header, with films judged equal boxed together in one slot under one rank.
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

The shared vocabulary, all defined in the token layer: `button` (primary, `secondary`, `danger`, and `link-button`), `field`, `chip`, `card`, `film-row`, the badges (`anchor-badge`, `provisional-mark`, `state-flag`), `band`, `empty`, `nudge`, `notice`, `dialog`, `spoiler`, `poster`, `neighbours`, and `actions`.

A screen composes these and adds only what is genuinely its own.
When a screen wants something a primitive nearly does, the primitive grows; a screen that grows its own copy is how 936 lines of ad-hoc CSS happened the first time.

`dialog` is the one primitive defined ahead of its first use: no surface today opens a modal, and inventing one would be the behavior change this foundation is not allowed to make.
It is here so that the first surface that needs it - drift resolution, retiring an anchor - inherits the direction instead of improvising.

## What is not styled yet

The prototypes rendered the densest version of every screen so the direction could be judged against it, including surfaces that do not exist: the needs-attention strip, the drift flag and its resolutions, watch and judgment history, log-a-rewatch, re-place, and a rated film's position among its neighbours.
Those arrive with [#29](https://github.com/KyleKDang/anchor/issues/29) and its siblings, and they use these primitives when they do rather than adding rules beside them.
