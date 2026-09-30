import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist/", "dist-signed/", "node_modules/", "web-ext-artifacts/"] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    languageOptions: {
      globals: { ...globals.browser, ...globals.node, browser: "readonly" },
    },
  },
);
