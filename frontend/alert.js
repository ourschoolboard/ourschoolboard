import { withGlossary } from "/glossary.js";

const params = new URLSearchParams(location.search);
const alertId = params.get("id") || "";
const district = params.get("district") || "";

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function block(title, body) {
  const section = el("section", "detail-block");
  section.append(el("h2", "", title));
  const paragraph = el("p");
  paragraph.append(withGlossary(body || ""));
  section.append(paragraph);
  return section;
}

function formatDate(value) {
  if (!value) return "Date not listed";
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })
    .format(new Date(`${value}T00:00:00Z`));
}

function render({ district: record, alert, related = [], sourceDocument }) {
  document.title = `${alert.title} · ourschoolboard.org`;
  document.getElementById("district-name").textContent = record.name;
  const main = document.getElementById("detail");
  main.replaceChildren();
  main.classList.add(`sev-${alert.severity || "medium"}`);
  const back = el("a", "back", "← Back to feed");
  back.href = `/feed?district=${encodeURIComponent(district)}`;
  main.append(back);
  const tag = el("div", "title-row detail-tag-row");
  tag.append(el("span", "detail-tag", alert.categoryLabel || alert.topic || "Update"));
  main.append(tag, el("h1", "detail-title", alert.title));
  const facts = el("div", "detail-facts");
  facts.append(el("span", "", record.name), el("span", "", formatDate(alert.date)),
    el("span", `status-pill ${alert.status || "published"}`, alert.status || "published"));
  main.append(facts, block("What happened", alert.summary),
    block("Why it matters to families", alert.whyItMatters), block("The evidence", alert.evidence));
  if (alert.caveat) main.append(block("What this does not tell us", alert.caveat));
  const source = el("section", "detail-block");
  source.append(el("h2", "", "The source"));
  const link = el("a", "source-card");
  link.href = alert.sourceUrl;
  link.target = "_blank";
  link.rel = "noreferrer";
  link.append(el("strong", "", alert.sourceTitle || "Official source"));
  if (alert.sourceHost) link.append(el("span", "", alert.sourceHost));
  if (sourceDocument?.textCharacters) link.append(el("span", "", `${sourceDocument.textCharacters.toLocaleString()} extracted characters`));
  source.append(link);
  main.append(source);
  if (related.length) {
    const section = el("section", "detail-block");
    section.append(el("h2", "", "Related in this district"));
    const list = el("div", "related");
    for (const item of related) {
      const relatedLink = el("a");
      relatedLink.href = `/alert?id=${encodeURIComponent(item.id)}&district=${encodeURIComponent(district)}`;
      relatedLink.append(el("span", "related-title", item.title), el("span", "related-date", formatDate(item.date)));
      list.append(relatedLink);
    }
    section.append(list);
    main.append(section);
  }
}

async function init() {
  if (!district || !alertId) throw new Error("Choose an alert and district.");
  const response = await fetch(`/api/alert?id=${encodeURIComponent(alertId)}&district=${encodeURIComponent(district)}`);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Alert returned ${response.status}`);
  render(payload);
}

init().catch((error) => {
  document.getElementById("detail").replaceChildren(el("p", "empty", error.message));
});
