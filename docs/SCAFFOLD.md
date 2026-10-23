# 骨架交付边界

日期：2026-10-19。基础工程搭建，不是 AI 审核 MVP 已完成。

## 目录

```text
frontend/src/
  app/                         # A：应用布局、主题、总路由
  shared/api/                  # A：HTTP 客户端、OpenAPI 生成类型
  shared/ui/                   # A：页头、状态、统计、流程和占位组件
  shared/lib/                  # A：公共时间格式
  features/projects/           # A：真实本地项目创建/列表/详情
  features/brief-script/       # B：页面与路由接入点
  features/video-model/        # C：页面与路由接入点
  features/human-review/       # D：页面与路由接入点
backend/
  app/core/                    # 公共配置、数据库、错误及数据契约
  app/modules/foundation/      # 已实现公共 API 与项目存储
  app/modules/brief_script/    # B：501 明确占位
  app/modules/video_model/     # C：501 明确占位
  app/modules/human_review/    # D：501 明确占位
  migrations/                  # 首次迁移 + 后续模块追加
  scripts/export_openapi.py
  tests/                       # 临时数据库基础测试
contracts/openapi.json         # 当前代码的生成契约
e2e/                           # 浏览器验证
scripts/                       # Windows 启动与 E2E 隔离启动
docs/                          # 协作、交付范围
openspec/                      # 完整产品规格
```

## 路由清单

| 路由 | 当前行为 |
|---|---|
| `/projects` | 真实读取本地项目，支持创建、筛选、分页 |
| `/projects/:projectId` | 真实查询项目；未知项目显示错误 |
| `/projects/:projectId/briefs/:briefId` | Brief 模块布局占位 |
| `/projects/:projectId/tasks/:taskId/script` | 脚本布局占位，操作禁用 |
| `/projects/:projectId/tasks/:taskId/video` | 视频布局占位，操作禁用 |
| `/reviews` / `/reviews/:reviewId` | 待办/复核布局占位 |
| `/settings/models` | 设置布局占位，不接收密钥 |

项目详情的开发链接使用 `new` 作为 UI 占位标识，只用于到达模块，不创建假任务或假 Brief。业务模块接入时需校验实际 ID 与项目归属，移除占位入口并改为真实创建/选择流程。

## 与原开发任务的关系

1.1 工程启动、1.4 公共框架已建立基础；1.3 仅项目相关迁移、模式字段及审计；1.5 仅项目创建幂等与公共错误契约。2.1 项目创建查询可用但编辑归档未做，2.2 项目概览有入口但达人任务未做，2.3 上传下载尚未做。其余业务任务没有完成，因此不把 OpenSpec 原任务整项勾选。

## 运行限制

- 当前只有 demo 空间，数据真实保存在本机，不自动填充图稿数值。
- 无模型调用、无 API 密钥保存、无完整视频审核或人工最终决定。
- 无业务 worker、OCR、Office 转换及上传存储服务；模块必须后续接入。
- 无认证，仅限本机开发；不是可直接上线的共享环境。
- 前端是开发工作台，尚未追求七图逐像素还原；业务页面为明确占位。

## 技术依据

工程入口遵循 [Vite 官方启动说明](https://vite.dev/guide/)；后端模块化路由与启动迁移采用 [FastAPI APIRouter](https://fastapi.tiangolo.com/tutorial/bigger-applications/) 和 [lifespan](https://fastapi.tiangolo.com/advanced/events/) 机制。依赖实际版本固定在 package-lock 和 requirements 中。
