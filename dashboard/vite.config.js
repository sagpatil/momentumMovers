import { defineConfig } from "vite";

// base is set to the repo name for GitHub Pages project-site hosting
// (https://<user>.github.io/<repo>/). Override with VITE_BASE for a custom domain.
export default defineConfig({
  base: process.env.VITE_BASE || "/momentumMovers/",
  build: { outDir: "dist", emptyOutDir: true },
});
