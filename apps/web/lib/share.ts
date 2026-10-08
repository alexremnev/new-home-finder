/**
 * A delivery share, said as a count rather than as a percentage.
 *
 * "20%" is arithmetic somebody has to do before it means anything, and it is
 * arithmetic about the thing they are being told they cannot have. "1 in 5" is
 * the same fact with the sum already done.
 *
 * Only where it comes out whole. A share of 30 is "3 in 10" at best, and
 * rounding it to "1 in 3" would overstate what is delivered — so anything that
 * does not divide keeps the percentage it arrived as.
 *
 * The twin of `share_words` in `worker/notify/fields.py`. The bot and the site
 * describe the same share to the same person, and two wordings for one number
 * is the drift the share being read from `plans` was meant to end.
 */
export function shareWords(share: number): string {
  if (Number.isInteger(share) && share > 0 && share < 100 && 100 % share === 0) {
    return `1 in ${100 / share}`;
  }
  return `${share}%`;
}
