import assert from "node:assert/strict";
import test from "node:test";

import { createClient } from "../../web/lib/client.js";

function fakeFetch(responses) {
  const calls = [];
  const fetchApi = async (path, init = {}) => {
    calls.push({ path, init });
    const [status, body] = responses.shift();
    return { ok: status >= 200 && status < 300, status, json: async () => body };
  };
  return { calls, fetchApi };
}

test("talks to the plugin routes with JSON", async () => {
  const { calls, fetchApi } = fakeFetch([[200, { key: { source: "file" } }], [200, { display: "$0.66" }]]);
  const client = createClient(fetchApi);
  await client.saveKey("cud_sk_live_x");
  const quote = await client.estimate("video", { model: "seedance-2.5" });
  assert.equal(quote.display, "$0.66");
  assert.equal(calls[0].path, "/cuddler/key");
  assert.equal(calls[0].init.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].init.body), { key: "cud_sk_live_x" });
  assert.deepEqual(JSON.parse(calls[1].init.body), { kind: "video", body: { model: "seedance-2.5" } });
});

test("status asks for the balance only when told to", async () => {
  const { calls, fetchApi } = fakeFetch([[200, {}], [200, {}]]);
  const client = createClient(fetchApi);
  await client.status();
  await client.status({ balance: true });
  assert.deepEqual(calls.map((call) => call.path), ["/cuddler/status", "/cuddler/status?balance=1"]);
});

test("errors carry the server's code and message", async () => {
  const { fetchApi } = fakeFetch([[400, { code: "key_missing", message: "Add your key." }]]);
  await assert.rejects(createClient(fetchApi).estimate("video", {}), (error) => {
    assert.equal(error.code, "key_missing");
    assert.equal(error.message, "Add your key.");
    return true;
  });
});
