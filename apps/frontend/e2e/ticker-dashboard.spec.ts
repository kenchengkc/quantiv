import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

function forecastFixtureSymbol(): string | null {
  const symbolsDir = path.resolve(__dirname, "..", "public", "symbols");
  const fixture = fs.readdirSync(symbolsDir).find((filename) => {
    if (!filename.endsWith(".json")) return false;
    const payload = JSON.parse(
      fs.readFileSync(path.join(symbolsDir, filename), "utf8"),
    ) as {
      expected_move?: { p10?: number | null; p90?: number | null };
      earnings_history?: unknown[];
    };
    return (
      payload.expected_move?.p10 != null &&
      payload.expected_move.p90 != null &&
      (payload.earnings_history?.length ?? 0) >= 2
    );
  });
  return fixture?.replace(/\.json$/, "") ?? null;
}

test("ticker dashboard presents one market-model-history view before detail", async ({
  page,
}) => {
  const symbol = forecastFixtureSymbol();
  test.skip(
    symbol == null,
    "No committed ticker fixture includes an ML forecast",
  );

  await page.goto(`/${symbol}`, { waitUntil: "domcontentloaded" });

  const comparison = page.getByRole("heading", {
    name: "Market vs model vs history",
  });
  const termStructure = page.getByRole("heading", {
    name: "Implied range across expiries",
  });
  const history = page.getByRole("heading", { name: /Event study/ });

  await expect(comparison).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/Straddle exceedance [≈≥≤]\d+%/)).toBeVisible();
  await expect(termStructure).toBeVisible();
  await expect(history).toBeVisible();

  const headingOrder = await page.locator("h2, h3").allTextContents();
  const comparisonIndex = headingOrder.indexOf("Market vs model vs history");
  const termStructureIndex = headingOrder.indexOf("Implied range across expiries");
  const historyIndex = headingOrder.findIndex((heading) =>
    heading.startsWith("Event study"),
  );

  expect(comparisonIndex).toBeGreaterThanOrEqual(0);
  expect(termStructureIndex).toBeGreaterThan(comparisonIndex);
  expect(historyIndex).toBeGreaterThan(termStructureIndex);
  await expect(
    page.getByRole("region", {
      name: "Research snapshot and forecast validation",
    }),
  ).toBeVisible();
  await expect(page.getByText("Probability density around spot")).toHaveCount(0);
  await expect(page.getByText("Expected move comparison")).toHaveCount(0);
  await expect(page.getByText("Event lens")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Export .* event study as CSV/ })).toBeVisible();

  await expect(page.locator(".qv-evidence-strip")).toHaveCount(0);
  await expect(page.getByText("Decision evidence")).toHaveCount(0);
});

test("About documents the platform-wide publication controls", async ({
  page,
}) => {
  // The page is ready for assertions at DOMContentLoaded. Waiting for the
  // browser's full `load` event also waits on non-critical third-party assets
  // and can exhaust Playwright's 30-second test budget on a cold CI runner.
  await page.goto("/about", { waitUntil: "domcontentloaded" });

  const controls = page.getByRole("region", {
    name: "Only validated snapshots reach the product.",
  });
  await expect(controls).toBeVisible({ timeout: 60_000 });
  await expect(controls.getByText("Point-in-time inputs")).toBeVisible();
  await expect(controls.getByText("Reconcile")).toBeVisible();
  await expect(controls.getByText("Score and verify")).toBeVisible();
  await expect(controls.getByText("Publish or stop")).toBeVisible();
  await expect(controls.getByText("Fail closed")).toBeVisible();
});
