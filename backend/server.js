import "./load-env.js";

import http from "node:http";
import path from "node:path";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import pg from "pg";

import { t } from "./env.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FRONTEND = path.join(ROOT, "frontend");
const PORT = Number(process.env.PORT || 3000);
const pool = process.env.DATABASE_URL
  ? new pg.Pool({ connectionString: process.env.DATABASE_URL, max: Number(process.env.PG_POOL_MAX || 10) })
  : null;

const ROUTES = new Map([
  ["/", "index.html"], ["/feed", "feed.html"], ["/feed.html", "feed.html"],
  ["/alert", "alert.html"], ["/alert.html", "alert.html"],
  ["/explore", "explore.html"], ["/journalists", "journalists.html"],
  ["/journalist-data", "journalist-data.html"]
]);
const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".jpg": "image/jpeg",
  ".webp": "image/webp", ".png": "image/png"
};
const TOPIC_LABELS = {
  budget: "Financial", curriculum: "Curriculum", facilities: "Facilities",
  policy: "Day-to-day", risk: "High-risk", staffing: "Admin hiring", other: "Other"
};

function json(res, status, value) {
  res.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(JSON.stringify(value));
}

function iso(value) {
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  return value ? String(value).slice(0, 10) : null;
}

function alertRecord(row) {
  return {
    ...(row.content || {}), id: row.alert_key, title: row.title, summary: row.summary,
    topic: row.topic || "other", severity: row.severity || "medium", date: iso(row.event_date),
    status: "published", sourceUrl: row.source_url,
    categoryLabel: row.content?.categoryLabel || TOPIC_LABELS[row.topic] || "Update",
    schoolScope: Array.isArray(row.school_scope) ? row.school_scope : []
  };
}

async function publishedAlerts(slug) {
  const { rows } = await pool.query(
    `select a.alert_key,a.title,a.summary,a.topic,a.severity,a.event_date,a.source_url,
            a.school_scope,a.content,a.source_meeting_id
       from ${t("alerts")} a join ${t("districts")} d on d.id=a.district_id
      where d.slug=$1 and d.status='published' and a.status='published'
      order by a.event_date desc nulls last,a.display_order nulls last,a.id`, [slug]
  );
  return rows;
}

async function feed(slug) {
  const [districtResult, pageResult, alerts] = await Promise.all([
    pool.query(`select id,slug,name,state,entity_type from ${t("districts")} where slug=$1 and status='published'`, [slug]),
    pool.query(
      `select p.content from ${t("district_pages")} p join ${t("districts")} d on d.id=p.district_id
        where d.slug=$1 and p.page_kind='feed' and p.status='published'`, [slug]
    ),
    publishedAlerts(slug)
  ]);
  const district = districtResult.rows[0];
  if (!district || (!pageResult.rows[0] && !alerts.length)) return null;
  const content = pageResult.rows[0]?.content || {};
  const mapped = alerts.map(alertRecord);
  const counts = new Map();
  for (const item of mapped) counts.set(item.topic, (counts.get(item.topic) || 0) + 1);
  const dates = mapped.map((item) => item.date).filter(Boolean).sort();
  return {
    ...content,
    district: { id: district.slug, name: district.name, state: district.state, entityType: district.entity_type },
    period: content.period || { start: dates[0] || null, end: dates.at(-1) || null },
    summary: content.summary || { alertsPublished: mapped.length },
    categories: [...counts].map(([id, count]) => ({ id, label: TOPIC_LABELS[id] || "Other", count })),
    alerts: mapped
  };
}

