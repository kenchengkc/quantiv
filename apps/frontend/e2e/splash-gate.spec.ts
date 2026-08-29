import { expect, test } from "@playwright/test";
import { SPLASH_SESSION_KEY, SPLASH_SKIP_ATTRIBUTE } from "@/lib/splashSession";

test.describe("homepage splash", () => {
  test("first visit paints the server-rendered intro without hiding the app shell", async ({
    page,
  }) => {
    await page.addInitScript(
      (key) => sessionStorage.removeItem(key),
      SPLASH_SESSION_KEY,
    );

    const response = await page.request.get("/");
    expect(await response.text()).toContain("quantiv-splash-wordmark");

    await page.goto("/", { waitUntil: "domcontentloaded" });

    await expect(
      page.locator("head > script#quantiv-splash-session"),
    ).toHaveCount(1);
    await expect(page.locator(".quantiv-splash")).toBeVisible();
    await expect(page.locator(".quantiv-splash-wordmark")).toHaveText(
      "QUANTIV",
    );
    await expect(page.locator(".quantiv-app-shell")).toBeVisible();

    await page.waitForFunction(() =>
      performance
        .getEntriesByType("paint")
        .some((entry) => entry.name === "first-contentful-paint"),
    );
    const firstContent = await page.evaluate(
      () =>
        performance
          .getEntriesByType("paint")
          .find((entry) => entry.name === "first-contentful-paint")
          ?.startTime ?? 0,
    );
    expect(firstContent).toBeGreaterThan(0);
  });

  test("waits for splash images before starting the full sequence", async ({
    page,
  }) => {
    await page.addInitScript(
      (key) => sessionStorage.removeItem(key),
      SPLASH_SESSION_KEY,
    );
    await page.addInitScript(() => {
      const nativeDecode = HTMLImageElement.prototype.decode;
      HTMLImageElement.prototype.decode = function delayedDecode() {
        return new Promise((resolve) => {
          window.setTimeout(() => {
            nativeDecode.call(this).then(
              () => resolve(),
              () => resolve(),
            );
          }, 350);
        });
      };
    });

    await page.goto("/", { waitUntil: "domcontentloaded" });

    const initialState = await page.evaluate(() => {
      const splash = document.querySelector(".quantiv-splash");
      return {
        ready: splash?.classList.contains("quantiv-splash--ready") ?? false,
        playState: splash ? getComputedStyle(splash).animationPlayState : null,
      };
    });
    expect(initialState.ready).toBe(false);
    expect(initialState.playState).toBe("paused");

    await page.waitForFunction(() =>
      document
        .querySelector(".quantiv-splash")
        ?.classList.contains("quantiv-splash--ready"),
    );
    const readyAt = Date.now();
    await page.waitForFunction(
      (attribute) => document.documentElement.getAttribute(attribute) === "1",
      SPLASH_SKIP_ATTRIBUTE,
    );
    expect(Date.now() - readyAt).toBeGreaterThanOrEqual(1_500);
  });

  test("returning session skips the intro before paint", async ({ page }) => {
    await page.addInitScript(
      (key) => sessionStorage.setItem(key, "1"),
      SPLASH_SESSION_KEY,
    );

    await page.goto("/", { waitUntil: "domcontentloaded" });

    const skip = await page.evaluate(
      (attribute) => document.documentElement.getAttribute(attribute),
      SPLASH_SKIP_ATTRIBUTE,
    );
    expect(skip).toBe("1");
    await expect(page.locator(".quantiv-splash")).toBeHidden();
    await expect(page.locator(".quantiv-app-shell")).toBeVisible();
  });

  test("subpages do not receive homepage splash markup", async ({ page }) => {
    await page.addInitScript(
      (key) => sessionStorage.removeItem(key),
      SPLASH_SESSION_KEY,
    );

    await page.goto("/screener", { waitUntil: "domcontentloaded" });

    await expect(page.locator(".quantiv-splash")).toHaveCount(0);
    await expect(page.locator(".quantiv-app-shell")).toBeVisible();
  });
});
