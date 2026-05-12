create table if not exists cmd_hosts (
  management_ip text primary key,
  business_ip text not null,
  system_name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_cmd_hosts_system_name
  on cmd_hosts(system_name);
