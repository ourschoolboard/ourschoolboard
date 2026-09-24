/* Which deployment this process is, and everything that follows from it.
 *
 * One box runs production and staging side by side. The two things that must
 * never bleed across that line are the database rows and the outbound email —
 * a staging deploy writing a real waitlist signup, or mailing the real alert
 * address, would be indistinguishable from the real thing after the fact.
 *
 * Both are decided here rather than at each call site, so adding a query or a
 * notification cannot accidentally opt out of the separation.
 */

const configuredEnvironment = process.env.APP_ENV;
if (configuredEnvironment && !["production", "staging"].includes(configuredEnvironment)) {
  throw new Error(`APP_ENV must be "production" or "staging", got ${JSON.stringify(configuredEnvironment)}`);
}
if (process.env.NODE_ENV === "production" && !configuredEnvironment) {
  throw new Error("APP_ENV is required when NODE_ENV=production");
}
export const APP_ENV = configuredEnvironment || "production";
export const IS_STAGING = APP_ENV === "staging";

// Staging tables are prefixed rather than living in a separate Postgres schema.
// A schema plus search_path would need no query changes at all, but a visible
// `staging_` in the table name is far harder to misread when you are looking at
// a row and deciding whether it is real.
export const TABLE_PREFIX = IS_STAGING ? "staging_" : "";

/** Physical table name for a logical one. */
export const t = (name) => `${TABLE_PREFIX}${name}`;

/** Apply the prefix to schema.sql, which is written with {{p}} placeholders. */
export const applyPrefix = (sql) => sql.replaceAll("{{p}}", TABLE_PREFIX);

/* Whether this deployment drives scrapers on a timer.
 *
 * Scraping is the third thing that must not bleed across the prod/staging
 * line, and the only one that reaches outside this box: it makes requests to
 * district websites on a schedule. Two deployments polling scrape_schedules
 * would double that outbound traffic and race each other for due rows, so
 * exactly one environment owns it, and that environment is production.
 *
 * This deliberately reads process.env.APP_ENV rather than the APP_ENV export
 * above. APP_ENV falls back to "production" when unset, which is the safe
 * default for mail and table names — but the identical default would turn any
 * developer's `node backend/server.js` into a second production scraper
 * worker. Requiring the variable to be set explicitly keeps "unset" meaning
 * "do not scrape", while leaving the deployed units unambiguous.
 *
 * SCRAPE_CRON_ENABLED still overrides in both directions: "true" to exercise
 * the scheduler in staging or a test, "false" to stand production down without
 * editing a unit file.
 */
export function scrapersActive(
  environment = process.env.APP_ENV,
  override = process.env.SCRAPE_CRON_ENABLED
) {
  if (override === "true") return true;
  if (override === "false") return false;
  return environment === "production";
}

/* Route staging mail to a plus-address on the same mailbox. Gmail delivers
 * user+tag@ to user@, so staging notifications are still received and still
 * filterable, without a second inbox and without ever pretending to be prod.
 */
export function envAlertEmail(configured) {
  const address = (configured || "").trim();
  if (!IS_STAGING || !address) return address;
  if (address.includes("+")) return address; // already tagged; leave it alone
  const at = address.lastIndexOf("@");
  if (at < 1) return address;
  return `${address.slice(0, at)}+staging${address.slice(at)}`;
}

/** Prefix the subject line so a staging email is obvious in the inbox. */
export const envSubject = (subject) => (IS_STAGING ? `[staging] ${subject}` : subject);

/* Decide where a message may actually be delivered.
 *
 * envAlertEmail() plus-tags the operator's own address, which is fine for the
 * one address we own. It is NOT a containment mechanism for anyone else's:
 * Gmail, Outlook and Yahoo all ignore the +tag and deliver
 * `subscriber+staging@gmail.com` straight to `subscriber@gmail.com`. Tagging a
 * subscriber list in staging would mail every real person on it.
 *
 * So in staging anything that is not the operator's own address is redirected
 * to the operator instead, with the intended recipient preserved in the subject
 * and an X-Original-To header. Staging can then exercise the real send path —
 * including suppression and unsubscribe — without a single message reaching a
 * real subscriber.
 */
export function routeRecipient(to) {
  const original = String(to || "").trim();
  if (!IS_STAGING || !original) return { deliverTo: original, original, redirected: false };

  const operator = (process.env.ALERT_EMAIL || "").trim().toLowerCase();
  const base = original.toLowerCase().replace(/\+[^@]*@/, "@");
  if (operator && base === operator.replace(/\+[^@]*@/, "@")) {
    // The operator's own address: tag it so staging mail is filterable.
    return { deliverTo: envAlertEmail(original), original, redirected: false };
  }
  return {
    deliverTo: envAlertEmail(operator) || operator,
    original,
    redirected: true
  };
}
