const input = document.getElementById("district-search");
const results = document.getElementById("district-results");
let timer;
input.addEventListener("input", () => {
  clearTimeout(timer);
  timer = setTimeout(async () => {
    const query = input.value.trim();
    if (query.length < 2) { results.innerHTML = '<p class="empty">Type at least two characters.</p>'; return; }
    const response = await fetch(`/api/schools/search?q=${encodeURIComponent(query)}`);
    const payload = await response.json().catch(() => ({}));
    results.replaceChildren();
    for (const item of payload.results || []) {
      const link = document.createElement("a");
      link.className = "alert-card";
      link.href = `/feed?district=${encodeURIComponent(item.districtId)}`;
      const title = document.createElement("h3");
      title.textContent = item.name;
      const detail = document.createElement("p");
      detail.textContent = `${item.state || ""}${item.available ? " · Alerts available" : " · No published alerts yet"}`;
      link.append(title, detail);
      results.append(link);
    }
    if (!results.children.length) results.innerHTML = '<p class="empty">No matching published districts.</p>';
  }, 150);
});
