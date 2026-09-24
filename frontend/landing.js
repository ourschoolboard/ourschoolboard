/* Landing page: real alert examples, aggregate coverage stats, saved-district
   shortcuts, and light progressive enhancement. No content depends on JS. */

const FALLBACK = [
  { topic: "policy", location: "Weston, MA", date: "2026-01-05", cardTitle: "Phones stored in lockers all day starting fall 2026" },
  { topic: "risk", location: "West Contra Costa, CA", date: "2026-08-03", cardTitle: "Kennedy High will close due to PCE finding" },
  { topic: "staffing", location: "Walnut Creek, CA", date: "2026-06-08", cardTitle: "Walnut Creek appoints a new superintendent" }
];

const SCHOOL_SUGGESTIONS_KEY = "osb:school-suggestions:v1";
const FALLBACK_STATS = { documents: 171, minutes: 71, alerts: 38 };
const numberFormatter = new Intl.NumberFormat("en-US");
let latestStats = FALLBACK_STATS;

async function preloadSchoolSuggestions() {
  try {
    const response = await fetch("/api/schools/search");
    if (!response.ok) return;
    const payload = await response.json();
    sessionStorage.setItem(SCHOOL_SUGGESTIONS_KEY, JSON.stringify({
      savedAt: Date.now(),
      results: payload.results || []
    }));
  } catch {
    // Speculative work for the setup page should never disrupt the landing page.
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function shortDate(value) {
  try {
    return new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" })
      .format(new Date(`${value}T00:00:00Z`));
  } catch {
    return value;
  }
}

function cardLabel(alert) {
  return alert.cardTitle || alert.title || "Update";
}

function topicLabel(topic) {
  return ({ budget: "Budget", staffing: "Hires", policy: "Policy", risk: "Safety", facilities: "Facilities" })[topic] || "Update";
}

function alertMeta(alert) {
  const meta = el("div", "hero-alert-meta");
  meta.append(
    el("span", "alert-topic", topicLabel(alert.topic)),
    el("span", "", `${alert.location || alert.districtName || ""} · ${shortDate(alert.date)}`)
  );
  return meta;
}

function recentIcon(topic) {
  const icon = el("span", "recent-icon");
  icon.setAttribute("aria-hidden", "true");
  const paths = {
    risk: '<path d="M24 11l12.5 23h-25z" fill="currentColor" opacity=".85"/><rect x="22.6" y="20" width="2.8" height="8" rx="1.4" fill="#fff6e8"/><circle cx="24" cy="30.5" r="1.7" fill="#fff6e8"/>',
    staffing: '<g fill="currentColor" opacity=".85"><circle cx="24" cy="19" r="6.5"/><path d="M12 37a12 12 0 0124 0z"/></g>',
    policy: '<g fill="currentColor" opacity=".85"><rect x="14" y="12" width="20" height="24" rx="3"/><rect x="18" y="8" width="3" height="8" rx="1.5"/><rect x="27" y="8" width="3" height="8" rx="1.5"/></g><rect x="18" y="22" width="12" height="3" rx="1.5" fill="#fff6e8"/>',
    budget: '<g fill="currentColor" opacity=".85"><rect x="12" y="26" width="7" height="12" rx="2"/><rect x="22" y="19" width="7" height="19" rx="2"/><rect x="32" y="12" width="7" height="26" rx="2"/></g>',
    facilities: '<g fill="currentColor" opacity=".85"><path d="M9 38h30L35 19 24 10 13 19z"/><rect x="20" y="27" width="8" height="11" fill="#fff6e8"/></g>'
  };
  icon.innerHTML = `<svg viewBox="0 0 48 48">${paths[topic] || paths.policy}</svg>`;
  return icon;
}

function renderCluster(alerts, { animate = true } = {}) {
  const cluster = document.getElementById("float-cluster");
  if (!cluster) return;
  cluster.replaceChildren();
  const cards = alerts.slice(0, 3).map((alert) => {
    const card = el("article", `hero-alert${animate ? " is-entering" : ""} cat-${alert.topic || "policy"}`);
    card.append(alertMeta(alert), el("h3", "", cardLabel(alert)));
    cluster.append(card);
    return card;
  });
  if (!animate) return;
  const reveal = () => cards.forEach((card) => card.classList.remove("is-entering"));
  requestAnimationFrame(() => requestAnimationFrame(reveal));
  setTimeout(reveal, 600);
}

function renderRecentAlerts(alerts) {
  const grid = document.getElementById("recent-alert-grid");
  if (!grid || !alerts.length) return;
  grid.replaceChildren();
  const selected = alerts.slice(0, 4);
  [...selected, ...selected].forEach((alert, index) => {
    const topic = alert.topic || "policy";
    const card = el(alert.href ? "a" : "article", `recent-card cat-${topic}`);
    if (alert.href) card.href = alert.href;
    if (index >= selected.length) card.setAttribute("aria-hidden", "true");
    card.append(recentIcon(topic), alertMeta(alert), el("h3", "", cardLabel(alert)), el("p", "", alert.location || alert.districtName || ""));
    grid.append(card);
  });
}

function animateNumber(node, value, delay = 0) {
  if (!node) return;
  const target = Math.max(0, Math.round(Number(value) || 0));
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    node.textContent = numberFormatter.format(target);
    return;
  }

  const duration = 850;
  let startedAt;
  node.textContent = "0";
  const tick = (now) => {
    if (startedAt === undefined) startedAt = now + delay;
    const progress = Math.max(0, Math.min(1, (now - startedAt) / duration));
    const eased = 1 - ((1 - progress) ** 4);
    node.textContent = numberFormatter.format(Math.round(target * eased));
    if (progress < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function renderStats(summary) {
  if (!summary) return;
  latestStats = summary;
  ["documents", "minutes", "alerts"].forEach((key, index) => {
    animateNumber(document.querySelector(`[data-stat="${key}"]`), summary[key], index * 70);
  });
}

async function hydrate() {
  renderCluster(FALLBACK, { animate: false });
  renderRecentAlerts(FALLBACK.slice(1));
  try {
    const [allRes, statsRes] = await Promise.all([
      fetch("/api/highlights"),
      fetch("/api/stats")
    ]);
    if (allRes.ok) {
      const all = await allRes.json();
      if (all.alerts?.length) renderRecentAlerts(all.alerts);
    }
    renderStats(statsRes.ok ? await statsRes.json() : FALLBACK_STATS);
  } catch (error) {
    console.warn("Landing hydrate failed, using static content:", error);
    renderStats(FALLBACK_STATS);
  }
}

function bindWatchTabs() {
  const tabs = [...document.querySelectorAll(".watch-tab")];
  const contents = [...document.querySelectorAll("[data-watch-content]")];
  const panel = document.getElementById("watch-panel");
  let swapTimer;
  let arrivalTimer;
  let autoTimer;
  let current = 0;

  const scheduleAutoAdvance = () => {
    clearInterval(autoTimer);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    autoTimer = setInterval(() => activate((current + 1) % tabs.length), 7000);
  };

  const activate = (index, { resetTimer = false } = {}) => {
    if (index === current || !panel || !contents[index]) return;
    tabs.forEach((candidate, candidateIndex) => {
      const active = candidateIndex === index;
      candidate.classList.toggle("is-active", active);
      candidate.setAttribute("aria-selected", String(active));
    });
    clearTimeout(swapTimer);
    clearTimeout(arrivalTimer);
    panel.classList.remove("is-arriving");
    panel.classList.add("is-swapping");
    swapTimer = setTimeout(() => {
      contents[current].classList.remove("is-active");
      contents[current].hidden = true;
      contents[index].hidden = false;
      contents[index].classList.add("is-active");
      current = index;
      panel.dataset.watchStep = String(index);
      if (index === 0) renderStats(latestStats);
      requestAnimationFrame(() => {
        panel.classList.remove("is-swapping");
        panel.classList.add("is-arriving");
        arrivalTimer = setTimeout(() => panel.classList.remove("is-arriving"), 500);
      });
    }, 165);
    if (resetTimer) scheduleAutoAdvance();
  };

  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(index, { resetTimer: true }));
    tab.addEventListener("keydown", (event) => {
      if (!['ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft'].includes(event.key)) return;
      event.preventDefault();
      const direction = ['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : -1;
      tabs[(index + direction + tabs.length) % tabs.length].focus();
    });
  });
  panel?.addEventListener("pointerenter", () => clearInterval(autoTimer));
  panel?.addEventListener("pointerleave", scheduleAutoAdvance);
  scheduleAutoAdvance();
}

/* Hero copy A/B test (see the inline script in index.html for the draw and
   the text swap). Each variant gets its own goal name so engagement can be
   compared side by side in Plausible. */
function bindHeroGoal() {
  const cta = document.getElementById("hero-primary-cta");
  if (!cta) return;
  cta.addEventListener("click", () => {
    const variant = window.__heroVariant === "b" ? "B" : "A";
    try {
      window.plausible?.(`hero-section-button-click-${variant}`);
    } catch {
      // Analytics must never be the reason the CTA fails to navigate.
    }
  });
}

preloadSchoolSuggestions();
hydrate();
bindWatchTabs();
bindHeroGoal();
