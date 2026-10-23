# 仓库整理与 GitHub 上传

## 根目录用途

| 目录或文件 | 用途 | 上传 |
| --- | --- | --- |
| backend/ | 后端源码、迁移和测试 | 是 |
| frontend/ | 前端源码及构建配置 | 是 |
| deploy/、compose.yaml | Docker 镜像与 Nginx 编排 | 是 |
| contracts/ | 接口契约 | 是 |
| scripts/、e2e/、.github/ | 开发脚本、浏览器测试及 CI | 是 |
| docs/、openspec/、team/ | 文档、规范及历史设计记录 | 是 |
| .env.example、.dockerignore、.gitignore | 配置示例和排除规则 | 是 |
| .local/ | 本地资料、调试日志、归档、导出包和临时测试数据 | 否 |
| backend/.data/、.env | 业务原件、数据库、加密材料及本机配置 | 否 |
| .venv/、node_modules/、test-results/ | 本地依赖及测试产物 | 否 |

根目录的测试临时目录已移动到 `.local/archive/2026-10-23-root-test-artifacts/`，原文件保留，`manifest.json` 记录原路径与归档路径。后续浏览器测试后端临时数据统一进入 `.local/tmp/e2e/`。

## 本次必须用干净目录上传

旧 Git 历史仍含 357.5MiB 的 LibreOffice 安装包，以及历史测试 `.secrets.key` 文件。虽然当前源码已排除这些文件，直接 push 旧仓库仍会携带历史对象。

**不要把当前目录的旧 `.git` 复制到新仓库，也不要直接对旧仓库执行 push。** 本次整理没有重写或删除历史，没有提交或推送。

在根目录运行：

```powershell
python -X utf8 scripts/export-github.py
```

脚本按白名单导出当前工作区（包含尚未提交的业务修改），生成：

- `.local/exports/github-ready-时间戳/`：可直接初始化新仓库的干净源码目录。
- `.local/exports/creator-review-github.zip`：同一份源码的压缩包，不含旧 Git 历史。
- `.local/exports/github-export-manifest.json`：文件清单及本次干净目录的完整路径。

导出会确认 Docker 编排、两份 Dockerfile、Nginx 配置和运行依赖齐备，并检查越界链接、超大文件及压缩包完整性。根目录有新增配置或源码目录时，需同步更新导出脚本白名单。

## 上传步骤

建议先创建一个空的 GitHub 私有仓库，不预先生成 README。

**进入刚生成的 github-ready 目录后**执行：

```powershell
git init
git add .
git diff --cached --stat
git commit -m "Initial source import"
git branch -M main
git remote add origin https://github.com/你的账号/你的仓库.git
git push -u origin main
```

上述新目录不包含旧 `.git`，因此不会带上大安装包和历史测试密钥。不要把 ZIP 文件本身作为项目源码上传；若通过浏览器上传，可从干净目录选文件，但 Git 命令更适合完整保留隐藏配置目录。

源码包含文档参考截图及测试图片，公开仓库前应确认业务资料是否适合公开；凭据扫描不能代替业务资料审查。

## 服务器部署与旧业务数据

服务器拉取新仓库后按 [Docker 部署说明](DOCKER_DEPLOYMENT.md) 启动。原开发环境的数据库、上传文件及 `.secrets.key` 不经过 GitHub；需要保留原项目时，按该文档单独迁移完整数据目录。

只移动本地测试残留，保留现有业务源码目录结构及并行开发中的修改；不搬动正在使用的数据库、依赖目录或容器数据卷。
