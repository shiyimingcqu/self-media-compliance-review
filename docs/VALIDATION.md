# 骨架验证记录

日期：2026-10-19（Asia/Hong_Kong）。以下为本次实际执行结果，仅验证公共骨架。

| 检查 | 结果 |
|---|---|
| 独立 `.venv` 安装 requirements-dev | 成功；非 system-site-packages 环境 |
| `.venv` 执行后端测试 | 12 项通过，使用临时数据库 |
| `.venv` 执行 pip check | No broken requirements found |
| OpenAPI 导出和 TypeScript 类型生成 | 成功，共享状态、证据与审核结果形状已生成 |
| 前端 typecheck 与生产构建 | 通过；页面采用按路由加载，构建无大块警告 |
| 前端 Vitest | 4 项通过 |
| Playwright + 本机 Chrome | 1 项流程测试通过，覆盖创建、刷新持久化、七页/复核详情及 768px 降级 |
| 浏览器运行异常 | 测试流程无 pageerror |
| OpenSpec 严格校验 | 通过 |
| 本地服务 | 后端 8000，前端 5175；默认 5173 被另一个项目占用，未改动原服务 |

网络环境中 pip 直接下载遇到 TLS 连接问题；本机通过官方 PyPI 元数据下载匹配 wheel、逐个验证 SHA-256 后，在独立虚拟环境离线安装成功。wheel 缓存在忽略的 `.venv/wheelhouse` 中，不提交仓库；其他成员按 README 正常安装即可，若同样遇到网络问题需使用各自可信网络/代理。

Playwright 使用已安装 Chrome，未要求业务代码依赖本机浏览器；CI 仍按配置安装 Chromium。浏览器测试数据库和开发数据库隔离，测试项目没有注入开发空间。

尚未验证：Brief/Office 解析、文件上传存储、规则启用、脚本/视频 AI、完整音轨分析、后台业务 worker、人工最终决定、真实模型能力、生产部署。当前 501 占位和禁用按钮是明确的未实现状态，不是业务失败重试或假通过。
