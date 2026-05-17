-- 容器首次初始化时自动建库（postgres 官方镜像执行 /docker-entrypoint-initdb.d/*.sql）
-- boss_gateway: api-gateway 与 agent-gateway 共用
-- smart_job:    portal-api（identity schema 由服务启动时自建）
CREATE DATABASE boss_gateway;
CREATE DATABASE smart_job;
