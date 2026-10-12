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
