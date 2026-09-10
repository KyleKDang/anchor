/**
 * The films the landing page shows, checked in rather than read from the API.
 *
 * The page has to paint for a visitor with no session, so there is nothing to read, and
 * it must not lean on the demo account, which does not exist until #108. The films are
 * real and their poster paths are TMDB's own, hotlinked through the same `Poster`
 * component every other surface uses (ADR 0003); the bands, the ordering, the anchors,
 * the pins and the pitches are invented for the page, in the shapes the real screens
 * take, because a landing page showing one stranger's actual taste would be showing an
 * account rather than a product.
 *
 * Nothing here is a link. The specimen and the three framed screens are pictures of the
 * app, and a visitor who taps a poster has no film page to arrive at.
 */

export interface LandingFilm {
  title: string;
  year: number;
  /** TMDB's path, not a URL: `posterUrl` picks the file size per surface. */
  posterPath: string;
  anchor?: boolean;
}

export interface LandingBand {
  band: number;
  anchors: number;
  films: LandingFilm[];
}

/** The hero's specimen: one band of the wall, beside the statement. */
export const specimen: LandingBand = {
  band: 5,
  anchors: 3,
  films: [
    {
      title: "In the Mood for Love",
      year: 2000,
      posterPath: "/iYypPT4bhqXfq1b6EnmxvRt6b2Y.jpg",
      anchor: true,
    },
    { title: "There Will Be Blood", year: 2007, posterPath: "/fa0RDkAlCec0STeMNAhPaF89q6U.jpg" },
    {
      title: "The Godfather",
      year: 1972,
      posterPath: "/3bhkrj58Vtu7enYsRolD1fZdja1.jpg",
      anchor: true,
    },
    { title: "Mulholland Drive", year: 2001, posterPath: "/x7A59t6ySylr1L7aubOQEA480vM.jpg" },
    {
      title: "Spirited Away",
      year: 2001,
      posterPath: "/39wmItIWsg5sZMyRUHLkWBcuVCM.jpg",
      anchor: true,
    },
    { title: "Parasite", year: 2019, posterPath: "/7IiTTgloJzvGI1TAYymCfbfl3vT.jpg" },
  ],
};

/** The Rated frame: two band rows of the wall. */
export const wall: LandingBand[] = [
  {
    band: 4.5,
    anchors: 2,
    films: [
      {
        title: "Portrait of a Lady on Fire",
        year: 2019,
        posterPath: "/rUDuOKpkKBHxx41BScqKej72iT3.jpg",
        anchor: true,
      },
      {
        title: "No Country for Old Men",
        year: 2007,
        posterPath: "/6d5XOczc226jECq0LIX0siKtgHR.jpg",
      },
      { title: "Blade Runner 2049", year: 2017, posterPath: "/gajva2L0rPYkEWjzgFlBXCAVBE5.jpg" },
      { title: "Whiplash", year: 2014, posterPath: "/7fn624j5lj3xTme2SgiLCeuedmO.jpg" },
      {
        title: "The Social Network",
        year: 2010,
        posterPath: "/n0ybibhJtQ5icDqTp8eRytcIHJx.jpg",
        anchor: true,
      },
      { title: "Aftersun", year: 2022, posterPath: "/evKz85EKouVbIr51zy5fOtpNRPg.jpg" },
    ],
  },
  {
    band: 4,
    anchors: 1,
    films: [
      { title: "Arrival", year: 2016, posterPath: "/pEzNVQfdzYDzVK0XqxERIw2x2se.jpg" },
      { title: "Her", year: 2013, posterPath: "/eCOtqtfvn7mxGl6nfmq4b1exJRc.jpg" },
      {
        title: "The Grand Budapest Hotel",
        year: 2014,
        posterPath: "/eWdyYQreja6JGCzqHWXpWHDrrPo.jpg",
        anchor: true,
      },
      { title: "Before Sunrise", year: 1995, posterPath: "/kf1Jb1c2JAOqjuzA3H4oDM263uB.jpg" },
      { title: "Mad Max: Fury Road", year: 2015, posterPath: "/ulcAi4dKpAjHwYGS08vNyx9H6I9.jpg" },
      { title: "Past Lives", year: 2023, posterPath: "/k3waqVXSnvCZWfJYNtdamTgTtTA.jpg" },
    ],
  },
];

export interface LandingQueued {
  film: LandingFilm;
  pinned?: boolean;
}

/** The Watchlist frame: the ranked tier, with the pin held at the top. */
export const watchlist: LandingQueued[] = [
  {
    film: { title: "Perfect Days", year: 2023, posterPath: "/tvUHVSTJV9ITON3oyHaWp7oaAc8.jpg" },
    pinned: true,
  },
  {
    film: {
      title: "The Zone of Interest",
      year: 2023,
      posterPath: "/hUu9zyZmDd8VZegKi1iK1Vk0RYS.jpg",
    },
  },
  {
    film: {
      title: "Anatomy of a Fall",
      year: 2023,
      posterPath: "/1ho0d4LNZw3Y0voeKmSvPSgJOJ2.jpg",
    },
  },
  {
    film: {
      title: "All of Us Strangers",
      year: 2023,
      posterPath: "/aviJMFZSnnCAsCVyJGaPNx4Ef3i.jpg",
    },
  },
  {
    film: { title: "The Holdovers", year: 2023, posterPath: "/VHSzNBTwxV8vh7wylo7O9CLdac.jpg" },
  },
];

export interface LandingSuggestion {
  film: LandingFilm;
  /** The one line the engine says out loud about an unwatched film; never a score (ADR 0005). */
  pitch: string;
  fresh?: boolean;
}

/** The Discovery frame: the shelf, each film with the sentence naming what it drew on. */
export const suggestions: LandingSuggestion[] = [
  {
    film: {
      title: "The Worst Person in the World",
      year: 2021,
      posterPath: "/1NxGNQchGBTHXJ6RShLY1IlZqWn.jpg",
    },
    pitch:
      "Because you loved Past Lives and Aftersun - the same patience with someone who hasn't decided yet.",
    fresh: true,
  },
  {
    film: { title: "Dune: Part Two", year: 2024, posterPath: "/6izwz7rsy95ARzTR3poZ8H6c5pp.jpg" },
    pitch:
      "Because Blade Runner 2049 sits high on your wall - Villeneuve at the same scale, with more happening.",
  },
  {
    film: { title: "Paris, Texas", year: 1984, posterPath: "/sP27Qm4THyRZyHjHYMfIDtJP6YE.jpg" },
    pitch:
      "Because you anchored In the Mood for Love - another film about the distance between two people in one room.",
  },
];