async function journalistPatterns(state) {
  const params = state ? [state] : [];
  const stateClause = state ? "and d.state=$1" : "";
  const [summary, states, topics, months, districts, recent] = await Promise.all([
    pool.query(
      `select count(a.id)::int alerts,count(distinct a.district_id)::int districts,
              count(distinct d.state)::int states,min(a.event_date) first_date,max(a.event_date) last_date
         from ${t("alerts")} a join ${t("districts")} d on d.id=a.district_id
        where a.status='published' and d.status='published' ${stateClause}`, params),
    pool.query(
      `select d.state,count(a.id)::int alerts,count(distinct case when a.id is not null then d.id end)::int covered_districts,
              count(distinct d.id)::int total_districts
         from ${t("districts")} d left join ${t("alerts")} a on a.district_id=d.id and a.status='published'
        where d.status='published' and d.state is not null group by d.state order by d.state`),
    pool.query(
      `select coalesce(a.topic,'other') topic,count(*)::int alerts
         from ${t("alerts")} a join ${t("districts")} d on d.id=a.district_id
        where a.status='published' and d.status='published' ${stateClause}
        group by coalesce(a.topic,'other') order by alerts desc`, params),
    pool.query(
      `select to_char(date_trunc('month',a.event_date),'YYYY-MM') month,
              coalesce(a.topic,'other') topic,count(*)::int alerts
         from ${t("alerts")} a join ${t("districts")} d on d.id=a.district_id
        where a.status='published' and d.status='published' ${stateClause}
          and a.event_date>=date_trunc('month',current_date)-interval '17 months'
        group by date_trunc('month',a.event_date),coalesce(a.topic,'other')
        order by date_trunc('month',a.event_date),topic`, params),
    pool.query(
      `select d.slug,d.name,d.state,count(*)::int alerts
         from ${t("alerts")} a join ${t("districts")} d on d.id=a.district_id
        where a.status='published' and d.status='published' ${stateClause}
        group by d.id,d.slug,d.name,d.state order by alerts desc,d.name limit 12`, params),
    pool.query(
      `select a.alert_key,a.title,a.topic,a.event_date,d.slug,d.name,d.state
         from ${t("alerts")} a join ${t("districts")} d on d.id=a.district_id
        where a.status='published' and d.status='published' ${stateClause}
        order by a.event_date desc nulls last,a.id desc limit 12`, params)
  ]);
  const stateRows = states.rows.map((row) => ({
    state: row.state, alerts: row.alerts, districts: row.covered_districts,
    coveredDistricts: row.covered_districts, totalDistricts: row.total_districts,
    coveragePct: row.total_districts ? Number((row.covered_districts / row.total_districts * 100).toFixed(1)) : 0
  }));
  const totals = summary.rows[0];
  const selected = state ? stateRows.filter((row) => row.state === state) : stateRows;
  const totalDistricts = selected.reduce((sum, row) => sum + row.totalDistricts, 0);
  const coveredDistricts = selected.reduce((sum, row) => sum + row.coveredDistricts, 0);
  return {
    generatedAt: new Date().toISOString(), filter: { state: state || null },
    summary: { alerts: totals.alerts, districts: totals.districts, states: totals.states,
      coveredDistricts, totalDistricts,
      coveragePct: totalDistricts ? Number((coveredDistricts / totalDistricts * 100).toFixed(1)) : 0,
      firstDate: iso(totals.first_date), lastDate: iso(totals.last_date) },
    states: stateRows, topics: topics.rows, months: months.rows, districts: districts.rows,
    recent: recent.rows.map((row) => ({ id: row.alert_key, title: row.title, topic: row.topic,
      date: iso(row.event_date), district: { id: row.slug, name: row.name, state: row.state } }))
  };
}

