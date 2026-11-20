import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const ROUTES = [
  "/login",
  "/w/demo/clients",
  "/w/demo/new",
  "/c/cl_apollo/today",
  "/c/cl_apollo/ideas",
  "/c/cl_apollo/ideas?evidence=rec_bypasscosthyd",
  "/c/cl_apollo/competitors?view=gaps",
  "/c/cl_apollo/competitors/UCfortishealthcare01",
  "/c/cl_apollo/channel",
  "/c/cl_apollo/radar?tab=early",
  "/c/cl_apollo/hygiene",
  "/c/cl_apollo/creators",
  "/c/cl_apollo/settings?tab=engine",
  "/c/cl_apollo/settings?tab=schedule",
  "/c/cl_apollo/setup/confirm?step=reasons",
  "/c/cl_apollo/setup/confirm?step=direct",
];

for (const theme of ["light", "dark"] as const) {
  for (const route of ROUTES) {
    test(`no serious accessibility violations (${theme}) on ${route}`, async ({ page }) => {
      await page.addInitScript((value) => localStorage.setItem("boardui:theme", value), theme);
      await page.goto(route);
      await page.waitForLoadState("networkidle");
      await page.waitForTimeout(500);
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]).analyze();
      const serious = results.violations.filter((violation) => violation.impact === "serious" || violation.impact === "critical");
      expect(serious.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
    });
  }
}
