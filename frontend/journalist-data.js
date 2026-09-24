const TOPICS = {
  budget: ["Budget & finance", "#0f7c72"], staffing: ["Staffing & leadership", "#bd7c12"],
  facilities: ["Facilities", "#7558a6"], curriculum: ["Curriculum", "#3f7ca6"],
  policy: ["Policy", "#4a6b96"], risk: ["Risk & oversight", "#c14a38"], other: ["Other", "#8a93a0"]
};
const params = new URLSearchParams(location.search);
const stateFilter = document.getElementById("state-filter");
const number = new Intl.NumberFormat("en-US");

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function shortDate(value) {
  if (!value) return "No dated alerts";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
    .format(new Date(`${value}T12:00:00Z`));
}

function populateStates(states, selected) {
  stateFilter.replaceChildren(new Option("All states", ""));
  for (const item of states) {
    stateFilter.append(new Option(`${item.state} · ${item.coveragePct}% coverage · ${number.format(item.alerts)} alerts`, item.state));
  }
  stateFilter.value = selected || "";
}

function renderMetrics(data) {
  document.getElementById("metric-alerts").textContent = number.format(data.summary.alerts);
  document.getElementById("metric-districts").textContent = number.format(data.summary.districts);
  document.getElementById("metric-coverage").textContent = `${data.summary.coveragePct}%`;
  document.getElementById("metric-coverage-detail").textContent =
    `${number.format(data.summary.coveredDistricts)} of ${number.format(data.summary.totalDistricts)} tracked districts`;
  document.getElementById("metric-range").textContent = data.summary.firstDate
    ? `${shortDate(data.summary.firstDate)} – ${shortDate(data.summary.lastDate)}` : "No dated alerts";
}

function renderCoverage(items) {
  const host = document.getElementById("state-coverage");
  host.replaceChildren();
  for (const item of items) {
    const link = el("a", "coverage-state");
    link.href = `?state=${encodeURIComponent(item.state)}`;
    const heading = el("span", "coverage-state-heading");
    heading.append(el("strong", "", item.state), el("b", "", `${item.coveragePct}%`));
    const track = el("span", "coverage-state-track");
    const fill = el("i", "coverage-state-fill");
    fill.style.width = `${Math.max(item.coveredDistricts ? 1.5 : 0, item.coveragePct)}%`;
    track.append(fill);
    link.append(heading, track, el("small", "", `${item.coveredDistricts} of ${item.totalDistricts} tracked districts`));
    host.append(link);
  }
}

function renderTopics(items) {
  const host = document.getElementById("topic-chart");
  host.replaceChildren();
  const max = Math.max(0, ...items.map((item) => item.alerts));
  for (const item of items) {
    const row = el("div", "topic-row");
    row.append(el("span", "topic-name", (TOPICS[item.topic] || TOPICS.other)[0]));
    const track = el("span", "topic-track");
    const fill = el("span", "topic-fill");
    fill.style.width = `${max ? Math.max(1.5, item.alerts / max * 100) : 0}%`;
    fill.style.setProperty("--topic-color", (TOPICS[item.topic] || TOPICS.other)[1]);
    track.append(fill);
    row.append(track, el("span", "topic-count", number.format(item.alerts)));
    host.append(row);
  }
}

function renderDistricts(items) {
  const host = document.getElementById("district-ranking");
  host.replaceChildren();
  items.forEach((item, index) => {
    const row = el("li");
    row.append(el("span", "district-rank", String(index + 1).padStart(2, "0")));
    const link = el("a", "", `${item.name}${item.state ? `, ${item.state}` : ""}`);
    link.href = `/feed?district=${encodeURIComponent(item.slug)}`;
    row.append(link, el("strong", "", number.format(item.alerts)));
    host.append(row);
  });
}

function renderTrend(items) {
  const host = document.getElementById("trend-chart");
  host.replaceChildren();
  const max = Math.max(1, ...items.map((item) => item.alerts));
  for (const item of items) {
    const bar = el("div", "trend-bar");
    bar.style.height = `${Math.max(3, item.alerts / max * 100)}%`;
    bar.style.setProperty("--topic-color", (TOPICS[item.topic] || TOPICS.other)[1]);
    bar.title = `${item.month}: ${item.alerts} ${(TOPICS[item.topic] || TOPICS.other)[0]} alerts`;
    host.append(bar);
  }
}

function renderRecent(items) {
  const host = document.getElementById("recent-alerts");
  host.replaceChildren();
  for (const item of items) {
    const link = el("a", "recent-alert");
    link.href = `/alert?district=${encodeURIComponent(item.district.id)}&id=${encodeURIComponent(item.id)}`;
    link.append(el("strong", "", item.title),
      el("span", "", `${item.district.name}${item.district.state ? `, ${item.district.state}` : ""} · ${shortDate(item.date)}`));
    host.append(link);
  }
}

async function load() {
  const selected = String(params.get("state") || "").toUpperCase();
  const response = await fetch(`/api/journalists/alerts${selected ? `?state=${encodeURIComponent(selected)}` : ""}`);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Data returned ${response.status}`);
  populateStates(data.states || [], selected);
  renderMetrics(data);
  renderCoverage(data.states || []);
  renderTopics(data.topics || []);
  renderDistricts(data.districts || []);
  renderTrend(data.months || []);
  renderRecent(data.recent || []);
  document.getElementById("data-status").hidden = true;
  document.getElementById("data-content").hidden = false;
}

stateFilter.addEventListener("change", () => {
  const next = new URL(location.href);
  if (stateFilter.value) next.searchParams.set("state", stateFilter.value);
  else next.searchParams.delete("state");
  location.assign(next);
});

load().catch((error) => {
  const status = document.getElementById("data-status");
  status.textContent = `Could not load journalist data: ${error.message}`;
  status.classList.add("error");
});
