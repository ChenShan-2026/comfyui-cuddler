import assert from "node:assert/strict";
import test from "node:test";

import { createStrings } from "../../web/lib/i18n.js";
import { capDisplay, createPriceUpdater, holdDisplay, toMicros } from "../../web/lib/price.js";

const t = createStrings("en");
const settle = () => new Promise((resolve) => setImmediate(resolve));
const refused = () => Object.assign(new Error("Too many requests."), { code: "rate_limited" });
const PRICE = { usd: "0.660000", hold_usd: "0.666955", display: "$0.66" };

// A clock the test moves by hand; schedule and cancel stand in for setTimeout and clearTimeout.
function virtualClock() {
  let now = 0;
  let next = 1;
  const queue = [];
  return {
    get now() {
      return now;
    },
    get pending() {
      return queue.length;
    },
    schedule(fn, ms = 0) {
      queue.push({ id: next, at: now + ms, fn });
      return next++;
    },
    cancel(id) {
      const index = queue.findIndex((item) => item.id === id);
      if (index >= 0) queue.splice(index, 1);
    },
    async advance(ms) {
      const end = now + ms;
      for (;;) {
        queue.sort((a, b) => a.at - b.at || a.id - b.id);
        if (!queue.length || queue[0].at > end) break;
        const item = queue.shift();
        now = item.at;
        item.fn();
        await settle();
      }
      now = end;
    },
  };
}

function updaterFor(clock, client, shown, extra = {}) {
  return createPriceUpdater({
    client, t, onText: (value) => shown.push(value), schedule: clock.schedule, cancel: clock.cancel, ...extra,
  });
}

test("waits for the settings to settle and shows the latest price", async () => {
  const clock = virtualClock();
  const asked = [];
  const shown = [];
  const client = { estimate: async (kind, body) => (asked.push(body.duration), PRICE) };
  const updater = updaterFor(clock, client, shown);
  updater.request("video", { duration: 4 }, "2.000000");
  await clock.advance(500);
  updater.request("video", { duration: 5 }, "2.000000");
  await clock.advance(999);
  assert.deepEqual(asked, []);
  await clock.advance(1);
  assert.deepEqual(asked, [5]);
  assert.deepEqual(shown, [{ text: "≈ $0.66", over: false, error: false }]);
});

test("above the limit it shows the most the run can be charged, rounded up to cents", async () => {
  const clock = virtualClock();
  const shown = [];
  // The estimate rounds up to exactly the limit; the hold (what the limit is checked against) is above it.
  const client = { estimate: async () => ({ usd: "1.995000", hold_usd: "2.034000", display: "$2.00" }) };
  updaterFor(clock, client, shown).request("video", {}, "2.000000");
  await clock.advance(1000);
  assert.deepEqual(shown, [{ text: "Up to $2.04, above the $2.00 limit", over: true, error: false }]);
});

test("a hold exactly at the limit is not over it", async () => {
  const clock = virtualClock();
  const shown = [];
  const client = { estimate: async () => ({ usd: "1.990000", hold_usd: "2.000000", display: "$1.99" }) };
  updaterFor(clock, client, shown).request("video", {}, 2);
  await clock.advance(1000);
  assert.deepEqual(shown, [{ text: "≈ $1.99", over: false, error: false }]);
});

test("one micro-dollar above the limit is over it, and the label still reads true", async () => {
  const clock = virtualClock();
  const shown = [];
  const client = { estimate: async () => ({ usd: "2.000000", hold_usd: "2.000001", display: "$2.00" }) };
  updaterFor(clock, client, shown).request("video", {}, 2);
  await clock.advance(1000);
  assert.deepEqual(shown, [{ text: "Up to $2.01, above the $2.00 limit", over: true, error: false }]);
});

test("asks for a key when there is none", async () => {
  const clock = virtualClock();
  const shown = [];
  const client = {
    estimate: async () => {
      throw Object.assign(new Error("Add your key."), { code: "key_missing" });
    },
  };
  updaterFor(clock, client, shown).request("video", {}, "2");
  await clock.advance(1000);
  assert.deepEqual(shown, [{ text: "Add an API key to see the price", over: false, error: true }]);
});

test("rounds without floats: holds up, limits down", () => {
  assert.equal(holdDisplay("2.000000"), "$2.00");
  assert.equal(holdDisplay("2.000001"), "$2.01");
  assert.equal(holdDisplay("0.070000"), "$0.07");
  assert.equal(holdDisplay("0.07"), "$0.07");
  assert.equal(holdDisplay(0.07), "$0.07");
  assert.equal(holdDisplay("4.35"), "$4.35");
  assert.equal(holdDisplay("0.000001"), "$0.01");
  assert.equal(holdDisplay("0"), "$0.00");
  assert.equal(holdDisplay("1000"), "$1000.00");
  assert.equal(capDisplay("2.000000"), "$2.00");
  assert.equal(capDisplay(0.5), "$0.50");
  assert.equal(capDisplay(2.0100000000000002), "$2.01");
  assert.equal(capDisplay("2.019999"), "$2.01");
  assert.equal(toMicros("0.07"), 70000n);
  assert.equal(toMicros(0.07), 70000n);
  assert.equal(toMicros("1.5e3"), 1_500_000_000n);
  for (const bad of ["", ".", "abc", "-1", -1, NaN, Infinity, undefined, null, "1e99"]) {
    assert.equal(toMicros(bad), null, String(bad));
    assert.equal(holdDisplay(bad), null, String(bad));
  }
});

