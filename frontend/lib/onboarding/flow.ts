// the confirm step as a list of screens, one question each; pure so the order and keys are testable

export type StepId = "business" | "buyers" | "competitors" | "review";

export type ScreenId = "summary" | "channel" | "offerings" | "locations" | "contacts" | "reasons" | "questions" | "keywords" | "direct" | "watch" | "review";

export interface Screen {
  id: ScreenId;
  step: StepId;
}

export const STEPS: { id: Exclude<StepId, "review">; label: string }[] = [
  { id: "business", label: "Business" },
  { id: "buyers", label: "Buyers" },
  { id: "competitors", label: "Competitors" },
];

const ALL_SCREENS: Screen[] = [
  { id: "summary", step: "business" },
  { id: "channel", step: "business" },
  { id: "offerings", step: "business" },
  { id: "locations", step: "business" },
  { id: "contacts", step: "business" },
  { id: "reasons", step: "business" },
  { id: "questions", step: "buyers" },
  { id: "keywords", step: "buyers" },
  { id: "direct", step: "competitors" },
  { id: "watch", step: "competitors" },
  { id: "review", step: "review" },
];

// the channel picker only appears when the onboarding run found candidates but could not verify one
export function screensFor({ askChannel }: { askChannel: boolean }): Screen[] {
  return ALL_SCREENS.filter((screen) => askChannel || screen.id !== "channel");
}

function isScreenId(screens: Screen[], value: string | null | undefined): value is ScreenId {
  return Boolean(value) && screens.some((screen) => screen.id === value);
}

// the url wins, then the screen saved when the user last left; unknown values start over
export function resolveScreen(screens: Screen[], fromUrl: string | null, saved: string | null): ScreenId {
  if (isScreenId(screens, fromUrl)) return fromUrl;
  if (isScreenId(screens, saved)) return saved;
  return screens[0].id;
}

export interface FlowState {
  screen: ScreenId;
  // 1 moves forward (next slides in from below), -1 goes back
  direction: 1 | -1;
}

export type FlowAction = { type: "next" } | { type: "back" } | { type: "go"; screen: ScreenId };

export function flowReducer(screens: Screen[], state: FlowState, action: FlowAction): FlowState {
  const index = Math.max(0, screens.findIndex((screen) => screen.id === state.screen));
  if (action.type === "next") return index < screens.length - 1 ? { screen: screens[index + 1].id, direction: 1 } : state;
  if (action.type === "back") return index > 0 ? { screen: screens[index - 1].id, direction: -1 } : state;
  if (!isScreenId(screens, action.screen) || action.screen === state.screen) return state;
  const target = screens.findIndex((screen) => screen.id === action.screen);
  return { screen: action.screen, direction: target > index ? 1 : -1 };
}

export function stepOf(screens: Screen[], screen: ScreenId): StepId {
  return screens.find((item) => item.id === screen)?.step ?? "business";
}

// share of the step's screens already confirmed, for the thin bar under each step label
export function stepProgress(screens: Screen[], screen: ScreenId): { id: Exclude<StepId, "review">; label: string; done: number; active: boolean }[] {
  const index = screens.findIndex((item) => item.id === screen);
  return STEPS.map((step) => {
    const members = screens.map((item, position) => ({ item, position })).filter(({ item }) => item.step === step.id);
    const passed = members.filter(({ position }) => position < index).length;
    return { ...step, done: members.length ? passed / members.length : 0, active: stepOf(screens, screen) === step.id };
  });
}

export type FlowKey = "next" | "back" | null;

// enter confirms and shift+enter or ↑ goes back, as typeform does; keys typed into a field or a button stay theirs
export function keyIntent(event: { key: string; shiftKey: boolean; metaKey: boolean; ctrlKey: boolean; altKey: boolean }, target: { tagName?: string; isContentEditable?: boolean } | null): FlowKey {
  if (event.metaKey || event.ctrlKey || event.altKey) return null;
  const tag = target?.tagName?.toLowerCase();
  const typing = tag === "input" || tag === "textarea" || tag === "select" || Boolean(target?.isContentEditable);
  const control = tag === "button" || tag === "a";
  if (typing) return null;
  if (event.key === "Enter") {
    if (event.shiftKey) return "back";
    return control ? null : "next";
  }
  if (event.key === "ArrowUp") return "back";
  if (event.key === "ArrowDown") return "next";
  return null;
}
