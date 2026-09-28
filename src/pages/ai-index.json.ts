import type { APIRoute } from "astro";
import { barklyDiscovery } from "../data/barkly-discovery";

export const GET: APIRoute = () => {
  const response = {
    schema: "barkly-discovery/v1",

    generated: true,

    organization: barklyDiscovery.organization,

    principles: barklyDiscovery.principles,

    projects: barklyDiscovery.projects,

    pages: barklyDiscovery.pages,

    discovery: {
      purpose:
        "Machine-readable description of Barkly Labs, its identity, projects, principles, and public documentation.",

      canonical:
        "https://www.barklylabs.space/",

      humanReadable:
        "https://www.barklylabs.space/",

      llms:
        "https://www.barklylabs.space/llms.txt",

      documentation:
        "https://www.barklylabs.space/docs/",
    },
  };

  return new Response(
    JSON.stringify(response, null, 2),
    {
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "Cache-Control": "public, max-age=3600",
      },
    }
  );
};