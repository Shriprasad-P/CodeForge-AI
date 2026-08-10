import { describe, expect, it } from "vitest";

import { ApiError, getApiBaseUrl } from "@/lib/api";

describe("getApiBaseUrl", () => {
  it("defaults to localhost API", () => {
    expect(getApiBaseUrl()).toBe("http://localhost:8000");
  });
});

describe("ApiError", () => {
  it("stores status", () => {
    const error = new ApiError(401, "Not authenticated");
    expect(error.status).toBe(401);
    expect(error.message).toBe("Not authenticated");
  });
});
