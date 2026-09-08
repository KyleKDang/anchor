// typescript-eslint parses through the TypeScript 6 API that `typescript` aliases to in
// package.json; `tsc` itself is TypeScript 7. See docs/adr/0014 for why and for the exit.
import js from "@eslint/js";
import prettier from "eslint-config-prettier";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/", "test-results/", "playwright-report/"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  prettier,
  {
    languageOptions: { globals: globals.browser },
    rules: {
      // Recommended ships this as a warning; a warning never fails CI.
      "react-hooks/exhaustive-deps": "error",
      // Every screen loads by calling a useCallback loader from an effect, and this rule
      // flags that shape on all of them. Its fix is to restructure each screen's loading,
      // which is a rewrite, not a lint.
      "react-hooks/set-state-in-effect": "off",
    },
  },
  {
    files: ["e2e/**", "*.config.{js,ts}"],
    languageOptions: { globals: globals.node },
  },
);
