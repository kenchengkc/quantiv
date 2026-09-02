import { describe, expect, it } from "vitest";

import { METRIC_GLOSSARY } from "@/lib/metricGlossary";
import { ABOUT_STORIES, METHODOLOGY_SECTIONS } from "./aboutContent";

describe("About page content", () => {
  it("keeps the default visual-story copy intentionally short", () => {
    expect(ABOUT_STORIES).toHaveLength(3);
    for (const story of ABOUT_STORIES) {
      expect(story.title.length).toBeLessThanOrEqual(32);
      expect(story.caption.length).toBeLessThanOrEqual(100);
    }
  });

  it("preserves every methodology anchor linked from metric explainers", () => {
    const available = new Set([
      "models-and-math",
      ...METHODOLOGY_SECTIONS.map((section) => section.id),
    ]);
    const required = new Set(
      Object.values(METRIC_GLOSSARY).map((metric) =>
        metric.methodologyHref.replace("/about#", ""),
      ),
    );

    for (const anchor of required) {
      expect(available.has(anchor), `missing About methodology anchor: ${anchor}`).toBe(true);
    }
  });

  it("keeps technical explanation available without rebuilding long cards", () => {
    expect(METHODOLOGY_SECTIONS).toHaveLength(7);
    for (const section of METHODOLOGY_SECTIONS) {
      expect(section.tex.length).toBeGreaterThan(12);
      expect(section.note.length).toBeLessThanOrEqual(190);
    }
  });
});
