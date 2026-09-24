import { shortDistrictName } from "./district-name.js";
export { shortDistrictName } from "./district-name.js";

/* The district a visitor already chose, remembered across visits so the
   landing page can offer it back instead of asking them to search again. */

export const SAVED_DISTRICT_KEY = "osb:onboarding:district";
export const SAVED_DISTRICT_NAME_KEY = "osb:onboarding:district-name";

export function readSavedDistrict() {
  try {
    const slug = localStorage.getItem(SAVED_DISTRICT_KEY);
    if (!slug) return null;
    return { slug, name: localStorage.getItem(SAVED_DISTRICT_NAME_KEY) || "" };
  } catch {
    // Private browsing modes can throw on access; treat that as "no district".
    return null;
  }
}

export function rememberDistrict(slug, name) {
  try {
    if (slug) localStorage.setItem(SAVED_DISTRICT_KEY, slug);
    if (name) localStorage.setItem(SAVED_DISTRICT_NAME_KEY, name);
  } catch {
    // Storage being unavailable only costs the shortcut on the next visit.
  }
}

async function lookupName(slug) {
  const response = await fetch("/api/districts");
  if (!response.ok) return "";
  const payload = await response.json();
  return (payload.districts || []).find((item) => item.id === slug)?.name || "";
}

/* Renders the shortcut into every placeholder that asks for it — the hero and
   the desktop nav — all of which ship hidden so a first-time visitor never
   sees a flash of someone else's district. */
export async function renderSavedDistrictLink() {
  const links = [...document.querySelectorAll("[data-saved-district-link]")];
  if (!links.length) return;

  const saved = readSavedDistrict();
  if (!saved) return;

  let { name } = saved;
  if (!name) {
    // Anyone who onboarded before the name was stored still gets the shortcut.
    name = await lookupName(saved.slug).catch(() => "");
    if (name) rememberDistrict(saved.slug, name);
  }
  if (!name) return;

  const short = shortDistrictName(name);
  for (const link of links) {
    const label = link.querySelector("[data-saved-district-name]");
    if (!label) continue;
    // One text node, not "View" plus a span: the button is a flex row, and two
    // children would be spaced apart by its gap.
    label.textContent = `View ${short}`;
    link.href = `/feed.html?district=${encodeURIComponent(saved.slug)}`;
    link.setAttribute("aria-label", `View alerts for ${name}`);
    link.title = `View alerts for ${name}`;
    link.hidden = false;
  }
}
