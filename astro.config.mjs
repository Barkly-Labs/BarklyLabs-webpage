import { defineConfig } from "astro";
import sitemap from "@astrojs/sitemap";

export default defineConfig({
  site: "https://www.barklylabs.space",

  integrations: [
    sitemap(),
  ],
});