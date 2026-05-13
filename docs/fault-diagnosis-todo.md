# Fault Diagnosis TODO

- Improve IP extraction from "first IP wins" to tagged management IP extraction, with fallback patterns for `管理IP`, `管理 IP`, and `mgmt_ip`.
- Make CMDB resolution observable by distinguishing unconfigured CMDB, query failure, and missing mapping in reports and logs.
- Expand metric collection beyond CPU, active threads, and slow SQL count to include connections, QPS/TPS, and lock waits.
- Add an explicit CMDB mapping section to the diagnosis report, including management IP, business IP, system name, and mapping status.
- Improve TopSQL evidence for historical incidents by adding slow log, performance schema history, or log MCP support.
