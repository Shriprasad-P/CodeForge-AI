import { describe, expect, it } from "vitest";

import { getApiBaseUrl } from "@/lib/api";

describe("getApiBaseUrl", () => {
  it("defaults to localhost API", () => {
    expect(getApiBaseUrl()).toBe("http://localhost:8000");
  });
});
