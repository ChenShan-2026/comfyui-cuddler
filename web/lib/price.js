// Price labels for Cuddler nodes: ask /cuddler/estimate about one second after the
// settings stop changing, and only ever show the answer to the latest question.
//
// Money never goes through a float here. An amount is read from its decimal text into whole
// micro-dollars (a BigInt), the unit the Platform and the Python side use, so 0.07 is 70000 and
// not 7.000000000000001.

const MICROS_PER_CENT = 10_000n;

// "2.5", "2.500000", 2.5 or "1.5e3" -> micro-dollars, cut off (not rounded) after the sixth decimal
// like the host's cap_from_widget. null when it is not a plain non-negative amount.
export function toMicros(value) {
  const text = typeof value === "number" ? String(value) : typeof value === "string" ? value.trim() : "";
  const match = /^(\d*)(?:\.(\d*))?(?:e([+-]?\d+))?$/i.exec(text);
  if (!match || (match[1] === "" && !match[2])) return null;
  const decimals = match[2] ?? "";
  const exponent = Number(match[3] ?? 0);
  if (Math.abs(exponent) > 30) return null;
  const digits = BigInt(match[1] + decimals);
  const places = 6 + exponent - decimals.length;
  return places >= 0 ? digits * 10n ** BigInt(places) : digits / 10n ** BigInt(-places);
}

function dollars(cents) {
  const padded = cents.toString().padStart(3, "0");
  return `$${padded.slice(0, -2)}.${padded.slice(-2)}`;
}

// The most a job can be charged (hold_usd), rounded UP to whole cents so it is never shown smaller
// than it is. For amounts of a cent or more this is what the host's display() writes in its
// over_cap message.
export function holdDisplay(holdUsd) {
  const micros = toMicros(holdUsd);
  return micros === null ? null : dollars((micros + MICROS_PER_CENT - 1n) / MICROS_PER_CENT);
}

// The node's limit, rounded DOWN to whole cents. Next to a hold rounded up, this means the label
// can never read "up to $2.01, above the $2.01 limit".
export function capDisplay(capUsd) {
  const micros = toMicros(capUsd);
  return micros === null ? null : dollars(micros / MICROS_PER_CENT);
}

function describeQuote(quote, capUsd, t) {
  const hold = toMicros(quote.hold_usd);
  const cap = toMicros(capUsd);
  // The test the host makes before it submits a job (hold_usd > cap), in micro-dollars.
  if (hold !== null && cap !== null && hold > cap) {
    const text = t("overCap", { amount: holdDisplay(quote.hold_usd), cap: capDisplay(capUsd) });
    return { text, over: true, error: false };
  }
  return { text: t("price", { amount: quote.display }), over: false, error: false };
}

export function createPriceUpdater({
  client, t, onText,
  delay = 1000,       // quiet time after the last change before asking
  retries = 4,        // extra asks when the host answers "too many requests"
  retryDelay = 1000,  // first wait before asking again; later waits grow
  schedule = setTimeout, cancel = clearTimeout, random = Math.random,
}) {
  let timer = null;
  let latest = 0;
  let disposed = false;

  const clear = () => {
    if (timer !== null) cancel(timer);
    timer = null;
  };

  const ask = async (kind, body, capUsd, ticket, retry) => {
    timer = null;
    let shown;
    try {
      shown = describeQuote(await client.estimate(kind, body), capUsd, t);
    } catch (error) {
      if (ticket !== latest) return;
      if (error?.code === "rate_limited" && retry < retries) {
        // A workflow with many priced nodes asks for all its prices at once and the host lets only a few
        // through per second. Wait a little longer each time, at a random moment so the nodes do not all
        // come back together. A newer request() or dispose() cancels this wait.
        const wait = retryDelay * (retry + 1) * (1 + random());
        timer = schedule(() => ask(kind, body, capUsd, ticket, retry + 1), wait);
        return;
      }
      const text = error?.code === "key_missing" ? t("priceNeedsKey") : t("priceError", { message: error?.message });
      shown = { text, over: false, error: true };
    }
    // Outside the try: a label callback that throws must not be reported as a price error.
    if (ticket === latest) onText(shown);
  };

  return {
    request(kind, body, capUsd) {
      if (disposed) return;
      clear();
      const ticket = ++latest;
      timer = schedule(() => ask(kind, body, capUsd, ticket, 0), delay);
    },
    // Call when the node is removed: no timer fires afterwards and no answer still in flight is shown.
    dispose() {
      disposed = true;
      latest += 1;
      clear();
    },
  };
}
