import assert from "node:assert/strict";
import test from "node:test";

import { createStrings } from "../../web/lib/i18n.js";
import { describeStatus } from "../../web/lib/settings-panel.js";

const t = createStrings("en");

test("no key yet", () => {
  assert.deepEqual(describeStatus({ key: { source: "none" } }, t), {
    keyText: "No API key yet.", balanceText: "", editable: true, removable: false,
  });
});

test("a saved live key with its balance", () => {
  const view = describeStatus({
    key: { source: "file", mode: "live", last4: "J0aa" },
    balance: { available_display: "$12.34" },
  }, t);
  assert.equal(view.keyText, "Using the key ending in J0aa (live).");
  assert.equal(view.balanceText, "Balance: $12.34 available.");
  assert.equal(view.removable, true);
});

test("a key from the environment cannot be edited here", () => {
  const view = describeStatus({ key: { source: "env", mode: "test", last4: "R1q0" } }, t);
  assert.equal(view.keyText, "Using the key from CUDDLER_API_KEY, ending in R1q0 (test key, prices only).");
  assert.equal(view.editable, false);
  assert.equal(view.removable, false);
});

test("problems are explained", () => {
  const view = describeStatus({
    key: { source: "file", error: "key_unreadable", message: "Save it again." },
    balance_error: { message: "The key was rejected." },
  }, t);
  assert.equal(view.keyText, "The saved key cannot be used: Save it again.");
  assert.equal(view.balanceText, "Balance unavailable: The key was rejected.");
});

test("a key from the environment that cannot be used is not called a saved key", () => {
  const status = { key: { source: "env", error: "key_invalid_format", message: "That is not a Cuddler key." } };
  const view = describeStatus(status, t);
  assert.equal(view.keyText, "The key in CUDDLER_API_KEY cannot be used: That is not a Cuddler key.");
  assert.equal(view.editable, false);
  assert.equal(view.removable, false);
  assert.match(describeStatus(status, createStrings("zh")).keyText, /^环境变量 CUDDLER_API_KEY/);
});
