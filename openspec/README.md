# 达人营销智能审核平台 · OpenSpec 导航

本目录根据用户提供的七张界面图及此前明确的审核业务需求整理，描述完整产品的规划基线。2026-10-19 已按用户后续授权建立前后端公共骨架；项目创建查询可运行，其余审核业务尚未实现，供应商完整视频能力尚未验证。当前代码范围见 [骨架说明](../docs/SCAFFOLD.md)，不能把规划齐备理解为产品全部完成。

## 阅读顺序

1. [proposal.md](changes/creator-review-workbench/proposal.md)：范围、目标和能力清单。
2. [页面详设](changes/creator-review-workbench/page-details.md)：七个页面的布局、组件、字段、按钮、状态与图稿修正。
3. [specs](changes/creator-review-workbench/specs)：八个能力的规范性要求及验收场景（七页加公共行为）。
4. [design.md](changes/creator-review-workbench/design.md)：架构、路由、复用组件、状态流转与实现决策。
5. [数据与接口](changes/creator-review-workbench/data-and-api.md)：实体、字段、接口及错误契约。
6. [验收与图稿映射](changes/creator-review-workbench/acceptance.md)：从七张图到验收项的对应关系。
7. [tasks.md](changes/creator-review-workbench/tasks.md)：一周实施顺序与验证方式；所有任务尚未执行。
8. [校验记录](changes/creator-review-workbench/validation.md)：OpenSpec 严格校验结果及尚未执行的验证边界。

## OpenSpec 使用

本地通过 npx 使用固定版本 OpenSpec 1.14.3，初始化时选择 `--tools none`，没有安装全局 CLI，也没有生成编辑器专属指令。

```powershell
npx --yes @fission-ai/openspec@1.14.3 status --change creator-review-workbench
npx --yes @fission-ai/openspec@1.14.3 validate creator-review-workbench --strict
```

这是首次建设的活动变更，新增要求放在 `changes/creator-review-workbench/specs/`，尚不复制到 `openspec/specs/`。完成实现并验收后再归档合并，避免把计划写成已实现现状。

## 依据与约定

- 视觉依据：[图片目录](../图片)，页面编号 01–07。
- 正文中“图稿”指视觉参考，“业务修正”指与用户明确范围或样例结论对齐，“建议默认”指为了让规格可执行而提出的可配置数值。
- 图片中的头像、商品图和数据仅用于视觉占位；不能伪造成真实项目、真实审核结果或真实统计。
- OpenSpec 结构参考官方文档：[CLI](https://github.com/Fission-AI/OpenSpec/blob/main/docs-lab/reference/cli.md)、[Getting started](https://github.com/Fission-AI/OpenSpec/blob/main/docs/getting-started.md)。
