import { describe, expect, it } from "vitest";
import { isMeaningfulContact } from "./contact";

describe("isMeaningfulContact", () => {
  it.each(["+91-88-0000-1068", "info@medanta.org", "Book Appointment", "Whatsapp", "संपर्क करें"])("keeps %s", (label) => expect(isMeaningfulContact(label)).toBe(true));
  it.each(["Submit", "submit »", "+91", "1068", "", "form", "Go", "Get OTP"])("hides %s", (label) => expect(isMeaningfulContact(label)).toBe(false));
});
