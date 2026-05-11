create table if not exists agent_token_usage (
  id bigserial primary key,
  task_id text not null references optimization_tasks(task_id) on delete cascade,
  provider text not null default 'qwen',
  model text not null,
  operation text not null,
  prompt_tokens integer not null default 0,
  completion_tokens integer not null default 0,
  total_tokens integer not null default 0,
  raw_usage jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_agent_token_usage_task_id_created_at
  on agent_token_usage(task_id, created_at desc);
