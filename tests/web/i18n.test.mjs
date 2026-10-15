import assert from "node:assert/strict";
import test from "node:test";

import { KEYS, TABLES, createStrings, pickLanguage } from "../../web/lib/i18n.js";

test("every string exists in every language", () => {
  for (const [language, table] of Object.entries(TABLES)) {
    assert.deepEqual(Object.keys(table).sort(), [...KEYS].sort(), language);
  }
});

test("Chinese locales get Chinese, everything else English", () => {
  assert.equal(pickLanguage("zh-CN"), "zh");
  assert.equal(pickLanguage("zh"), "zh");
  assert.equal(pickLanguage("en-US"), "en");
  assert.equal(pickLanguage(undefined), "en");
});

test("fills placeholders", () => {
  const t = createStrings("en");
  assert.equal(t("keyFile", { last4: "abcd", mode: "live" }), "Using the key ending in abcd (live).");
  assert.equal(createStrings("zh")("price", { amount: "$0.66" }), "约 $0.66");
  assert.equal(t("no-such-string"), "no-such-string");
});
