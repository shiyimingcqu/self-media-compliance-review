# 四人协作与接入约定

沿用 `team/creator-review` 的 A/B/C/D 模块划分。本次按用户直接授权创建公共骨架；不修改团队登记、不代替协调者分发消息、不自动实施其他成员业务。协调台账关于只读准备的既有记录保持原样。

## 1. 文件归属

| 归属 | 前端 | 后端 | 其他 |
|---|---|---|---|
| A 公共基础实施者（协调者评审） | `frontend/src/app/`、`shared/`、`features/projects/` | `app/core/`、`modules/foundation/`、`app/main.py` | 根配置、锁文件、契约生成、公共迁移、CI、scripts |
| B Brief/脚本 | `frontend/src/features/brief-script/` | `backend/app/modules/brief_script/` | 自己的模块测试及有 B 前缀的迁移 |
| C 视频/模型 | `frontend/src/features/video-model/` | `backend/app/modules/video_model/` | 作业 worker、模型适配器、C 模块测试及迁移 |
| D 人工/报告/验收 | `frontend/src/features/human-review/` | `backend/app/modules/human_review/` | 通用报告、D 模块测试、跨模块 E2E |

公共基础目录单写；B/C/D 不各造一份 requestId、状态枚举、文件存储或门禁。需要公共变更时提交明确字段/行为的修改建议，由协调者评审后交单一实施者落地。不要直接修改其他模块私有 service/repository。

## 2. 每位成员启动方式

1. 使用自己的分支与 checkout/worktree。共享仓库路径时必须确保文件所有权不交叉；推荐独立工作目录。
2. 各自运行 `npm ci`，创建 `.venv` 并安装 `backend/requirements-dev.txt`。
3. 各自使用 `backend/.data`，或设置独立 `REVIEW_DATA_DIR`。不要同时修改同一个 SQLite 文件。
4. 如果同一台机器并行启动，分配不同 API/前端端口，在自己的未提交 `.env` 设置 API_PROXY_TARGET。
5. 从对应 `routes.tsx` 和 `router.py` 扩展，无需修改公共路由注册文件；已有页面文件可直接替换占位实现。

## 3. 各模块接入点

### B：Brief 与脚本

- 前端 `BriefPage.tsx` / `ScriptPage.tsx`，路由已注册。
- 后端 GET `/briefs/:id/rule-versions`、GET `/tasks/:id/scripts` 暂为 501，后续按 OpenSpec 增加上传解析/规则确认等端点。
- 文件存储/下载/Range 由 A 后续实现，B 负责解析与 sourceMap。骨架尚未提供文件存储服务，不能把本地任意路径暴露成 API。
- B 负责规则启用快照、反馈内容；脚本人工裁决交 D 的 scope=script，不另建人工通过旁路。

### C：视频与模型

- 前端 `VideoPage.tsx` / `ModelSettingsPage.tsx`，视频、模型与 job 查询端点已保留。
- 当前没有运行中的 worker、供应商 SDK、密钥管理或伪造能力报告。C 在本模块实现 job 存储、worker 与适配器，公共状态直接引用 `core/contracts.py`。
- 原视频及完整音轨进入分析，禁止应用抽帧替代；记录供应商请求 ID 和快照。完整性/时间越界的业务校验应在模块实现并测试。
- 当前 context 固定 demo，切真实前需通过能力验证并与 A 协调公共 context 变更。

### D：人工与报告

- `HumanReviewsPage.tsx` 及后端 human-review router 已注册。
- 复用 script/video scope，最终决定绑定 `ReviewSnapshot`；不直接改写 AI 结果。
- `/jobs/:id/report` 由 D 统一实现，B/C 提供模块反馈数据。
- D 完善 E2E 的真实审核场景；骨架测试只验证路由、项目与基础门禁形状，不证明模型准确性。

### A：公共基础余项

项目编辑/归档、达人任务 CRUD、规则兼容绑定、全链版本失效、受控上传下载/Range、完整审计与认证尚未实施。当前项目创建/查询已具幂等与审计；不要据此宣称全部 foundation 任务完成。

## 4. 公共契约

- API 基础路径 `/api/v1`。成功 `{data,requestId}`；分页增加 `{page,pageSize,total}`；错误 `{error:{code,message,fieldErrors,details,retryable},requestId}`。
- 未接入端点必须 501/MODULE_NOT_IMPLEMENTED，不能返回空成功或假通过。模块正常接入后删除该端点的 stub 并补 response_model。
- 后端通用字段和枚举位于 `core/contracts.py`；证据、问题、作业和结果形状位于 `core/review_contracts.py`。
- 前端只从 `shared/api/types.ts` 或 `generated.d.ts` 引用，不复制另一套接口类型。
- `ReviewSnapshot` 绑定 mode、规则、脚本/视频、模型配置及能力报告。brief/capability 作业使用自己的输入元数据，不伪填不存在的规则版本。
- `Evidence` 支持原文、时间区间和全片缺失三类定位；时间大于时长需模块服务额外校验，共享结构本身不会访问视频元数据。
- 新 API 必须统一错误和 Pydantic response_model；输入输出使用 camelCase，Python 内部 snake_case。
- OpenSpec 是未来完整行为契约；现有 OpenAPI 是当前代码形状。任何偏离规范的业务改动先同步文档。

## 5. 数据库迁移

迁移只追加，不修改已应用 SQL。约定：A `A_0002_*.sql`、B `B_0001_*.sql`、C `C_0001_*.sql`、D `D_0001_*.sql`，在基础 `0001_foundation.sql` 之后按名称稳定排序。各模块迁移不能依赖别组尚未合并的表；有跨模块外键时先经 A 协调发布顺序。数据写入使用参数化 SQL 和事务。

## 6. 合并前检查

```powershell
npm run typecheck
npm run build
npm run test:frontend
python -m pytest -c backend/pyproject.toml backend/tests
npm run contracts
npm run spec:validate
```

修改契约时同时提交 OpenAPI 与生成类型。新增依赖需提前协调，避免四人各自重写 package-lock。PR 写明模块、接口变化、迁移影响、验证结果和未实现部分。每天合并小增量，第四天至少跑通一条纵向流程；未实现功能保持明确状态。
