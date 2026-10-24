# 达人营销智能审核平台

**达人进度与异议已接入：** 脚本／视频验收后可确认修改或逐条提出异议，品牌可裁决；「达人反馈」提供进度看板、异议处理与站内催办。见 [达人协作说明](docs/CREATOR_DISCUSSIONS.md)。

**视频验收已支持三层规则：** 基础默认、Brief 定制、详细组件分开配置，统一按必须／建议通过；缺资料标待品牌配置，历史记录可按层复盘。见 [视频三层规则](docs/VIDEO_RULE_LAYERS.md)。

品牌现可在项目详情、规则编辑及脚本/视频验收页查看达人未通过记录，对照当时规则、材料和反馈调整规则。见 [提交记录复盘](docs/SUBMISSION_FEEDBACK.md)。

**Brief 解构模型已独立：** 默认语言模型 `qwen3.8-flash`，独立保存服务地址、加密 Key、思考参数和配置版本；内容审核继续使用原有多模态配置。见 [解构模型说明](docs/DECOMPOSITION_MODEL.md)。

**视频入口与图文验收已接入：** 达人合作页可切换脚本、视频和图文；图文支持文案与有序多图提交、版本历史、AI逐条反馈及品牌复核。操作流程与限制见 [图文验收说明](docs/IMAGE_POST_REVIEW.md)。

**视频取证前端已接入：** `/settings/models` 可在服务端加密保存阿里云 Key，显式发起真实文本/短视频测试；视频任务页可带入当前规则进行独立取证预览。需额外安装 `backend/requirements-evidence.txt`。操作与限制见 [视频工具取证](docs/VIDEO_EVIDENCE.md)。真实模型兼容性尚待用户自己的账号测试，测试结果不自动推进人工通过资格。下面“骨架”清单为最初交付记录，当前已逐步接入解析和取证功能。

基于 React、TypeScript 与 FastAPI 的本地审核工作台，支持 Brief 解析与规则确认、脚本审核、视频取证、图文验收及人工复核。模型配置与业务数据保存在本地，真实供应商能力需使用自己的账号验证。

## 已可使用

- React + TypeScript + Vite 工作台，Ant Design 中文主题、TanStack Query、七页路由与响应式公共布局。
- FastAPI 模块化 API、SQLite 版本迁移、统一错误/requestId 与交互式 OpenAPI。
- 本地演示工作区的项目创建、查询、品牌/平台/状态筛选、分页及详情。
- 项目创建幂等、重名校验、事务与审计记录；未接入接口明确返回 501。
- 后端公共状态/审核结果模型，以及从 OpenAPI 自动生成的前端 TypeScript 类型。
- 基础 API/契约测试、前端测试、浏览器冒烟测试入口与 CI。

上方基础能力清单保留最初交付范围；当前业务能力及限制以对应模块文档为准。AI 输出仍需经过业务门禁及人工复核。

## 环境

服务器部署已提供 Docker Compose（品牌端、达人端、共享后端及持久化数据卷），见 [Docker 部署说明](docs/DOCKER_DEPLOYMENT.md)。

Node.js 22.12+（本地验证 24.14），Python 3.11，npm。后端单实例、本机使用，不直接公开到公网；共享部署前补认证与文件授权。前端代理后端，无须开放跨域。

## 快速启动（Windows PowerShell）

在项目根目录安装前端依赖：

```powershell
npm ci
```

