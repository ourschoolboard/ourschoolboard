import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

const read = (name) => readFile(new URL(`../${name}`, import.meta.url), "utf8");

test("landing, feed, alert, and public journalist surfaces are present", async () => {
  const [landing, feed, alert, journalists, data] = await Promise.all([
    read("frontend/index.html"), read("frontend/feed.html"), read("frontend/alert.html"),
    read("frontend/journalists.html"), read("frontend/journalist-data.html")
  ]);
  assert.match(landing, /Free, nonprofit, and open-source/);
  assert.match(feed, /Your alert feed/);
  assert.match(alert, /Official record/);
  assert.match(journalists, /OurSchool for Journalists/);
  assert.match(data, /What school boards are discussing/);
});

test("excluded interactive and password-gated experiences are absent", async () => {
  const files = await readdir(new URL("../frontend", import.meta.url));
  const source = await Promise.all(files.filter((file) => /\.(?:html|js)$/.test(file)).map((file) => read(`frontend/${file}`)));
  const joined = source.join("\n");
  assert.doesNotMatch(joined, /journalist-password|journalist-chat|\/api\/comments|\/api\/subscription/);
  assert.ok(!files.includes("district.html"));
  assert.ok(!files.includes("journalist-investigation.html"));
  assert.ok(!files.includes("journalist-money.html"));
});

test("the public server exposes only read-only APIs", async () => {
  const server = await read("backend/server.js");
  assert.match(server, /\/api\/feed/);
  assert.match(server, /\/api\/alert/);
  assert.match(server, /\/api\/journalists\/alerts/);
  assert.doesNotMatch(server, /req\.method === "POST"/);
  assert.doesNotMatch(server, /comments|subscriptions|modal/i);
});

test("repository starts with one current-state migration", async () => {
  const files = await readdir(new URL("../migrations", import.meta.url));
  assert.deepEqual(files.sort(), ["0001_baseline.sql"]);
});
