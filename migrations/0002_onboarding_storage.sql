-- Guarded onboarding and publishing storage interfaces.

alter table {{p}}districts
  add column if not exists school_board_members_list jsonb not null default '[]'::jsonb;
alter table {{p}}districts
  add column if not exists school_board_contact_info jsonb not null default '{}'::jsonb;
alter table {{p}}districts
  drop constraint if exists {{p}}districts_board_members_array_check;
alter table {{p}}districts
  add constraint {{p}}districts_board_members_array_check
  check (jsonb_typeof(school_board_members_list) = 'array');
alter table {{p}}districts
  drop constraint if exists {{p}}districts_board_contact_object_check;
alter table {{p}}districts
  add constraint {{p}}districts_board_contact_object_check
  check (jsonb_typeof(school_board_contact_info) = 'object');
create unique index if not exists {{p}}districts_nces_id_key
  on {{p}}districts (nces_id) where nces_id is not null and nces_id <> '';

alter table {{p}}district_pages
  drop constraint if exists {{p}}district_pages_page_kind_check;
alter table {{p}}district_pages
  add constraint {{p}}district_pages_page_kind_check
  check (page_kind in ('overview', 'feed'));
create index if not exists {{p}}district_pages_status_idx
  on {{p}}district_pages (status, updated_at desc);

create table if not exists {{p}}past_reports (
  id bigserial primary key,
  district_id bigint not null references {{p}}districts (id) on delete cascade,
  report_key text not null,
  report_date date,
  report_type text not null,
  title text not null,
  summary text not null,
  school_scope jsonb not null default '[]'::jsonb,
  document_links jsonb not null default '[]'::jsonb,
  status text not null default 'draft',
  published_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (district_id, report_key),
  constraint {{p}}past_reports_report_type_check check (report_type in (
    'board_meeting', 'local_leadership_meeting', 'local_school_announcement',
    'budget_publication', 'policy_publication', 'plan_publication',
    'audit_publication', 'other_official_report'
  )),
  constraint {{p}}past_reports_status_check check (status in ('draft', 'published')),
  constraint {{p}}past_reports_school_scope_array_check
    check (jsonb_typeof(school_scope) = 'array'),
  constraint {{p}}past_reports_document_links_array_check
    check (jsonb_typeof(document_links) = 'array'),
  constraint {{p}}past_reports_published_at_check
    check ((status = 'published' and published_at is not null)
        or (status = 'draft' and published_at is null))
);
create index if not exists {{p}}past_reports_district_date_idx
  on {{p}}past_reports (district_id, report_date desc, id desc);
create index if not exists {{p}}past_reports_published_idx
  on {{p}}past_reports (district_id, report_date desc, id desc)
  where status = 'published';

create table if not exists {{p}}local_votes (
  id bigserial primary key,
  district_id bigint not null references {{p}}districts (id) on delete cascade,
  vote_key text not null,
  kind text not null,
  venue text not null,
  title text not null,
  summary text,
  amount numeric,
  jurisdiction text,
  vote_date date,
  status text not null default 'draft',
  detail jsonb not null default '{}'::jsonb,
  source_url text,
  last_verified date,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (district_id, vote_key),
  constraint {{p}}local_votes_kind_check check (kind in (
    'board_election', 'appointment', 'override', 'debt_exclusion', 'bond',
    'parcel_tax', 'charter_question', 'other'
  )),
  constraint {{p}}local_votes_venue_check check (
    venue in ('ballot', 'town_meeting', 'city_council', 'appointing_body')
  ),
  constraint {{p}}local_votes_status_check check (status in (
    'draft', 'proposed', 'qualified', 'scheduled', 'passed', 'failed', 'withdrawn'
  )),
  constraint {{p}}local_votes_detail_object_check check (jsonb_typeof(detail) = 'object'),
  constraint {{p}}local_votes_amount_check check (amount is null or amount >= 0)
);
create index if not exists {{p}}local_votes_district_idx
  on {{p}}local_votes (district_id, vote_date desc, id desc);
create index if not exists {{p}}local_votes_upcoming_idx
  on {{p}}local_votes (vote_date) where status in ('qualified', 'scheduled');

create table if not exists {{p}}district_category_pages (
  id bigserial primary key,
  district_id bigint not null references {{p}}districts (id) on delete cascade,
  topic text not null,
  content jsonb not null,
  alert_count integer not null default 0,
  source_alert_ids bigint[] not null default '{}',
  status text not null default 'draft',
  generated_at timestamptz,
  published_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (district_id, topic)
);
create index if not exists {{p}}district_category_pages_status_idx
  on {{p}}district_category_pages (status, updated_at desc);
