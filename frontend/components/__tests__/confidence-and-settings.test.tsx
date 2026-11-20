import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { safeNext } from "@/components/auth/login-form";
import { validateProvider } from "@/components/settings/engine-settings";
import { ConfidenceMeter } from "@/components/ui/confidence-meter";


describe("ConfidenceMeter", () => {
  it("always prints the label, and First-mover says there are no references", () => {
    render(<ConfidenceMeter confidence={{ label: "Low", outliers: 2, channels: 1 }} />);
    expect(screen.getByText("Low")).toBeInTheDocument();
    render(<ConfidenceMeter confidence={{ label: "Low", outliers: 0, channels: 0 }} firstMover />);
    expect(screen.getByText("First-mover")).toBeInTheDocument();
    expect(screen.getByText(/no reference videos yet/)).toBeInTheDocument();
  });
});

describe("validateProvider", () => {
  it("requires a base url only for an enabled openai-compatible fallback", () => {
    expect(validateProvider({ base_url: "" }, "openai_compatible", true)).toMatch(/base URL/);
    expect(validateProvider({ base_url: "http://localhost:8001/v1" }, "openai_compatible", true)).toBeNull();
    expect(validateProvider({ base_url: "http://example.com" }, "openai_compatible", true)).toMatch(/base URL/);
    expect(validateProvider({ base_url: "" }, "openai_compatible", false)).toBeNull();
    expect(validateProvider({ base_url: "" }, "anthropic", true)).toBeNull();
  });
});

describe("safeNext", () => {
  it("only follows same-origin paths after sign-in", () => {
    expect(safeNext("/c/cl/today")).toBe("/c/cl/today");
    expect(safeNext("//evil.com")).toBeNull();
    expect(safeNext("https://evil.com")).toBeNull();
    expect(safeNext(null)).toBeNull();
  });
});
