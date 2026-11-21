import { expect, test } from "@playwright/test";

test("onboarding to live analysis, confirm, first brief, competitor folder and evidence", async ({ page }) => {
  await page.goto("/w/demo/new");
  await page.getByLabel("Your website or YouTube channel").fill("apollohospitals.com");
  await page.getByRole("button", { name: "Analyse my business" }).click();

  await expect(page).toHaveURL(/\/setup\/analyze\?run=/);
  await expect(page.getByText("02 · Live analysis")).toBeVisible();
  await expect(page).toHaveURL(/\/setup\/confirm/, { timeout: 60_000 });

  const heading = (name: string) => page.getByRole("heading", { name, level: 1 });
  await expect(heading("Is this what you do?")).toBeVisible();
  await expect(page).toHaveURL(/step=summary/);
  await page.keyboard.press("Enter");

  await expect(heading("Which YouTube channel is yours?")).toBeVisible();
  await page.getByRole("radio").first().click();
  await page.getByRole("button", { name: "This is mine" }).click();

  await expect(heading("What do you sell?")).toBeVisible();
  await page.getByRole("button", { name: "Remove “Cancer care”" }).click();
  await expect(page.getByText("Removed")).toBeVisible();
  await page.getByRole("button", { name: "Undo removing “Cancer care”" }).click();
  await expect(page.getByRole("button", { name: "Edit “Cancer care”" })).toBeVisible();
  await page.getByRole("button", { name: "Edit “Cardiac sciences”" }).click();
  await page.getByLabel("Correct “Cardiac sciences”").fill("Heart care");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Edit “Heart care”" })).toBeVisible();
  await page.getByLabel("Add a product or service").fill("Dialysis");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Edit “Dialysis”" })).toBeVisible();
  await page.getByRole("button", { name: "Looks right" }).click();

  await expect(heading("Where do you work?")).toBeVisible();
  await expect(page).toHaveURL(/step=locations/);
  await page.keyboard.press("Shift+Enter");
  await expect(heading("What do you sell?")).toBeVisible();
  await page.keyboard.press("Enter");
  for (const name of ["Where do you work?", "How do customers reach you?", "Why do customers choose you?", "What do your buyers ask?", "Which words describe what you do?", "Who are your direct competitors?", "Anyone else worth watching?"]) {
    await expect(heading(name)).toBeVisible();
    await page.keyboard.press("Enter");
  }

  await expect(heading("Ready for your first analysis?")).toBeVisible();
  const start = page.getByRole("button", { name: "Start analysis" });
  await expect(start).toBeEnabled();
  await start.click();

  await expect(page).toHaveURL(/\/setup\/analysis\?run=/);
  await expect(page).toHaveURL(/\/ideas/, { timeout: 60_000 });
  await expect(page.getByRole("heading", { name: "Your first brief is ready." })).toBeVisible();
  await expect(page.locator("ol").first().getByRole("link")).toHaveCount(5);

  const tip = page.getByRole("dialog").filter({ hasText: "Tip 1 of 3" });
  await expect(tip).toBeVisible();
  await tip.getByRole("button", { name: "Skip tips" }).click();
  await expect(tip).toBeHidden();

  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Competitors" }).click();
  await page.getByRole("link").filter({ hasText: "Open competitor analysis" }).filter({ hasText: "Fortis Healthcare" }).click();
  await expect(page.getByRole("heading", { name: "Fortis Healthcare", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Evidence sheet" })).toBeVisible();

  await page.getByRole("tab", { name: /Their outliers/ }).click();
  await page.getByRole("button", { name: /^Evidence for / }).first().click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText(/Calculated by GrowthHacker/)).toBeVisible();
  await expect(drawer.getByText("Outlier ratio")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(drawer).toBeHidden();

  await page.getByRole("navigation", { name: "Sections" }).getByRole("link", { name: "Next videos" }).click();
  await page.getByRole("button", { name: "Evidence", exact: true }).first().click();
  const evidence = page.getByRole("dialog");
  await expect(evidence.getByText(/Reference videos · 3/)).toBeVisible();
  await expect(page).toHaveURL(/evidence=rec_/);
});
