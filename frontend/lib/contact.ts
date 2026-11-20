const GENERIC_LABELS = new Set(["submit", "submit now", "send", "go", "ok", "next", "continue", "search", "enter", "done", "save", "apply", "reset", "clear", "form", "click here", "here", "get otp", "send otp", "resend otp", "verify", "verify otp"]);
const FULL_PHONE_DIGITS = 8;

// a way to reach the business needs words that name it or a full phone number; form buttons and "+91" are neither
export function isMeaningfulContact(label: string): boolean {
  const text = label.replace(/\s+/g, " ").trim();
  if ((text.match(/\d/g) ?? []).length >= FULL_PHONE_DIGITS && /^[\d\s()+.-]+$/.test(text)) return true;
  const letters = text.match(/\p{L}/gu) ?? [];
  return letters.length >= 2 && !GENERIC_LABELS.has(text.toLowerCase().replace(/[\s.!:>»→]+$/u, ""));
}
