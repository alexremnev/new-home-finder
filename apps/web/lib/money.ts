/**
 * Pence as somebody reads them: "£19.99", but "£20" rather than "£20.00".
 *
 * One definition, because there were six and one of them rounded. The admin's
 * Payments page formatted with `maximumFractionDigits: 0`, so a £19.99 plan was
 * displayed as £20 — a price that was never charged, on the one page whose job
 * is to say what was.
 *
 * Trailing ".00" is dropped because a price list of whole pounds reads as a
 * rounding artefact with it; pence are never dropped, because they are the
 * price.
 */
export function pounds(pence: number): string {
  const sign = pence < 0 ? "−" : "";
  const value = Math.abs(pence) / 100;
  return (
    sign +
    "£" +
    value.toLocaleString("en-GB", {
      minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
      maximumFractionDigits: 2,
    })
  );
}

/**
 * Millionths of a US dollar as somebody reads them: "$19.80".
 *
 * Dollars and not pounds because that is the currency Meta bills WhatsApp in,
 * and converting would mean picking an exchange rate and then quoting it as a
 * cost. The revenue beside it on the same page stays in pounds for the same
 * reason — it is the currency it arrives in. See 0063.
 *
 * Micros in, because a message costs a fraction of a penny ($0.022) and
 * rounding at the message would lose most of the bill. Rounded only here, at
 * the total.
 *
 * Always two decimal places, unlike `pounds`: this is an amount owed rather
 * than a price, and a whole number of dollars is a coincidence rather than
 * something worth reading as one.
 */
export function dollars(micros: number): string {
  const value = micros / 1_000_000;
  const sign = value < 0 ? "\u2212" : "";
  return (
    sign +
    "$" +
    Math.abs(value).toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  );
}

/**
 * A per-message rate, written out at the precision it actually has: "$0.022".
 *
 * `dollars` rounds to the cent, which is right for a total and wrong for a
 * rate — $0.022 printed to two places is $0.02, a tenth out and confidently
 * so. Three places, and trailing zeroes kept, because a rate quoted as
 * "$0.06" invites the reader to multiply by it and get a different answer
 * from the one above it.
 */
export function rate(micros: number): string {
  return "$" + (micros / 1_000_000).toFixed(4).replace(/0$/, "");
}

/**
 * The WhatsApp bill as the sum it is: "1,234 × $0.022 + 7 × $0.0635 = $27.59".
 *
 * Written out rather than asserted. Meta charges two prices on this number —
 * $0.022 for free-form text inside the 24-hour window and for the listing
 * template, $0.0635 for the marketing template that carries a payment button
 * — and a single dollar figure gives the reader no way to tell a quiet month
 * of alerts from a month that sent a lot of plan notices. See 0067.
 *
 * Takes counts and rates rather than the stamped total, so what it prints can
 * be checked against the total beside it. Where the two disagree, the total
 * is right and the difference is messages sent before a rate was set: the
 * price is stamped on each row at send time precisely so that setting one now
 * does not rewrite a month already billed. The caller says so — see
 * `wa_uncosted`.
 */
export function waFormula(
  service: number,
  marketing: number,
  serviceMicros: number,
  marketingMicros: number,
): string {
  const comma = (n: number) => n.toLocaleString("en-GB");
  const total = service * serviceMicros + marketing * marketingMicros;
  return (
    `${comma(service)} × ${rate(serviceMicros)} service` +
    ` + ${comma(marketing)} × ${rate(marketingMicros)} marketing` +
    ` = ${dollars(total)}`
  );
}
