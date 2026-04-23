create extension if not exists vector;

create table if not exists optimization_tasks (
  task_id text primary key,
  raw_sql text not null,
  status text not null,
  dingtalk_message_id text,
  dingtalk_conversation_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists optimization_events (
  id bigserial primary key,
  task_id text not null references optimization_tasks(task_id),
  status text not null,
  message text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create table if not exists optimization_cases (
  case_id text primary key,
  db_type text not null,
  db_version text not null,
  sql_fingerprint text not null,
  scenario_tags text[] not null default '{}',
  plan_features jsonb not null default '{}'::jsonb,
  root_cause_tags text[] not null default '{}',
  optimization_actions jsonb not null default '[]'::jsonb,
  before_after_metrics jsonb not null default '{}'::jsonb,
  case_card text not null,
  full_text text not null,
  embedding vector(1024),
  quality_score numeric not null default 0,
  created_at timestamptz not null default now()
);
