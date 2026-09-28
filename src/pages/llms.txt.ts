import type { APIRoute } from "astro";
import { barklyDiscovery } from "../data/barkly-discovery";

const BASE_URL = barklyDiscovery.organization.url.replace(/\/$/, "");

export const GET: APIRoute = () => {
  const { organization, principles, projects, pages } =
    barklyDiscovery;

  const output: string[] = [];

  output.push(`# ${organization.name}`);
  output.push("");

  output.push(`> ${organization.description}`);
  output.push("");

  output.push("## Identity");
  output.push("");

  output.push(`- Name: ${organization.name}`);
  output.push(`- Type: ${organization.type}`);
  output.push(`- Location: ${organization.location.city}, ${organization.location.state}, ${organization.location.country}`);
  output.push(`- Website: ${organization.url}`);
  output.push(`- Tagline: ${organization.tagline}`);
  output.push("");

  output.push("## What Barkly Labs Does");
  output.push("");

  output.push(organization.description);
  output.push("");

  output.push("## Principles");
  output.push("");

  for (const principle of principles) {
    output.push(`- ${principle}`);
  }

  output.push("");

  output.push("## Projects");
  output.push("");

  for (const project of projects) {
    output.push(`### ${project.name}`);
    output.push("");
    output.push(`Status: ${project.status}`);
    output.push("");
    output.push(project.description);
    output.push("");
    output.push(
      `Topics: ${project.category.join(", ")}`
    );
    output.push("");
    output.push(`URL: ${project.url}`);
    output.push("");
  }

  output.push("## Important Pages");
  output.push("");

  for (const page of pages) {
    output.push(
      `- [${page.name}](${BASE_URL}${page.path}) — ${page.description}`
    );
  }

  output.push("");

  output.push("## Topics");
  output.push("");

  for (const topic of organization.topics) {
    output.push(`- ${topic}`);
  }

  output.push("");

  output.push("## Canonical Website");
  output.push("");
  output.push(organization.url);
  output.push("");

  return new Response(output.join("\n"), {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
};