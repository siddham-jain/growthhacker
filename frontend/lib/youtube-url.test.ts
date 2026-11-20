import { describe, expect, it } from "vitest";
import { videoIdFromUrl } from "./youtube-url";

describe("videoIdFromUrl", () => {
  it.each([
    ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["https://youtube.com/shorts/dQw4w9WgXcQ?feature=share", "dQw4w9WgXcQ"],
    ["https://m.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"],
    ["dQw4w9WgXcQ", "dQw4w9WgXcQ"],
  ])("reads %s", (input, expected) => expect(videoIdFromUrl(input)).toBe(expected));

  it.each(["https://vimeo.com/123", "not a link", "https://youtube.com/watch?v=short", "https://evil.com/watch?v=dQw4w9WgXcQ"])("rejects %s", (input) => expect(videoIdFromUrl(input)).toBeNull());
});
