import mdx from "@mdx-js/rollup";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// mdx() must run before @vitejs/plugin-react so the JSX it emits from
// `.mdx` files (content/architecture-story.mdx, AA-31) gets Fast Refresh
// and the automatic JSX runtime like any other component.
export default defineConfig({
  plugins: [{ enforce: "pre", ...mdx({ providerImportSource: "@mdx-js/react" }) }, react()],
});
