import { describe, expect, it } from "vitest";
import { toCsv } from "./csv";

describe("toCsv", () => {
  it("escapes commas, quotes, and newlines", () => {
    expect(toCsv([{ title: 'A "quoted", event', notes: "line\nbreak" }])).toBe(
      'title,notes\n"A ""quoted"", event","line\nbreak"',
    );
  });
});
