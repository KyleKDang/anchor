import { type ReactNode } from "react";
import { Link } from "react-router";

import { Band } from "../../films/Band";
import { Poster } from "../../films/Poster";
import {
  specimen,
  suggestions,
  wall,
  watchlist,
  type LandingBand,
  type LandingFilm,
} from "./films";

/**
 * The front door: what a visitor with no session sees at the root.
 *
 * The one screen that is not a destination inside the frame, so it is composed rather
 * than inherited - but composed out of the app's own primitives, on the visual system
 * visual-design.md fixes. Its structure is the tour direction the owner picked on #113:
 * a statement beside one real band of the wall, then one step per destination with the
 * screen itself in a frame, then the last word and the verbs once more.
 *
 * Sign up is the page's one loud action, in the rail, the hero, and the footer. The
 * hairline secondary beside it holds Log in until #108 puts the demo there.
 *
 * The three framed screens are `aria-hidden` pictures of the app: their verbs are inert
 * text, because a control that does nothing is worse on the one page whose visitor has
 * never seen a live one. Nothing on the page reads the API - there is no session to read
 * it with - so the films come from a checked-in list (`films.ts`).
 */
export function Landing() {
  return (
    <div className="landing">
      <header className="landing-nav">
        <Link className="wordmark" to="/">
          Anchor
        </Link>
        <nav className="landing-nav-actions" aria-label="Account">
          <Link className="landing-login" to="/login">
            Log in
          </Link>
          <Link className="button" to="/signup">
            Sign up
          </Link>
        </nav>
      </header>

      <main>
        <Hero />
        <TourStep
          id="step-wall"
          destination="Rated"
          claim="Everything you've watched, ranked by hand."
          body="Every film you've rated, grouped by star rating and ordered within it, each poster stamped with its place. Rate a new film by comparing it with the ones you're sure of and it lands where it belongs; drag it if you'd put it somewhere else. Nothing moves unless you move it."
          active="Rated"
        >
          <WallFrame />
        </TourStep>
        <TourStep
          id="step-watchlist"
          destination="Watchlist"
          claim="A watchlist that ranks itself."
          body="Anything you pin stays at the top; the rest is ordered by how much you're likely to love it, once Anchor knows your taste. Mark a film watched and you rate it on the spot."
          active="Watchlist"
        >
          <WatchlistFrame />
        </TourStep>
        <TourStep
          id="step-discovery"
          destination="Discovery"
          claim="Recommendations that know your taste."
          body="Anchor learns from your ratings and writes one line about each film it thinks you'd love, naming the films of yours it drew on. Add it to your watchlist, or say it isn't for you and it learns from that too."
          active="Discovery"
        >
          <DiscoveryFrame />
        </TourStep>

        <section className="closing" aria-labelledby="closing-heading">
          <h2 id="closing-heading">Start with the films you're sure of.</h2>
          <p className="muted">
            Rate a handful you're certain about and the wall builds itself from there.
          </p>
          <Verbs />
        </section>
      </main>

      <Footer />
    </div>
  );
}

/** The statement, the verbs, and one real band of the wall beside them. */
function Hero() {
  return (
    <section className="hero" aria-labelledby="hero-heading">
      <div className="hero-copy">
        <h1 id="hero-heading">Rank every film you've ever seen.</h1>
        <p className="lede">
          A tier list of everything you've watched, a watchlist that ranks itself, and
          recommendations that actually know your taste.
        </p>
        <Verbs />
        {/* The one line for the visitor who already rates somewhere else. Framed as the
            import Anchor really does (#30) rather than as a comparison. */}
        <p className="hero-note">
          Already on Letterboxd? Import your ratings and start with your wall already built.
        </p>
      </div>
      <div className="specimen">
        <BandRow row={specimen} posterSize="w342" />
        <p className="specimen-note">
          One row of the wall: six films rated five stars, in their owner's order. Three are
          anchors, the films they're certain of, and the ones a new film gets compared with.
        </p>
      </div>
    </section>
  );
}

/**
 * The page's action row, in the hero and again in the closing.
 *
 * Sign up is the primary everywhere it appears; the secondary beside it is Log in until
 * #108, which takes this slot for the demo and touches nothing else on the page.
 */
function Verbs() {
  return (
    <div className="actions">
      <Link className="button" to="/signup">
        Sign up
      </Link>
      <Link className="button secondary" to="/login">
        Log in
      </Link>
    </div>
  );
}

/**
 * One step of the tour: the destination's own name as the eyebrow, a claim, a paragraph,
 * and the screen it is about in a frame.
 *
 * The eyebrow is the rail's word rather than a number, so the tour reads as the app's
 * five destinations rather than as three marketing steps. It is also the only place on
 * the page besides the stars and the anchor badges that spends the amber.
 */
