CREATE TABLE IF NOT EXISTS cmd_hosts (
  management_ip varchar(64) PRIMARY KEY,
  business_ip varchar(64) NOT NULL,
  system_name varchar(255) NOT NULL,
  created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  KEY idx_cmd_hosts_system_name (system_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
