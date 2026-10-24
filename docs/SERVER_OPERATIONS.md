# 当前服务器维护

服务器：`101.132.236.122`（Ubuntu 24.04）。

- 品牌端：`http://101.132.236.122/projects`
- 达人端：`http://101.132.236.122/creator`
- 统一访问用户名：`review`。密码在部署电脑的 `.local/deployments/101.132.236.122/access.json`，不提交 Git。
- 当前是 HTTP IP 访问与 Nginx 入口密码，不是品牌/达人独立账号权限体系；配置域名后应增加 HTTPS。
- 本机旧项目及模型 Key 没有迁移；首次使用在品牌端设置模型，再上传 Brief 创建项目。

## 目录与服务

| 项目 | 位置 |
| --- | --- |
| 新项目源码与 Compose | `/srv/creator-review/app` |
| Compose 项目名 | `creator-review` |
| 业务数据卷 | `creator-review_review-data` |
| Nginx 网关配置 | `/etc/nginx/sites-available/creator-review` |
| 入口密码哈希文件 | `/etc/nginx/creator-review.htpasswd` |
| 网关内部凭据配置（不进 Git） | `/etc/nginx/creator-review-gateway.conf` |
| 旧项目源目录（已停用） | `/root/agent` |
| 旧数据库与 Nginx 备份 | `/srv/creator-review/backups` |
| 上传镜像包与源码包 | `/srv/creator-review/incoming` |

后端与静态站点容器均有重启策略及健康检查。容器的 8080/8081 仅绑定服务器回环地址，宿主 Nginx 通过 80 端口统一代理。

```bash
cd /srv/creator-review/app
docker compose ps
docker compose logs --tail=100 api web
systemctl status nginx
nginx -t
```

新版本可在有足够资源的机器构建 `creator-review-api:latest` 和 `creator-review-web:latest`，用 `docker save` 导出并上传服务器，`docker load` 导入后执行：

```bash
cd /srv/creator-review/app
docker compose up -d --no-build --wait --wait-timeout 120
```

服务器约 1.6GiB 内存，本次使用本机构建镜像上传的方式。源码发生修改不代表运行镜像会自动更新；GitHub 推送也不会自动部署。

## 数据备份

沿用 [Docker 部署说明](DOCKER_DEPLOYMENT.md) 的完整备份方式。SQLite、上传原件、证据与 `.secrets.key` 应一起备份；不要删除数据卷或使用 `down -v`。

旧 `agent` 容器已停止，数据库卷未删除，并额外保存逻辑备份 `agent-postgres-before-deploy.sql.gz`。如需回退旧站，先停止宿主 Nginx 以释放 80 端口，再到 `/root/agent` 启动旧 Compose；新项目数据卷不需要删除。

## 本次验证

模型设置、Brief 解析与取证接口的远程访问采用显式授权网关：Nginx 密码认证后覆盖 `X-Review-Gateway-Token`，API 用常量时间比较校验 `REVIEW_GATEWAY_TOKEN`，同时核对 `REVIEW_ALLOWED_ORIGINS` 与 Host。凭据保存在服务器私密配置和应用 `.env`，不会写入前端或仓库。当前允许来源为 `http://101.132.236.122`；更换域名或启用 HTTPS 时需同步更新来源并重建 API 容器。默认未配置时仍保持仅本机限制。

已通过外网浏览器检查品牌/达人固定角色、静态资源、页面刷新及认证 API；未登录页面与 API 返回 401。容器健康检查、PDF/ASR 依赖导入、FFmpeg 本地编码正常。修复了 HTTP IP 下 `crypto.randomUUID` 不存在导致的前端报错，使用 `crypto.getRandomValues` 生成 UUID v4，不采用弱随机数。

以上仅证明部署与基础运行可用，没有发起付费模型调用或验证模型结论准确性。
