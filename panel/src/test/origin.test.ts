import { describe, expect, it } from "vitest";

import { originError, parseAllowedOrigin } from "@/lib/origin";

import shared from "../../../tests/fixtures/origin_cases.json";

describe("parseAllowedOrigin", () => {
  it.each(shared.cases)("matches the API for $input", ({ input, expected }) => {
    expect(parseAllowedOrigin(input)).toBe(expected);
  });

  it("canonicalises a trailing slash like the API", () => {
    expect(parseAllowedOrigin("https://shop.example/")).toBe("https://shop.example");
  });

  it("rejects path, wildcard, bad scheme and missing scheme", () => {
    for (const bad of ["https://shop.example/path", "https://*.example", "*", "ftp://shop.example", "shop.example"]) {
      expect(parseAllowedOrigin(bad)).toBeNull();
    }
    expect(originError("https://shop.example/path")).toMatch(/No path/);
    expect(originError("  ")).toBe("Enter an origin.");
  });
});
