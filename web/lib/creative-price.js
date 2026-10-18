// Only the speech estimate requires user text; it goes to the same Platform API through the local backend.
const value = (node, name) => node.widgets?.find(w => w.name === name || w.name === `model.${name}`)?.value;

export function imageEstimate(node, choices) {
  const selected = choices[value(node, "model")];
  if (!selected) return null;
  const resolution = value(node, "resolution"), aspect_ratio = value(node, "aspect_ratio");
  if (!resolution || !aspect_ratio) return null;
  const count = node.inputs?.filter(input => /^model\.references\.image\d+$/.test(input.name) && input.link != null).length || 0;
  return { body: { model: selected.model, variant: selected.variant, resolution, aspect_ratio,
    reference_image_count: count }, cap: value(node, "max_usd") };
}

export function speechEstimate(node, choices) {
  const selected = choices[value(node, "model")];
  const text = value(node, "text"), voice_id = value(node, "voice_id");
  if (!selected || !text?.trim() || !voice_id) return null;
  let content = { text, voice_id };
  if (value(node, "second_speaker") === true) {
    const second = { text: value(node, "text_2"), voice_id: value(node, "voice_id_2") };
    if (!second.text?.trim() || !second.voice_id) return null;
    content = { dialogue: [content, second] };
  }
  return { body: { model: selected.model, output_format: "mp3", ...content }, cap: value(node, "max_usd") };
}

export function musicEstimate(node, choices) {
  const selected = choices[value(node, "model")];
  // Music is billed per generation, independent of prompt length or content.
  return selected ? { body: { model: selected.model, prompt: "Instrumental music.", output_format: "mp3" },
    cap: value(node, "max_usd") } : null;
}
