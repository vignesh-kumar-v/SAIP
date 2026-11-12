import { describe, expect, it } from "vitest";
import { parseBlocks } from "./Markdown";

describe("chat markdown", () => {
  it("finds fenced code with its language, lists and paragraphs", () => {
    const b = parseBlocks("Here you go:\n\n```python\nimport time\nprint(1)\n```\n\n- one\n- two\n\n1. first\n2. second\n\nDone.");
    expect(b.map((x) => x.t)).toEqual(["p", "code", "ul", "ol", "p"]);
    const code = b[1] as { lang: string; text: string };
    expect(code.lang).toBe("python");
    expect(code.text).toBe("import time\nprint(1)");
    expect((b[2] as { items: string[] }).items).toEqual(["one", "two"]);
  });

  it("ends an unclosed fence at the end of the text instead of swallowing nothing", () => {
    const b = parseBlocks("```js\nconst a = 1;");
    expect(b).toEqual([{ t: "code", lang: "js", text: "const a = 1;" }]);
  });

  it("keeps hostile text as text", () => {
    const b = parseBlocks("<img src=x onerror=alert(1)> [click](javascript:alert(1))");
    expect(b).toHaveLength(1);
    expect(b[0].t).toBe("p");
  });
});

import { splitQuote, withQuote } from "./ChatParts";

describe("replying to a message", () => {
  it("round-trips a quote above the reply", () => {
    const sent = withQuote("first line\nsecond line", "yes, the second");
    expect(sent).toBe("> first line\n> second line\n\nyes, the second");
    expect(splitQuote(sent)).toEqual({ quote: "first line\nsecond line", rest: "yes, the second" });
    expect(splitQuote("no quote here")).toEqual({ quote: null, rest: "no quote here" });
    expect(withQuote(null, "plain")).toBe("plain");
  });
});
