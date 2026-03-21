import { describe, expect, it } from "vitest";
import { shouldPreferPresentationLiveBrowser } from "../../src/utils/presentation";

describe("presentation utils", () => {
  it("prefers live browser for loopback and private hosts", () => {
    expect(shouldPreferPresentationLiveBrowser("http://127.0.0.1:3000")).toBe(true);
    expect(shouldPreferPresentationLiveBrowser("http://localhost:5173")).toBe(true);
    expect(shouldPreferPresentationLiveBrowser("https://192.168.1.20/dashboard")).toBe(true);
    expect(shouldPreferPresentationLiveBrowser("http://172.20.0.4")).toBe(true);
    expect(shouldPreferPresentationLiveBrowser("http://host.docker.internal:8080")).toBe(true);
    expect(shouldPreferPresentationLiveBrowser("https://demo.local")).toBe(true);
  });

  it("keeps preview as default for normal public urls", () => {
    expect(shouldPreferPresentationLiveBrowser("https://example.com")).toBe(false);
    expect(shouldPreferPresentationLiveBrowser("https://app.github.com/demo")).toBe(false);
    expect(shouldPreferPresentationLiveBrowser("")).toBe(false);
    expect(shouldPreferPresentationLiveBrowser("not-a-url")).toBe(false);
    expect(shouldPreferPresentationLiveBrowser("file:///tmp/demo.html")).toBe(false);
  });
});
