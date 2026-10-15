// The "Cuddler" settings page. The key goes straight to this ComfyUI server's /cuddler/key
// route; it is never handed to ComfyUI's settings store, so it never lands in comfy.settings.json.
// Every string on the page is set with textContent, never as HTML: messages come from the server.

export const CONSOLE_URL = "https://platform.cuddler.ai/console";
// ComfyUI's theme variables, so the form follows the light and dark themes.
const FIELD = "background:var(--comfy-input-bg);color:var(--input-text);border:1px solid var(--border-color);"
  + "border-radius:4px;padding:4px 8px";

// What to show for a /cuddler/status answer. Pure, so it is tested without a browser.
export function describeStatus(status, t) {
  const key = status.key || {};
  const mode = key.mode === "test" ? t("modeTest") : t("modeLive");
  let keyText = t("keyNone");
  if (key.error) keyText = t(key.source === "env" ? "keyEnvError" : "keyError", { message: key.message || key.error });
  else if (key.source === "env") keyText = t("keyEnv", { last4: key.last4, mode });
  else if (key.source === "file") keyText = t("keyFile", { last4: key.last4, mode });
  let balanceText = "";
  if (status.balance) balanceText = t("balance", { amount: status.balance.available_display });
  else if (status.balance_error) balanceText = t("balanceError", { message: status.balance_error.message });
  return { keyText, balanceText, editable: key.source !== "env", removable: key.source === "file" };
}

function element(tag, attributes = {}, text = "") {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  if (text) node.textContent = text;
  return node;
}

export function renderSettingsPanel({ client, t }) {
  const root = element("div", { class: "cuddler-settings", "data-cuddler": "panel" });
  root.style.cssText = "display:flex;flex-direction:column;gap:8px;min-width:min(420px,100%)";
  const keyLine = element("div", { "data-cuddler": "status" });
  // Browsers ignore autocomplete="off" on password fields; "new-password" is what stops them offering a saved login here.
  const input = element("input", { type: "password", autocomplete: "new-password", spellcheck: "false",
    placeholder: t("placeholder"), "data-cuddler": "key" });
  input.style.cssText = `flex:1;min-width:0;${FIELD}`;
  const save = element("button", { type: "button", "data-cuddler": "save" }, t("save"));
  const remove = element("button", { type: "button", "data-cuddler": "remove" }, t("remove"));
  save.style.cssText = remove.style.cssText = `${FIELD};cursor:pointer`;
  const row = element("div");
  row.style.cssText = "display:flex;gap:6px;align-items:center";
  row.append(input, save, remove);
  const balanceLine = element("div", { "data-cuddler": "balance" });
  const links = element("div");
  const keysLink = element("a", { href: `${CONSOLE_URL}/keys`, target: "_blank", rel: "noopener" }, t("createKey"));
  const billingLink = element("a", { href: `${CONSOLE_URL}/billing`, target: "_blank", rel: "noopener" }, t("addFunds"));
  links.append(keysLink, document.createTextNode(" · "), billingLink);
  const message = element("div", { role: "status", "data-cuddler": "message" });
  root.append(keyLine, row, balanceLine, links, message);

  let view = { editable: true, removable: true }; // what the last status that could be read allowed
  let busy = false;
  let newest = 0; // only the newest status request may change the page

  const sync = () => {
    input.disabled = !view.editable;
    save.disabled = !view.editable || busy;
    remove.disabled = !view.removable || busy;
    for (const control of [input, save, remove]) control.style.opacity = control.disabled ? "0.5" : "1";
  };
  const show = (status) => {
    view = describeStatus(status, t);
    keyLine.textContent = view.keyText;
    balanceLine.textContent = view.balanceText;
    if (!view.editable) message.textContent = t("envLocked");
    sync();
  };
  // Never rejects. What a save or remove reported stays in `message`; a status that cannot be read goes on the status line.
  const refresh = async () => {
    const mine = ++newest;
    try {
      const status = await client.status({ balance: true });
      if (mine === newest) show(status);
    } catch (error) {
      if (mine === newest) {
        keyLine.textContent = error.message;
        balanceLine.textContent = "";
      }
    }
  };
  const act = async (work, done) => {
    message.textContent = t("working");
    busy = true;
    sync();
    try {
      await work();
      message.textContent = t(done);
    } catch (error) {
      message.textContent = error.message;
    }
    await refresh();
    busy = false;
    sync();
  };
  save.addEventListener("click", () =>
    act(async () => {
      await client.saveKey(input.value);
      input.value = "";
    }, "saved"));
  remove.addEventListener("click", () => act(() => client.clearKey(), "removed"));
  refresh();
  return root;
}
