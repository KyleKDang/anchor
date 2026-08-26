import type { ComponentType } from "react";

import { Discovery } from "./screens/Discovery";
import { Profile } from "./screens/Profile";
import { Rated } from "./screens/Rated";
import { Search } from "./screens/Search";
import { Watchlist } from "./screens/Watchlist";

export interface Destination {
  path: string;
  label: string;
  screen: ComponentType;
}

/** The five top-level destinations, in navigation order. */
export const destinations: [Destination, ...Destination[]] = [
  { path: "/watchlist", label: "Watchlist", screen: Watchlist },
  { path: "/discovery", label: "Discovery", screen: Discovery },
  { path: "/rated", label: "Rated", screen: Rated },
  { path: "/search", label: "Search", screen: Search },
  { path: "/profile", label: "Profile", screen: Profile },
];
