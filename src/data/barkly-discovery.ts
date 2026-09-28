export const barklyDiscovery = {
  organization: {
    name: "Barkly Labs",
    type: "Human-centered technology laboratory",
    url: "https://www.barklylabs.space/",
    location: {
      city: "Detroit",
      state: "Michigan",
      country: "United States",
    },

    tagline: "Technology should adapt to humans.",

    description:
      "Barkly Labs is a Detroit-based human-centered technology laboratory exploring AI, software, hardware, robotics, computer vision, creative technology, education, and community.",

    topics: [
      "human-centered technology",
      "artificial intelligence",
      "local AI",
      "software engineering",
      "hardware",
      "embedded systems",
      "robotics",
      "computer vision",
      "creative technology",
      "accessibility",
      "documentation",
      "education",
      "community technology",
    ],
  },

  principles: [
    "Humans are not machines",
    "Accessibility is foundational",
    "Make technology understandable",
    "Expand human capability",
    "Respect human autonomy",
    "Build openly where practical",
    "Experiment, document, learn, improve",
  ],

  projects: [
    {
      name: "CYN-X",
      slug: "cyn-x",
      category: [
        "artificial intelligence",
        "local AI",
        "intelligent systems",
        "human-AI interaction",
      ],
      description:
        "CYN-X is Barkly Labs' flagship intelligent-systems project exploring local AI, interfaces, personality systems, computer vision, tools, and future intelligent computing systems.",
      status: "experimental",
      url: "https://www.barklylabs.space/",
    },

    {
      name: "PawBoard",
      slug: "pawboard",
      category: [
        "hardware",
        "human-centered computing",
        "interfaces",
        "embedded systems",
      ],
      description:
        "PawBoard is an experimental human-centered computing and hardware platform intended to make complex computing systems easier for people to understand, interact with, and modify.",
      status: "experimental",
      url: "https://www.barklylabs.space/",
    },

    {
      name: "Barkly Docs",
      slug: "barkly-docs",
      category: [
        "documentation",
        "developer tools",
        "software engineering",
        "AST analysis",
      ],
      description:
        "Barkly Docs is an automated documentation system that analyzes software structure and presents it in a human-readable form without importing or executing the project being documented.",
      status: "active",
      url: "https://www.barklylabs.space/docs/",
    },

    {
      name: "CYN-X Vision",
      slug: "cyn-x-vision",
      category: [
        "computer vision",
        "AI",
        "machine learning",
        "responsible technology",
      ],
      description:
        "CYN-X Vision explores computer vision systems with human oversight, transparency, privacy, and responsible-use considerations.",
      status: "experimental",
      url: "https://www.barklylabs.space/",
    },

    {
      name: "PAW-HC",
      slug: "paw-hc",
      category: [
        "AI engineering",
        "human-centered AI",
        "accessibility",
        "engineering standards",
      ],
      description:
        "PAW-HC is Barkly Labs' proposed Human-Centered AI Engineering Standard for accessibility, cognitive load, autonomy, privacy, transparency, safety, non-manipulation, and evidence.",
      status: "experimental",
      url: "https://www.barklylabs.space/",
    },
  ],

  pages: [
    {
      name: "Barkly Labs",
      path: "/",
      description:
        "The main Barkly Labs website and overview of the laboratory.",
    },

    {
      name: "Why Barkly Exists",
      path: "/about/",
      description:
        "The philosophy, principles, and motivation behind Barkly Labs.",
    },

    {
      name: "Barkly Documentation",
      path: "/docs/",
      description:
        "Technical documentation, research notes, systems, architecture, and engineering references.",
    },

    {
      name: "Labs as a Service",
      path: "/laas/",
      description:
        "Barkly Labs' experimental model for supporting creators, builders, and engineering projects.",
    },
  ],
} as const;