import { withGlossary } from "/glossary.js";

const params = new URLSearchParams(location.search);
const district = params.get("district") || "";
const state = { feed: null, query: "", categories: new Set() };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatDate(value) {
  if (!value) return "Date not listed";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
    .format(new Date(`${value}T00:00:00Z`));
}

function visibleAlerts() {
  const needle = state.query.trim().toLowerCase();
  return state.feed.alerts.filter((alert) =>
    (!state.categories.size || state.categories.has(alert.topic)) &&
    (!needle || [alert.title, alert.summary, alert.whyItMatters, alert.categoryLabel]
      .filter(Boolean).join(" ").toLowerCase().includes(needle))
  );
}

function syncCategories() {
  if (state.categories.size) params.set("category", [...state.categories].join(","));
  else params.delete("category");
  history.replaceState(null, "", `${location.pathname}?${params}`);
}

function renderChips() {
  const host = document.getElementById("chips");
  host.replaceChildren();
  const options = [{ id: "", label: "All", count: state.feed.alerts.length }, ...state.feed.categories];
  for (const option of options) {
    const button = el("button", "chip", `${option.label} ${option.count ?? 0}`);
    button.type = "button";
    const pressed = option.id === "" ? state.categories.size === 0 : state.categories.has(option.id);
    button.setAttribute("aria-pressed", String(pressed));
    button.addEventListener("click", () => {
      if (!option.id) state.categories.clear();
      else if (state.categories.has(option.id)) state.categories.delete(option.id);
      else state.categories.add(option.id);
      syncCategories();
      renderChips();
      renderList();
    });
    host.append(button);
  }
}

function renderList() {
  const alerts = visibleAlerts();
  document.getElementById("result-count").textContent = `${alerts.length} ${alerts.length === 1 ? "alert" : "alerts"}`;
  const host = document.getElementById("alert-list");
  host.replaceChildren();
  if (!alerts.length) {
    host.append(el("p", "empty", "No alerts match that search yet."));
    return;
  }
  for (const alert of alerts) {
    const card = el("a", `alert-card sev-${alert.severity || "medium"}`);
    card.href = `/alert?id=${encodeURIComponent(alert.id)}&district=${encodeURIComponent(district)}`;
    const meta = el("div", "alert-meta");
    meta.append(
      el("span", "alert-tag", alert.categoryLabel || alert.topic || "Update"),
      el("span", `status-pill ${alert.status || "published"}`, alert.status || "published"),
      el("span", "", formatDate(alert.date))
    );
    card.append(meta, el("h3", "", alert.title));
    const why = el("p", "alert-why");
    why.append(withGlossary(alert.whyItMatters || alert.summary || ""));
    card.append(why, el("span", "alert-src", alert.sourceType || "Official record"));
    host.append(card);
  }
}

async function init() {
  if (!district) throw new Error("Choose a district with ?district=.");
  const response = await fetch(`/api/feed?district=${encodeURIComponent(district)}`);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Feed returned ${response.status}`);
  state.feed = payload;
  const requested = String(params.get("category") || "").split(",").filter(Boolean);
  const valid = new Set(payload.categories.map((item) => item.id));
  state.categories = new Set(requested.filter((item) => valid.has(item)));
  document.getElementById("district-name").textContent = payload.district.name;
  document.getElementById("district-period").textContent = payload.period?.start
    ? `${formatDate(payload.period.start)} – ${formatDate(payload.period.end)}` : "Published alerts";
  document.getElementById("feed-sub").textContent =
    `${payload.summary?.documentsRead || payload.summary?.documents || 0} source documents reviewed.`;
  renderChips();
  renderList();
  document.getElementById("search").addEventListener("input", (event) => {
    state.query = event.target.value;
    renderList();
  });
}

init().catch((error) => {
  document.getElementById("alert-list").replaceChildren(el("p", "empty", error.message));
  document.getElementById("result-count").textContent = "";
});
