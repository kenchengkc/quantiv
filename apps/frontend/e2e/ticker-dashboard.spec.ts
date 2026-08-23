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

test("ticker dashboard presents market, model, comparison, then history", async ({
  page,
}) => {
  const symbol = forecastFixtureSymbol();
  test.skip(
    symbol == null,
    "No committed ticker fixture includes an ML forecast",
  );

  await page.goto(`/${symbol}`);

  const forecast = page.getByRole("heading", { name: "Forecast distribution" });
  const comparison = page.getByRole("heading", {
    name: "Expected move comparison",
  });
  const history = page.getByRole("heading", { name: /Realized moves/ });

  await expect(forecast).toBeVisible({ timeout: 60_000 });
  await expect(comparison).toBeVisible();
  await expect(history).toBeVisible();

  const headingOrder = await page.locator("h2, h3").allTextContents();
  const forecastIndex = headingOrder.indexOf("Forecast distribution");
  const comparisonIndex = headingOrder.indexOf("Expected move comparison");
  const historyIndex = headingOrder.findIndex((heading) =>
    heading.startsWith("Realized moves"),
  );

  expect(forecastIndex).toBeGreaterThanOrEqual(0);
  expect(comparisonIndex).toBeGreaterThan(forecastIndex);
  expect(historyIndex).toBeGreaterThan(comparisonIndex);
  await expect(page.getByText("Event lens")).toHaveCount(0);

  await expect(page.locator(".qv-evidence-strip")).toHaveCount(0);
  await expect(page.getByText("Decision evidence")).toHaveCount(0);
});

test("About documents the platform-wide publication controls", async ({
  page,
}) => {
  await page.goto("/about");

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
