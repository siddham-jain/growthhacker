import { describe, expect, it } from "vitest";
import { flowReducer, keyIntent, resolveScreen, screensFor, stepOf, stepProgress, type FlowState } from "./flow";

const withChannel = screensFor({ askChannel: true });
const withoutChannel = screensFor({ askChannel: false });

const key = (value: string, shiftKey = false) => ({ key: value, shiftKey, metaKey: false, ctrlKey: false, altKey: false });

describe("confirm flow", () => {
  it("runs business, buyers and competitors, then review, and asks for the channel only when needed", () => {
    expect(withChannel.map((screen) => screen.id)).toEqual(["summary", "channel", "offerings", "locations", "contacts", "reasons", "questions", "keywords", "direct", "watch", "review"]);
    expect(withoutChannel.map((screen) => screen.id)).not.toContain("channel");
    expect(stepOf(withoutChannel, "keywords")).toBe("buyers");
  });

  it("moves forward and back with a direction, and stops at both ends", () => {
    let state: FlowState = { screen: "summary", direction: 1 };
    expect(flowReducer(withoutChannel, state, { type: "back" })).toBe(state);
    state = flowReducer(withoutChannel, state, { type: "next" });
    expect(state).toEqual({ screen: "offerings", direction: 1 });
    expect(flowReducer(withoutChannel, state, { type: "back" })).toEqual({ screen: "summary", direction: -1 });
    const last: FlowState = { screen: "review", direction: 1 };
    expect(flowReducer(withoutChannel, last, { type: "next" })).toBe(last);
  });

  it("jumps to a screen with the direction it lies in and ignores unknown ones", () => {
    const state: FlowState = { screen: "review", direction: 1 };
    expect(flowReducer(withoutChannel, state, { type: "go", screen: "keywords" })).toEqual({ screen: "keywords", direction: -1 });
    expect(flowReducer(withoutChannel, state, { type: "go", screen: "channel" })).toBe(state);
  });

  it("resumes from the url, then the saved screen, else the start", () => {
    expect(resolveScreen(withoutChannel, "keywords", "direct")).toBe("keywords");
    expect(resolveScreen(withoutChannel, "nonsense", "direct")).toBe("direct");
    expect(resolveScreen(withoutChannel, "channel", null)).toBe("summary");
    expect(resolveScreen(withChannel, null, null)).toBe("summary");
  });

  it("fills each step's bar by the screens already confirmed", () => {
    const progress = stepProgress(withoutChannel, "locations");
    expect(progress.map((step) => [step.id, step.done, step.active])).toEqual([
      ["business", 2 / 5, true],
      ["buyers", 0, false],
      ["competitors", 0, false],
    ]);
    expect(stepProgress(withoutChannel, "review").every((step) => step.done === 1)).toBe(true);
  });

  it("maps typeform keys without stealing keys typed into fields or buttons", () => {
    const body = { tagName: "BODY" };
    expect(keyIntent(key("Enter"), body)).toBe("next");
    expect(keyIntent(key("Enter", true), body)).toBe("back");
    expect(keyIntent(key("ArrowUp"), body)).toBe("back");
    expect(keyIntent(key("ArrowDown"), body)).toBe("next");
    expect(keyIntent(key("Enter"), { tagName: "INPUT" })).toBeNull();
    expect(keyIntent(key("ArrowUp"), { tagName: "TEXTAREA" })).toBeNull();
    expect(keyIntent(key("Enter"), { tagName: "BUTTON" })).toBeNull();
    expect(keyIntent({ ...key("Enter"), metaKey: true }, body)).toBeNull();
    expect(keyIntent(key("a"), body)).toBeNull();
  });
});
