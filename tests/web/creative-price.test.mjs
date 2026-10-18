import assert from "node:assert/strict";
import test from "node:test";
import { imageEstimate, musicEstimate, speechEstimate } from "../../web/lib/creative-price.js";

const node = (widgets, inputs = []) => ({ inputs, widgets: Object.entries(widgets).map(([name, value]) => ({ name, value })) });

test("image estimate counts connected references and excludes prompt, URLs and seed", () => {
  const n = node({ model: "image", "model.resolution": "1K", "model.aspect_ratio": "1:1", max_usd: 2,
    prompt: "private", seed: 1 }, [{ name: "model.references.image0", link: 4 }, { name: "model.references.image1", link: null }]);
  assert.deepEqual(imageEstimate(n, { image: { model: "seedream-5", mode: "reference", variant: "default" } }),
    { body: { model: "seedream-5", variant: "default", resolution: "1K", aspect_ratio: "1:1", reference_image_count: 1 }, cap: 2 });
});

test("speech uses both speakers only when explicitly selected", () => {
  const n = node({ model: "speech", "model.text": "你好🙂", "model.voice_id": "voice_a", "model.second_speaker": true,
    "model.text_2": "Hello", "model.voice_id_2": "voice_b", max_usd: 2 });
  assert.deepEqual(speechEstimate(n, { speech: { model: "elevenlabs-v4" } }).body.dialogue,
    [{ text: "你好🙂", voice_id: "voice_a" }, { text: "Hello", voice_id: "voice_b" }]);
  n.widgets.find(w => w.name === "model.text_2").value = "";
  assert.equal(speechEstimate(n, { speech: { model: "elevenlabs-v4" } }), null);
});

test("fixed-price music estimate never sends the user's prompt", () => {
  const n = node({ model: "music", prompt: "private", max_usd: 2 });
  assert.equal(musicEstimate(n, { music: { model: "lyria-3" } }).body.prompt, "Instrumental music.");
});