function TourStep({
  id,
  destination,
  claim,
  body,
  active,
  children,
}: {
  id: string;
  destination: string;
  claim: string;
  body: string;
  /** The tab the frame's miniature rail marks as current. */
  active: string;
  /** The framed screen itself. */
  children: ReactNode;
}) {
  return (
    <section className="tour-step" aria-labelledby={id}>
      <div className="tour-copy">
        <p className="eyebrow">{destination}</p>
        <h2 id={id}>{claim}</h2>
        <p>{body}</p>
      </div>
      <div className="screen" aria-hidden="true">
        <ScreenNav active={active} />
        {children}
      </div>
    </section>
  );
}

/** A miniature of the app's rail across the top of a frame, with its tab marked. */
function ScreenNav({ active }: { active: string }) {
  return (
    <div className="screen-nav">
      <span className="wordmark">Anchor</span>
      {["Watchlist", "Discovery", "Rated", "Search", "Profile"].map((label) => (
        <span key={label} className={label === active ? "active" : undefined}>
          {label}
        </span>
      ))}
    </div>
  );
}

function WallFrame() {
  return (
    <div className="screen-body">
      <h1>Rated</h1>
      {wall.map((row) => (
        <BandRow key={row.band} row={row} posterSize="w154" />
      ))}
    </div>
  );
}

/**
 * One band row of the wall: the half-star header with the band's anchor count, and the
 * films in their order with the rank stamped on each poster.
 *
 * The same shape the Rated screen renders, down to the header and the badges, because
 * the whole claim of the framed screens is that they are the product rather than a
 * drawing of it.
 */
function BandRow({ row, posterSize }: { row: LandingBand; posterSize: "w154" | "w342" }) {
  return (
    <section className="band-group" aria-label={`${row.band.toFixed(1)} stars`}>
      <header className="band-header">
        <h3>
          <Band band={row.band} />
        </h3>
        <span className="muted">
          {row.anchors} {row.anchors === 1 ? "anchor" : "anchors"}
        </span>
      </header>
      <ol className="ordering">
        {row.films.map((film, index) => (
          <li key={film.title} className="ordering-slot">
            <span className="ordering-rank">{index + 1}</span>
            <div className="ordering-film">
              <FilmPoster film={film} size={posterSize} />
              <div className="ordering-film-body">
                <span className="film-title">{film.title}</span>
                <span className="film-year muted">{film.year}</span>
                {film.anchor && <span className="anchor-badge">Anchor</span>}
              </div>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function WatchlistFrame() {
  return (
    <div className="screen-body">
      <h1>Watchlist</h1>
      <section className="section">
        <h2>Up next</h2>
        <p className="muted">In order. Pin anything you want held at the top.</p>
        <ul className="film-list">
          {watchlist.map(({ film, pinned }) => (
            <li key={film.title} className="film-row">
              <FilmPoster film={film} size="w154" />
              <div className="film-row-body">
                <h3 className="film-row-title">{film.title}</h3>
                <p className="film-row-meta">
                  <span className="muted">{film.year}</span>
                  {pinned && <span className="state-flag">Pinned</span>}
                </p>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function DiscoveryFrame() {
  return (
    <div className="screen-body">
      <h1>Discovery</h1>
      <section className="section">
        <h2>Suggestions</h2>
        <ul className="film-list">
          {suggestions.map(({ film, pitch, fresh }) => (
            <li key={film.title} className="film-row suggestion">
              <FilmPoster film={film} size="w154" />
              <div className="film-row-body">
                <h3 className="film-row-title">{film.title}</h3>
                <p className="film-row-meta">
                  <span className="muted">{film.year}</span>
                  {fresh && <span className="state-flag">New</span>}
                </p>
                <p className="pitch">{pitch}</p>
                {/* Inert text, not controls: see the note on the page above. */}
                <p className="row-verbs">
                  <span className="link-button">Add to watchlist</span>
                  <span className="link-button">Not for me</span>
                </p>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

/**
 * A poster in the shape the app gives it, without the link the app wraps it in.
 *
 * The span keeps the box the wall and the rows are laid out against; only the navigation
 * is missing, because a visitor has no film page to reach.
 */
function FilmPoster({ film, size }: { film: LandingFilm; size: "w154" | "w342" }) {
  return (
    <span className="poster-link">
      <Poster title={film.title} path={film.posterPath} size={size} />
    </span>
  );
}

/**
 * The foot: the verbs once more for whoever read to the end, and the attribution TMDB's
 * terms require wherever their art is shown (ADR 0003).
 */
function Footer() {
  return (
    <footer className="landing-footer">
      <div className="landing-footer-inner">
        <div className="footer-brand">
          <span className="wordmark">Anchor</span>
          <nav className="footer-links" aria-label="Account">
            <Link to="/signup">Sign up</Link>
            <Link to="/login">Log in</Link>
          </nav>
        </div>
        <div className="attribution">
          <a href="https://www.themoviedb.org/" target="_blank" rel="noreferrer noopener">
            <img className="tmdb-logo" src="/tmdb.svg" alt="The Movie Database (TMDB)" />
          </a>
          <p className="muted">
            This product uses the TMDB API but is not endorsed, certified, or otherwise approved by
            TMDB.
          </p>
        </div>
      </div>
    </footer>
  );
}
