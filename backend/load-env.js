/* Populate process.env from .env before anything else evaluates.
 *
 * This must stay the FIRST import of the entry point, and it has to be its own
 * module rather than a function call in server.js.
 *
 * ES module imports are hoisted: every imported module is fully evaluated
 * before the first statement of the importing module runs. So a loadDotEnv()
 * call in the body of server.js executed *after* env.js had already read
 * process.env.APP_ENV and after stateDir had already read process.env.DATA_DIR.
 * Both silently fell back to their defaults, which meant .env was useless for
 * exactly the two variables that decide which database tables get written and
 * where generated state lands. Only real environment variables — systemd's
 * EnvironmentFile — took effect, so the deployed services were correct and
 * local development quietly ran as production.
 *
 * Real environment variables win for the implicit repository .env. When an
 * ENV_FILE is explicitly selected, keys in that file are authoritative. That
 * prevents inherited credentials for one deployment environment from silently
 * overriding a command that explicitly selected another environment.
 */
import { readFileSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

/* ENV_FILE selects an alternate file, which is how server-side tooling reads
 * /etc/ourschoolboard/<env>.env.
 *
 * It has to be an environment variable rather than a command-line flag for the
 * same hoisting reason this module exists: argv is only readable once the
 * program body runs, by which point env.js has already decided the table
 * prefix.
 *
 * These files are also read by systemd's EnvironmentFile, which is NOT a shell.
 * `MAIL_FROM=ourschoolboard alerts <alerts@ourschoolboard.org>` is perfectly
 * valid there but blows up under `. file` in bash, where `<` is a redirect —
 * so parse them here instead of shelling out, and keep the two readers
 * agreeing on what the file means.
 */
const envPath = process.env.ENV_FILE
  ? path.resolve(process.env.ENV_FILE)
  : path.resolve(here, "..", ".env");

/* An explicitly requested env file that cannot be read is a misconfiguration,
 * not an absence. existsSync returns false for a permission error just as it
 * does for a missing file, so without this an unreadable /etc/... file would
 * silently yield an app with no DATABASE_URL — which is how this first failed.
 * The implicit ./.env stays optional: not having one is normal.
 */
if (process.env.ENV_FILE) {
  try {
    readFileSync(envPath, "utf8");
  } catch (error) {
    throw new Error(`ENV_FILE=${envPath} could not be read: ${error.code || error.message}`);
  }
}

if (existsSync(envPath)) {
  for (const line of readFileSync(envPath, "utf8").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const index = trimmed.indexOf("=");
    const key = trimmed.slice(0, index).trim();
    let value = trimmed.slice(index + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (key && (process.env.ENV_FILE || process.env[key] === undefined)) {
      process.env[key] = value;
    }
  }
}
