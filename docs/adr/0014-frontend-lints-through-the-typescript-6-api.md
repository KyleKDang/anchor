# The frontend lints through the TypeScript 6 API while it compiles with TypeScript 7

Decided 2026-09-08, while giving the frontend its tooling floor (#106).

The frontend compiles with TypeScript 7, the native Go port, which ships `tsc` and no JavaScript compiler API.
ESLint reaches TypeScript through `typescript-eslint`, whose parser imports the `typescript` package and needs that API; against TypeScript 7.0 it refuses to load, and its maintainers have said there is nothing to support until a stable API exists, which is promised for 7.1 as a new and different interface.
So the two tools the frontend needs cannot both resolve `typescript` to the same package.

We decided to run the two side by side the way the TypeScript team documents it.

- `typescript` in `frontend/package.json` is an npm alias for `@typescript/typescript6`, which re-exports the TypeScript 6 API; `typescript-eslint` reads it, and its peer range is satisfied without any override.
- `@typescript/native` is an npm alias for `typescript@7`, and it is the package that installs `tsc`, so `npm run build` and `npm run typecheck` compile with TypeScript 7 exactly as before.
- Lint parses at the 6.0 language level, which is the level 7.0 is a port of, so nothing the build accepts is foreign to the linter today.

Chosen because it keeps ESLint and `eslint-plugin-react-hooks`, whose hook rules are the React team's own implementation, on a codebase where every screen keys effects on loaders and ids.
The cost is a second TypeScript on disk and a `typescript` line that does not mean what it looks like it means, which this record exists to explain.
The exit is fixed: when `typescript-eslint` supports the TypeScript the build runs on, `typescript` goes back to the real package, `@typescript/native` is deleted, and this ADR is superseded.

## Consequences

- Bumping TypeScript means bumping `@typescript/native`; the `typescript` alias stays on the last 6.x until the exit above.
- A language feature added after 6.0 would compile and fail to lint; that is loud, in CI, and the signal to check whether the exit has arrived.
- Editors are unaffected: the compat package ships no `tsserver`, so a workspace-TypeScript setting falls through to the editor's own.

## Considered options

- Wait for `typescript-eslint` and ship Prettier alone: rejected; the lint rules are most of the value of a tooling floor, and the wait has no date.
- oxlint with Prettier: no TypeScript dependency and the parser Vite already builds with, but its `exhaustive-deps` is a port of the React team's rule rather than the rule itself, and the ticket chose ESLint; rejected for now, and worth another look if the alias outlives its welcome.
- Biome for both formatting and linting: one tool, but its hook-dependency rule diverges from React's and its formatter is only Prettier-compatible; rejected as two departures from reference tools to save one config file.
- ESLint on a Babel parser: keeps the hook rules without the alias but drops the whole `typescript-eslint` rule set, including the `no-explicit-any` that `tsc` cannot catch; rejected.
