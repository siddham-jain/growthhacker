import { describe, expect, it } from "vitest";
import { apiPath } from "./http";

describe("apiPath", () => {
  it("encodes each interpolated segment and leaves the template as written", () => {
    const channelId = "UC1/../../admin?x=1#y";
    expect(apiPath`/clients/${"cl 1"}/analytics/competitors/${channelId}`).toBe("/clients/cl%201/analytics/competitors/UC1%2F..%2F..%2Fadmin%3Fx%3D1%23y");
    expect(apiPath`/runs/${42}/events`).toBe("/runs/42/events");
  });
});
