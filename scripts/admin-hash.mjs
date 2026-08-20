// Turn a password into the value ADMIN_PASSWORD_HASH wants.
//
//   node scripts/admin-hash.mjs 'the password'
//
// Read from argv rather than prompted because this is run once, by hand. Note that
// it will land in your shell history: use a leading space if your shell is
// configured to skip those, or change the password afterwards.

import { randomBytes, scryptSync } from "node:crypto";

const password = process.argv[2];
if (!password || password.length < 12) {
  console.error(
    "Usage: node scripts/admin-hash.mjs '<password>'\n" +
      "At least 12 characters — this is the only thing between the internet and\n" +
      "every subscriber's filter.",
  );
  process.exit(2);
}

const salt = randomBytes(16);
const key = scryptSync(password, salt, 64, { N: 16384, r: 8, p: 1 });

console.log("\nADMIN_PASSWORD_HASH=" + `scrypt$${salt.toString("hex")}$${key.toString("hex")}`);
console.log("ADMIN_SESSION_SECRET=" + randomBytes(32).toString("hex"));
console.log(
  "\nBoth go in Vercel (production). The hash is not the password — you cannot\n" +
    "read the password back out of it, so keep the password somewhere yourself.\n",
);
