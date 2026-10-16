import assert from "node:assert/strict";
import test from "node:test";

import { createStrings } from "../../web/lib/i18n.js";
import { renderSettingsPanel } from "../../web/lib/settings-panel.js";

const t = createStrings("en");
const settle = () => new Promise((resolve) => setImmediate(resolve));
const FILE_KEY = { source: "file", mode: "live", last4: "J0aa", error: null };
const KEY = "cud_sk_live_abcdefghijklmnopqrstuvwx";

// Just enough of a DOM for the panel. Setting innerHTML throws, so any HTML injection fails the test.
class FakeNode {
  constructor(tag) {
    Object.assign(this, { tag, attributes: {}, children: [], listeners: {}, style: {}, value: "", disabled: false, own: "" });
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  getAttribute(name) {
    return this.attributes[name];
  }
  append(...nodes) {
    this.children.push(...nodes);
  }
  addEventListener(type, handler) {
    (this.listeners[type] ??= []).push(handler);
  }
  click() {
    if (this.disabled) return Promise.resolve([]);
    return Promise.all((this.listeners.click ?? []).map((handler) => handler()));
  }
  set textContent(value) {
    this.own = String(value);
    this.children = [];
  }
  get textContent() {
    return this.own + this.children.map((child) => child.textContent).join("");
  }
  set innerHTML(value) {
    throw new Error(`innerHTML is not allowed: ${value}`);
  }
}

const find = (node, name) =>
  node.getAttribute?.("data-cuddler") === name ? node : node.children.map((child) => find(child, name)).find(Boolean);

function mount(client) {
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createTextNode: (text) => ({ textContent: text, children: [] }),
  };
  const root = renderSettingsPanel({ client, t });
  const part = (name) => find(root, name);
  return {
    root, input: part("key"), save: part("save"), remove: part("remove"),
    keyLine: part("status"), balanceLine: part("balance"), message: part("message"),
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => ((resolve = done), (reject = fail)));
  return { promise, resolve, reject };
}

test("the key field is a password field that browsers are told not to fill or remember", async () => {
  const { input } = mount({ status: async () => ({ key: { source: "none" } }) });
  assert.equal(input.getAttribute("type"), "password");
  assert.equal(input.getAttribute("autocomplete"), "new-password");
  assert.equal(input.getAttribute("spellcheck"), "false");
});

test("text from the server is shown as text, never as HTML", async () => {
  const hostile = '<img src=x onerror="alert(1)">';
  const { keyLine, balanceLine } = mount({
    status: async () => ({ key: { source: "file", error: "key_unreadable", message: hostile }, balance_error: { message: hostile } }),
  });
  await settle();
  assert.equal(keyLine.textContent, `The saved key cannot be used: ${hostile}`);
  assert.equal(balanceLine.textContent, `Balance unavailable: ${hostile}`);
});

test("saving sends the key once, empties the field, and never logs or stores it", async () => {
  const logged = [];
  const originals = {};
  for (const name of ["log", "info", "warn", "error", "debug"]) {
    originals[name] = console[name];
    console[name] = (...args) => logged.push(args);
  }
  for (const name of ["localStorage", "sessionStorage"]) {
    Object.defineProperty(globalThis, name, { configurable: true, get() { throw new Error(`${name} must not be used`); } });
  }
  try {
    const saved = [];
    const panel = mount({ status: async () => ({ key: FILE_KEY }), saveKey: async (key) => (saved.push(key), {}) });
    await settle();
    panel.input.value = KEY;
    await panel.save.click();
    assert.deepEqual(saved, [KEY]);
    assert.equal(panel.input.value, "");
    assert.equal(panel.message.textContent, "Saved.");
    assert.ok(!panel.root.textContent.includes(KEY));
    assert.deepEqual(logged, []);
  } finally {
    Object.assign(console, originals);
    delete globalThis.localStorage;
    delete globalThis.sessionStorage;
  }
});

test("a rejected key stays in the field so it can be fixed, and the form stays usable", async () => {
  const panel = mount({
    status: async () => ({ key: { source: "none" } }),
    saveKey: async () => {
      throw new Error("That does not look like a Cuddler key.");
    },
  });
  await settle();
  panel.input.value = "cud_sk_live_typo";
  await panel.save.click();
  assert.equal(panel.input.value, "cud_sk_live_typo");
  assert.equal(panel.message.textContent, "That does not look like a Cuddler key.");
  assert.equal(panel.save.disabled, false);
});

test("the form is usable again, and the result still shown, when the status cannot be read after a save", async () => {
  let calls = 0;
  const panel = mount({
    status: async () => {
      calls += 1;
      if (calls === 1) return { key: FILE_KEY };
      throw new Error("Failed to fetch");
    },
    saveKey: async () => ({}),
  });
  await settle();
  panel.input.value = KEY;
  await panel.save.click();
  assert.equal(panel.message.textContent, "Saved.");
  assert.equal(panel.keyLine.textContent, "Failed to fetch");
  assert.equal(panel.save.disabled, false);
  assert.equal(panel.remove.disabled, false);
});

test("an older status answer cannot overwrite a newer one", async () => {
  const first = deferred();
  const second = deferred();
  const answers = [first, second];
  const panel = mount({ status: () => answers.shift().promise, saveKey: async () => ({}) });
  panel.input.value = KEY;
  const clicked = panel.save.click();
  await settle();
  second.resolve({ key: FILE_KEY });
  await clicked;
  first.resolve({ key: { source: "none" } });
  await settle();
  assert.equal(panel.keyLine.textContent, "Using the key ending in J0aa (live).");
  assert.equal(panel.remove.disabled, false);
});

test("a key from the environment locks the form", async () => {
  const panel = mount({ status: async () => ({ key: { source: "env", mode: "live", last4: "R1q0", error: null } }) });
  await settle();
  assert.equal(panel.input.disabled, true);
  assert.equal(panel.save.disabled, true);
  assert.equal(panel.remove.disabled, true);
  assert.equal(panel.message.textContent, "Set by the environment variable; change it there.");
});