async function api(req, res, url) {
  if (url.pathname === "/api/health") {
    let postgres = false;
    if (pool) try { await pool.query("select 1"); postgres = true; } catch {}
    return json(res, postgres ? 200 : 503, { ok: postgres, postgres, generatedAt: new Date().toISOString() });
  }
  if (!pool) return json(res, 503, { ok: false, error: "DATABASE_URL is not configured." });
  if (url.pathname === "/api/stats") {
    const { rows } = await pool.query(
      `select count(distinct d.id)::int schools,count(distinct m.id)::int documents,
              count(distinct a.id)::int alerts
         from ${t("districts")} d
         left join ${t("district_documents")} x on x.district_id=d.id
         left join ${t("agency_meetings")} m on m.id=x.meeting_id
         left join ${t("alerts")} a on a.district_id=d.id and a.status='published'
        where d.status='published'`);
    return json(res, 200, { ...rows[0], minutes: 0 });
  }
  if (url.pathname === "/api/schools/search") {
    const q = String(url.searchParams.get("q") || "").trim();
    const { rows } = await pool.query(
      `select d.slug as key,d.slug as "districtId",d.name,d.state,d.entity_type as "entityType",
              exists(select 1 from ${t("alerts")} a where a.district_id=d.id and a.status='published') as available
         from ${t("districts")} d where d.status='published' and ($1='' or d.name ilike '%'||$1||'%')
        order by d.name limit 30`, [q]);
    return json(res, 200, { ok: true, query: q, results: rows });
  }
  if (url.pathname === "/api/highlights") {
    const { rows } = await pool.query(
      `select a.alert_key,a.title,a.summary,a.topic,a.severity,a.event_date,a.source_url,a.content,d.slug,d.name,d.state
         from ${t("alerts")} a join ${t("districts")} d on d.id=a.district_id
        where a.status='published' and d.status='published'
        order by a.event_date desc nulls last,a.id desc limit 24`);
    return json(res, 200, { alerts: rows.map((row) => ({ ...alertRecord(row), district: { id: row.slug, name: row.name, state: row.state } })) });
  }
  if (url.pathname === "/api/feed") {
    const slug = String(url.searchParams.get("district") || "");
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(slug)) return json(res, 400, { ok: false, error: "Pass a valid district slug." });
    const value = await feed(slug);
    return value ? json(res, 200, value) : json(res, 404, { ok: false, error: "No published feed for this district." });
  }
  if (url.pathname === "/api/alert") {
    const slug = String(url.searchParams.get("district") || "");
    const id = String(url.searchParams.get("id") || "");
    const value = await feed(slug);
    const alert = value?.alerts.find((item) => item.id === id);
    if (!alert) return json(res, 404, { ok: false, error: "Unknown alert." });
    const related = value.alerts.filter((item) => item.id !== id && item.topic === alert.topic).slice(0, 4);
    return json(res, 200, { district: value.district, alert, related, sourceDocument: null });
  }
  if (url.pathname === "/api/journalists/alerts") {
    const state = String(url.searchParams.get("state") || "").toUpperCase();
    if (state && !/^[A-Z]{2}$/.test(state)) return json(res, 400, { ok: false, error: "Pass a two-letter state code." });
    return json(res, 200, await journalistPatterns(state || null));
  }
  return json(res, 404, { ok: false, error: "Unknown API route." });
}

async function serveStatic(res, pathname) {
  const file = ROUTES.get(pathname) || pathname.slice(1);
  if (!file || file.includes("..") || path.isAbsolute(file)) return false;
  const target = path.resolve(FRONTEND, file);
  if (!target.startsWith(`${FRONTEND}${path.sep}`)) return false;
  try {
    const body = await readFile(target);
    res.writeHead(200, { "content-type": MIME[path.extname(target)] || "application/octet-stream",
      "cache-control": target.endsWith(".html") ? "no-cache" : "public, max-age=3600" });
    res.end(body);
    return true;
  } catch { return false; }
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
    if (req.method === "GET" && url.pathname.startsWith("/api/")) return await api(req, res, url);
    if (req.method === "GET" && await serveStatic(res, url.pathname)) return;
    json(res, 404, { ok: false, error: "Not found." });
  } catch (error) {
    console.error(error);
    json(res, 500, { ok: false, error: "Internal server error." });
  }
});

if (process.env.NODE_ENV !== "test") server.listen(PORT, () => console.log(`ourschoolboard listening on ${PORT}`));
export { server };
