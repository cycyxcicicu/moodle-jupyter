# Infra Data Stack

Stack Docker rieng cho cac dich vu data:
- MySQL
- MariaDB
- PostgreSQL
- Redis
- Qdrant
- MongoDB

## Huong production-style cho PostgreSQL

Container `postgres` trong stack nay dong vai tro shared DB layer:
- 1 PostgreSQL service chung
- nhieu database/user rieng cho tung ung dung

Init script se tao san cac database/user sau:
- `moodle` / `moodle_user`
- `jupyterhub` / `jupyterhub_user`
- `gitlab_jupyter_db` / `gitlab_jupyter_user`
- `qa_default_db` / `admin`
- `mantis_db` / `testlink_db` dung chung user `admin`

## Khoi dong nhanh

1. Tao file env:

```bash
cp .env.example .env
```

2. Chay stack:

```bash
docker compose up -d
```

3. Kiem tra trang thai:

```bash
docker compose ps
```

4. Dung stack:

```bash
docker compose down
```

## Luu y

- Stack nay dung named volumes de tranh loi quyen tren /mnt/d voi DB (dac biet PostgreSQL).
- Muon dung cho production-style local test, chay `postgres` truoc va cho cac app tro den cung host DB.
- Neu chi can mot so dich vu, co the xoa service khong dung trong `docker-compose.yml`.
- Tren production AWS, uu tien dung dich vu managed (RDS, ElastiCache, v.v.).
