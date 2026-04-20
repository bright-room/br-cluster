# CNPG Clusters

Shared PostgreSQL clusters managed by the CloudNativePG operator.

## Policy: 1 DB : 1 login role

Each logical database has exactly one login role of the same name. A role
MUST NOT be granted privileges on any database other than its own.

- **Do**: add a new database via the `Database` CRD (CNPG v1.25+) and
  create a dedicated role with ownership on that database only.
- **Don't**: reuse an existing role across databases, or grant
  `ALL PRIVILEGES` / ownership across multiple databases to one role.
- **Don't**: extend `bootstrap.initdb` after the cluster is initialised —
  `initdb` only runs once. Add subsequent databases declaratively.

## Clusters

- `platform-pg` — shared cluster for platform components (Zitadel, ...).
