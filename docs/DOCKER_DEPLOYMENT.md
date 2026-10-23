# Docker 服务器部署

适用于 Linux 服务器、Docker Engine 与 Compose v2+。无需在服务器安装 Node、Python、FFmpeg 或 SQLite。

## 服务结构

| 服务 | 用途 | 端口 |
| --- | --- | --- |
| `web` | Nginx，分别托管固定品牌端和固定达人端，转发同源 `/api/` | 品牌 8080、达人 8081 |
| `api` | FastAPI、SQLite、Brief 解析、视频压缩裁剪和模型调用 | 仅容器网络 8000 |
| `review-data` | 数据库、上传原件、证据、模型配置、加密主密钥和 ASR 缓存 | Docker 命名卷 |

后端保持 **一个容器、一个 Uvicorn worker**。启动时会迁移 SQLite，并将中断中的模型任务标记为待核实；不要增加 worker 或扩容 API 副本，否则启动恢复和进程内任务执行器会互相干扰。

## 首次启动

把项目代码上传到服务器，在仓库根目录运行：

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

默认仅绑定服务器本机：

- 品牌端：`http://127.0.0.1:8080`
- 达人端：`http://127.0.0.1:8081`

可通过 SSH 隧道在自己电脑访问，无需开放服务器业务端口：

```bash
ssh -L 8080:127.0.0.1:8080 -L 8081:127.0.0.1:8081 user@服务器IP
```

然后在自己电脑打开上述两个地址。若前方已有带访问控制的网关，也可以把 `.env` 的 `REVIEW_BIND_IP` 改成服务器内网 IP，由网关转发到两个端口。

当前项目没有账号登录或达人数据授权隔离，固定品牌/达人端口只区分界面角色。公网部署请在前方配置 HTTPS 与访问控制，或使用 VPN/SSH 隧道；不应把 `REVIEW_BIND_IP=0.0.0.0` 当成已经完成用户权限管理。

模型的地址、Key、模型名及思考配置仍在品牌端“设置”中保存，不写入镜像，也不写入前端环境变量。新部署是空数据；迁移旧数据见下文。

## 上传、取证与本地 ASR

- 镜像包含 FFmpeg、PDF 解析库、文档解析库、加密依赖和中文字体。
- Nginx 不限制请求体大小，视频上传采用流式代理，不引入额外的 1MB/200MB 限制；Brief 文档仍保留应用自身的 20MiB 限制。模型服务商自己的请求限制依然有效。
- 视频证据的 Range 请求原样交给后端处理。浏览器无需跨域连接 8000。
- 默认安装 CPU 版 `faster-whisper`；首次取音轨可能下载模型，缓存保存在数据卷中，需要服务器能访问模型下载源。没有 GPU 也可运行，具体处理速度取决于服务器。
- 不需要本地 ASR 时设置 `INSTALL_LOCAL_ASR=false`，重新构建 API；相关独立音轨取证能力会按现有逻辑降级，而非伪造识别结果。
- 若外层还有宝塔、云网关或其他反向代理，也要放开其上传大小和超时，否则外层仍可能返回 HTTP 413/504。

## 健康检查与日志

```bash
curl -f http://127.0.0.1:8080/healthz
curl -f http://127.0.0.1:8081/healthz
curl -f http://127.0.0.1:8080/api/v1/context
docker compose logs --tail=100 api web
```

两个容器都有健康检查，API 就绪后才启动 Web；日志自动轮转。若浏览器显示后端异常，先看 API 日志，再检查容器健康状态。

## 更新与停止

先备份，再上传或拉取新代码并更新：

```bash
docker compose up -d --build
```

停止服务但保留数据：

```bash
docker compose down
```

**不要使用 `docker compose down -v`**：`-v` 会删除命名卷中的业务数据和加密主密钥。旧版本应用不一定兼容升级后的数据库；回滚镜像时应同时恢复配套数据库备份。

## 完整备份

在 Linux 服务器执行；暂停 API 以保证 SQLite、证据和上传文件的一致性。等待当前审核完成后再停机，避免打断可能计费的请求。

```bash
mkdir -p backups
backup_path="./backups/review-$(date +%Y%m%d-%H%M%S)"
docker compose stop api
docker compose cp api:/var/lib/review "$backup_path"
docker compose start api
```

复制成功后把备份保存到另一台机器或备份存储。备份含模型凭据的加密材料与业务原件，应限制访问；不要提交到 Git。

## 迁移本机数据或恢复备份

默认本机数据在 `backend/.data/`；如果本机设置了 `REVIEW_DATA_DIR`，使用实际目录。必须复制**整个目录**，同时包含 `workbench.sqlite3`、`.secrets.key`、uploads、证据等，不能只复制数据库。若原服务使用 `REVIEW_SECRET_KEY` 环境变量，则将同一把密钥通过服务器的私密 Compose override/密钥管理注入 API，不要重新生成。

1. 停止旧后端，避免 SQLite WAL 和上传文件在复制时继续变化。
2. 把完整数据目录上传到服务器，例如 `/srv/review-import`，包含隐藏文件。
3. 构建并创建容器，然后导入到空卷：

```bash
docker compose build
docker compose create api
docker compose cp /srv/review-import/. api:/var/lib/review/
docker compose run --rm --no-deps --user root api chown -R 10001:10001 /var/lib/review
docker compose run --rm --no-deps api python tools/relocate_uploads.py
docker compose run --rm --no-deps api python tools/relocate_uploads.py --apply
docker compose up -d
```

恢复备份也使用同样方法，但先停止现有 API，并另行保留当前数据；不要将不同版本数据目录直接合并覆盖。上传目录中的子目录名称与数据库记录保持一致，勿重命名文件。

路径迁移工具处理数据库中指向 Windows 盘符或旧部署目录的上传文件路径，先逐个校验大小与 SHA-256，任何缺失或不一致都会停止，不进行部分更新。默认预览，`--apply` 才会更新并先保存一份数据库备份；审核快照与模型原始回复不重写。

## 拉取基础镜像超时

如果构建停在 `load metadata`，提示访问 `auth.docker.io/token` 超时，说明尚未进入项目构建，先排查 Docker Hub 的网络与代理连通性。可分别运行：

```bash
docker pull python:3.11-slim-bookworm
docker pull node:24-bookworm-slim
docker pull nginx:1.28-alpine
```

Docker Desktop 的代理在 Settings → Resources → Proxies 设置；按实际环境使用系统代理或手动 HTTP/HTTPS 代理。浏览器能访问不代表 Docker 已正确使用代理，代理端口也必须处于可用状态。配置方式见 [Docker 官方代理说明](https://docs.docker.com/desktop/features/networking/networking-how-tos/#working-with-proxies)。拉取成功后重新运行 `docker compose up -d --build` 即可，无需更换项目镜像标签或修改业务代码。

## 验证范围

`docker compose config --quiet` 只验证编排配置，前端本地构建只验证两个角色的静态产物；均不证明容器内的运行依赖或服务商模型能力正常。首次部署还应运行健康检查，上传一份本地测试文档与视频，确认解析、播放和裁剪。真实付费模型调用由你在页面明确发起。

本地 Docker 实测已完成：两套镜像成功构建，API 与 Web 健康检查正常，品牌端及达人端固定角色页面、同源 API 代理可用，容器内 PDF、加密、本地 ASR 依赖可导入，FFmpeg 可用。此验证没有调用付费模型，也没有迁移开发环境数据。