test("asks again when the host says too many requests, and shows only the price", async () => {
  const clock = virtualClock();
  const shown = [];
  let calls = 0;
  const client = {
    estimate: async () => {
      calls += 1;
      if (calls === 1) throw refused();
      return PRICE;
    },
  };
  updaterFor(clock, client, shown, { random: () => 0 }).request("video", {}, "2");
  await clock.advance(1000);
  assert.equal(calls, 1);
  assert.deepEqual(shown, []);
  await clock.advance(999);
  assert.equal(calls, 1);
  await clock.advance(1);
  assert.equal(calls, 2);
  assert.deepEqual(shown, [{ text: "≈ $0.66", over: false, error: false }]);
});

test("gives up after the retries and shows the host's message", async () => {
  const clock = virtualClock();
  const shown = [];
  let calls = 0;
  const client = { estimate: async () => (calls += 1, Promise.reject(refused())) };
  updaterFor(clock, client, shown, { random: () => 0, retries: 3 }).request("video", {}, "2");
  await clock.advance(1000 + 1000 + 2000 + 3000);
  assert.equal(calls, 4);
  assert.deepEqual(shown, [{ text: "Price unavailable: Too many requests.", over: false, error: true }]);
  assert.equal(clock.pending, 0);
});

test("other errors are not retried", async () => {
  const clock = virtualClock();
  const shown = [];
  let calls = 0;
  const client = { estimate: async () => (calls += 1, Promise.reject(Object.assign(new Error("Bad key."), { code: "key_invalid" }))) };
  updaterFor(clock, client, shown).request("video", {}, "2");
  await clock.advance(30_000);
  assert.equal(calls, 1);
  assert.deepEqual(shown, [{ text: "Price unavailable: Bad key.", over: false, error: true }]);
});

test("a newer request replaces a retry that is waiting", async () => {
  const clock = virtualClock();
  const shown = [];
  const asked = [];
  const client = {
    estimate: async (kind, body) => {
      asked.push(body.n);
      if (body.n === 1) throw refused();
      return PRICE;
    },
  };
  const updater = updaterFor(clock, client, shown, { random: () => 0 });
  updater.request("video", { n: 1 }, "2");
  await clock.advance(1000);
  assert.equal(clock.pending, 1);
  updater.request("video", { n: 2 }, "2");
  await clock.advance(30_000);
  assert.deepEqual(asked, [1, 2]);
  assert.deepEqual(shown, [{ text: "≈ $0.66", over: false, error: false }]);
});

test("an answer to an older question is never shown, even when it is a refusal", async () => {
  const clock = virtualClock();
  const shown = [];
  const gates = [];
  const client = { estimate: () => new Promise((resolve, reject) => gates.push({ resolve, reject })) };
  const updater = updaterFor(clock, client, shown);
  updater.request("video", { n: 1 }, "2");
  await clock.advance(1000);
  updater.request("video", { n: 2 }, "2");
  await clock.advance(1000);
  gates[1].resolve(PRICE);
  await settle();
  gates[0].reject(refused());
  await settle();
  await clock.advance(30_000);
  assert.equal(gates.length, 2);
  assert.deepEqual(shown, [{ text: "≈ $0.66", over: false, error: false }]);
});

test("dispose stops the timer, ignores an answer in flight and ignores later requests", async () => {
  const clock = virtualClock();
  const shown = [];
  const asked = [];
  const gates = [];
  const client = { estimate: (kind, body) => (asked.push(body.n), new Promise((resolve) => gates.push(resolve))) };
  const updater = updaterFor(clock, client, shown);
  updater.request("video", { n: 1 }, "2");
  updater.dispose();
  assert.equal(clock.pending, 0);
  await clock.advance(5000);
  assert.deepEqual(asked, []);
  const second = updaterFor(clock, client, shown);
  second.request("video", { n: 2 }, "2");
  await clock.advance(1000);
  assert.deepEqual(asked, [2]);
  second.dispose();
  gates[0](PRICE);
  await settle();
  second.request("video", { n: 3 }, "2");
  assert.equal(clock.pending, 0);
  await clock.advance(5000);
  assert.deepEqual(asked, [2]);
  assert.deepEqual(shown, []);
});

test("a label callback that throws is not reported as a price error", async () => {
  const clock = virtualClock();
  const seen = [];
  const escaped = [];
  const updater = createPriceUpdater({
    client: { estimate: async () => PRICE }, t, cancel: clock.cancel,
    schedule: (fn, ms) => clock.schedule(() => fn().catch((error) => escaped.push(error.message)), ms),
    onText: (value) => {
      seen.push(value);
      throw new Error("the node is gone");
    },
  });
  updater.request("video", {}, "2");
  await clock.advance(1000);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].error, false);
  assert.deepEqual(escaped, ["the node is gone"]);
});

// The host lets 4 estimate calls per second through and 4 at a time (cuddler_host/routes.py).
function hostLimits(clock, latency = 300) {
  const recent = [];
  let inFlight = 0;
  return async () => {
    while (recent.length && clock.now - recent[0] >= 1000) recent.shift();
    if (recent.length >= 4 || inFlight >= 4) throw refused();
    recent.push(clock.now);
    inFlight += 1;
    await new Promise((resolve) => clock.schedule(resolve, latency));
    inFlight -= 1;
    return PRICE;
  };
}

function seeded(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let x = Math.imul(state ^ (state >>> 15), 1 | state);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

test("a workflow with 16 priced nodes loads without error labels", async () => {
  const clock = virtualClock();
  const random = seeded(7);
  const client = { estimate: hostLimits(clock) };
  const labels = Array.from({ length: 16 }, () => []);
  labels.forEach((list, node) => updaterFor(clock, client, list, { random }).request("video", { node }, "2.000000"));
  await clock.advance(120_000);
  for (const list of labels) {
    assert.equal(list.length, 1);
    assert.equal(list[0].error, false);
  }
});
