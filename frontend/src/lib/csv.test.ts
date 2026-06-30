import { describe, expect, it } from "vitest";
import { toCsv } from "./csv";

describe("toCsv", () => {
  it("escapes commas, quotes, and newlines", () => {
    expect(toCsv([{ title: 'A "quoted", event', notes: "line\nbreak" }])).toBe(
      '\uFEFFtitle,notes\n"A ""quoted"", event","line\nbreak"',
    );
  });

  it("adds a UTF-8 BOM and neutralizes spreadsheet formulas", () => {
    expect(toCsv([{ title: "=SUM(A1:A2)", owner: "@handle", plus: "+risk", minus: "-risk" }])).toBe(
      "\uFEFFtitle,owner,plus,minus\n'=SUM(A1:A2),'@handle,'+risk,'-risk",
    );
  });
});
