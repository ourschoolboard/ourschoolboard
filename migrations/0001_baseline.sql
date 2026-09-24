-- Current-state baseline. Contains no application data or prior migration history.
create table if not exists {{p}}agencies (
  id bigserial primary key, nces_leaid text not null unique, name text not null,
  state text not null default 'CA', county text, agency_type integer,
  agency_type_label text, enrollment integer, website text, board_page_url text,
  platform text, platform_detail jsonb not null default '{}'::jsonb,
  status text not null default 'not_attempted', status_detail text,
  consecutive_failures integer not null default 0, last_success_at timestamptz,
  last_attempt_at timestamptz, terms_status text, terms_url text, terms_note text,
  skip_reason text, created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index {{p}}agencies_status_idx on {{p}}agencies(status);
create index {{p}}agencies_platform_idx on {{p}}agencies(platform);

create table if not exists {{p}}agency_tos_checks (
  id bigserial primary key, agency_id bigint not null references {{p}}agencies(id) on delete cascade,
  source text not null, url text, verdict text not null, evidence text,
  raw jsonb not null default '{}'::jsonb, checked_at timestamptz not null default now()
);
create table if not exists {{p}}platform_terms (
  id bigserial primary key, platform text not null, host text not null, terms_url text,
  status text not null, binds text, evidence text, scope_note text,
  checked_at timestamptz not null default now(), unique(platform,host)
);

create table if not exists {{p}}districts (
  id bigserial primary key, slug text not null unique, name text not null, state text,
  entity_type text not null default 'district', nces_id text,
  agency_id bigint references {{p}}agencies(id) on delete set null,
  governed_by text, website text, status text not null default 'draft',
  source_evidence jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb, display_order integer,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create index {{p}}districts_status_idx on {{p}}districts(status,name);
create index {{p}}districts_agency_idx on {{p}}districts(agency_id);

create table if not exists {{p}}district_pages (
  id bigserial primary key, district_id bigint not null references {{p}}districts(id) on delete cascade,
  page_kind text not null check(page_kind='feed'), content jsonb not null,
  status text not null default 'draft', published_at timestamptz,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
  unique(district_id,page_kind)
);

create table if not exists {{p}}agency_scrapers (
  id bigserial primary key, agency_id bigint not null references {{p}}agencies(id) on delete cascade,
  district_id bigint references {{p}}districts(id) on delete cascade, version integer not null,
  source text not null, language text not null default 'python', origin text not null default 'authored',
  model text, repair_reason text, notes text, is_active boolean not null default false,
  kind text not null default 'generated', adapter text, config jsonb not null default '{}'::jsonb,
  lookback_days integer not null default 365, created_at timestamptz not null default now(),
  unique(agency_id,version)
);
create unique index {{p}}agency_scrapers_active_agency_idx on {{p}}agency_scrapers(agency_id)
  where is_active and district_id is null;
create unique index {{p}}agency_scrapers_active_district_idx on {{p}}agency_scrapers(district_id)
  where is_active and district_id is not null;

create table if not exists {{p}}agency_scrape_runs (
  id bigserial primary key, agency_id bigint not null references {{p}}agencies(id) on delete cascade,
  district_id bigint references {{p}}districts(id) on delete set null,
  scraper_id bigint references {{p}}agency_scrapers(id) on delete set null,
  trigger text not null default 'schedule', status text not null default 'running',
  documents_seen integer not null default 0, documents_new integer not null default 0,
  failure_kind text, error text, detail jsonb not null default '{}'::jsonb,
  alert_review_status text, alert_review_error text, alert_review_attempts integer not null default 0,
  alert_review_attempted_at timestamptz, alerts_published integer not null default 0,
  alert_review_alert_ids bigint[] not null default '{}', alert_review_usage jsonb not null default '{}'::jsonb,
  emails_sent integer not null default 0, failure_notified_at timestamptz,
  started_at timestamptz not null default now(), finished_at timestamptz
);
create index {{p}}agency_scrape_runs_status_idx on {{p}}agency_scrape_runs(status,started_at desc);
create index {{p}}agency_scrape_runs_review_idx on {{p}}agency_scrape_runs(alert_review_status,finished_at desc);

create table if not exists {{p}}agency_meetings (
  id bigserial primary key, agency_id bigint not null references {{p}}agencies(id) on delete cascade,
  district_id bigint references {{p}}districts(id) on delete set null, meeting_date date,
  title text, kind text not null default 'other', source_url text not null, document_filename text,
  storage_key text, content_type text, bytes integer, sha256 text,
  origin text not null default 'scraped' check(origin in ('scraped','manual_staff','community_upload')),
  first_seen_run bigint references {{p}}agency_scrape_runs(id) on delete set null,
  created_at timestamptz not null default now(), updated_at timestamptz not null default now()
);
create unique index {{p}}agency_meetings_scraped_source_url_idx on {{p}}agency_meetings(agency_id,source_url)
  where origin='scraped';
create unique index {{p}}agency_meetings_manual_sha_idx on {{p}}agency_meetings(agency_id,sha256)
  where origin<>'scraped';
create index {{p}}agency_meetings_district_date_idx on {{p}}agency_meetings(district_id,meeting_date desc);

create table if not exists {{p}}agency_document_versions (
  id bigserial primary key, meeting_id bigint not null references {{p}}agency_meetings(id) on delete cascade,
  scrape_run_id bigint references {{p}}agency_scrape_runs(id) on delete set null,
  storage_key text not null, content_type text, bytes integer not null, sha256 text not null,
  fetched_at timestamptz not null default now(), unique(meeting_id,sha256)
);
create table if not exists {{p}}district_documents (
  district_id bigint not null references {{p}}districts(id) on delete cascade,
  meeting_id bigint not null references {{p}}agency_meetings(id) on delete cascade,
  created_at timestamptz not null default now(), primary key(district_id,meeting_id)
);
create table if not exists {{p}}agency_document_texts (
  document_version_id bigint primary key references {{p}}agency_document_versions(id) on delete cascade,
  meeting_id bigint not null references {{p}}agency_meetings(id) on delete cascade,
  content text not null, search_vector tsvector generated always as (to_tsvector('english',content)) stored,
  extracted_at timestamptz not null default now()
);
create index {{p}}agency_document_texts_search_idx on {{p}}agency_document_texts using gin(search_vector);

create table if not exists {{p}}alerts (
  id bigserial primary key, district_id bigint not null references {{p}}districts(id) on delete cascade,
  alert_key text not null, title text not null, summary text, topic text, severity text,
  event_date date, source_url text,
  source_meeting_id bigint references {{p}}agency_meetings(id) on delete set null,
  school_scope jsonb not null default '[]'::jsonb check(jsonb_typeof(school_scope)='array'),
  content jsonb not null default '{}'::jsonb, status text not null default 'draft',
  published_at timestamptz, display_order integer, created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(), unique(district_id,alert_key),
  check(status<>'published' or severity is distinct from 'low')
);
create index {{p}}alerts_feed_idx on {{p}}alerts(district_id,status,event_date desc);

create table if not exists {{p}}scrape_schedules (
  id bigserial primary key, district_id bigint not null references {{p}}districts(id) on delete cascade,
  scraper_id bigint not null references {{p}}agency_scrapers(id) on delete cascade,
  cadence_days integer not null default 7, schedule_config jsonb, enabled boolean not null default true,
  next_run_at timestamptz, claimed_at timestamptz, last_started_at timestamptz,
  last_completed_at timestamptz, created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(), unique(district_id)
);
create index {{p}}scrape_schedules_due_idx on {{p}}scrape_schedules(next_run_at) where enabled;