在根目录创建后端环境（团队成员各自独立工作目录、虚拟环境和数据库）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
```

终端一运行后端：

```powershell
.\scripts\dev-backend.ps1
```

终端二运行前端：

```powershell
npm run dev
```

若默认 5173 已被其他项目占用，运行 `npm run dev --workspace frontend -- --port 5175`，不会终止其他项目。

同时开着品牌端和达人端时，各开一个终端（共用同一个后端）：

```powershell
npm run dev:brand     # http://127.0.0.1:5173，固定品牌端
npm run dev:creator   # http://127.0.0.1:5174，固定达人端
```

两个端口通过 `--mode brand|creator` 把角色钉死，页面上不再显示"切换到品牌端/达人端"按钮，避免在同一个窗口里串台。达人端右上角可以切换当前达人的身份，选择记在本地，切到别的达人时任务页会回到「我的合作」。不带 `--mode` 的 `npm run dev` 仍是原来的单端口手动切换行为。

- 前端：[http://127.0.0.1:5173](http://127.0.0.1:5173)
- 健康检查：[http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
- API 文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 首次启动自动迁移 SQLite 到 `backend/.data/workbench.sqlite3`，不自动注入项目或假审核数据。

环境变量可参考 `backend/.env.example` 与 `frontend/.env.example`。默认无需创建 .env；后端脚本会在存在 `backend/.env` 时读取。可通过 `REVIEW_DATA_DIR` 指定各成员自己的数据目录。模型密钥不得使用 `VITE_` 前缀。

Linux/macOS：创建 `.venv` 后激活，安装相同 requirements；运行 `python -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload`。

## 验证

```powershell
npm run typecheck
npm run build
npm run test:frontend
.\.venv\Scripts\python.exe -X utf8 -m pytest -c backend/pyproject.toml backend/tests
npm run spec:validate
```

后端测试使用临时数据库，不修改开发数据。生产构建只是生成前端静态文件；当前未包含生产部署、SPA 静态托管及后台 worker 的业务实现。

首次运行浏览器测试需安装 Chromium：

```powershell
npx playwright install chromium
npx playwright test
```

已安装 Google Chrome 时也可设置 `$env:PLAYWRIGHT_CHANNEL='chrome'` 后运行测试，无须下载 Playwright Chromium。

Playwright 自动启动独立 API（8001，临时数据目录）与前端测试实例（5175），不会占用品牌端和达人端的端口，也不会清空开发数据库。测试也会覆盖新项目、项目详情、各模块路由和移动宽度降级。

## 公共契约更新

后端是字段和枚举的单一事实源。使用与后端相同的 Python 环境导出：

```powershell
.\.venv\Scripts\Activate.ps1
npm run contracts
```

如果本机执行策略不允许激活，可以直接运行：

```powershell
.\.venv\Scripts\python.exe backend/scripts/export_openapi.py
npx openapi-typescript contracts/openapi.json -o frontend/src/shared/api/generated.d.ts
```

`contracts/openapi.json` 与 `frontend/src/shared/api/generated.d.ts` 提交到 Git，禁止手工编辑生成类型。完整未来契约见 [OpenSpec](openspec/README.md)，当前已实现接口见运行中的 `/docs`，两者范围不同。

## 团队入口

- [协作与文件归属](docs/COLLABORATION.md)
- [骨架交付范围](docs/SCAFFOLD.md)
- [实际验证记录](docs/VALIDATION.md)
- [OpenSpec 文档](openspec/README.md)
- [七张参考图](docs/assets/screenshots)

不自动创建提交、分支或修改 `team/creator-review` 的协调台账。当前骨架是用户授权的一次公共基础实施，不代表 B/C/D 业务任务已放行或完成。


## 目录与 GitHub 上传

```text
backend/             FastAPI 服务、迁移、提示词和后端测试
frontend/            React 工作台
contracts/           OpenAPI 公共契约
scripts/             开发、浏览器测试和源码导出工具
e2e/                 浏览器测试与固定测试素材
.github/             CI 工作流
docs/                功能说明、研究记录及 assets/screenshots 参考图
openspec/            需求和设计规范
team/                历史协作记录
.local/              本地归档、业务样例、导出包；Git 忽略
```

数据库、模型密钥、上传文件保留在 `backend/.data/`；历史调试文件保存在 `.local/archive/2026-10-21/`，移动清单为其中的 `manifest.json`。原 `图片/` 中的业务 Brief、脚本和视频保存在 `.local/samples/original-materials/`，不随源码发布。依赖目录 `.venv/`、`node_modules/` 保留本机使用，不上传。

生成不包含本地数据和 Git 历史的源码包：

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/export-github.py
```

输出 `.local/exports/creator-review-github.zip`。解压后可以初始化一个新的 GitHub 仓库，先检查文件再提交。旧仓库历史已经包含 357MB 安装包及测试密钥文件，仅删除工作区文件不会清除这些历史对象；直接推送旧历史仍可能因超大文件被拒绝。此源码包不携带旧 `.git` 历史；本次整理没有改写历史、创建提交或推送。

详细整理范围和上传步骤见 [仓库整理说明](docs/REPOSITORY.md)。
