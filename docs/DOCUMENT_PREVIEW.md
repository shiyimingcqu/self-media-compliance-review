# 脚本原布局预览

达人提交页、品牌审核页和历史异议页共用预览。Word 保留文档表格、图片、样式和已保存的分页，PDF 按物理页显示；XLS/XLSX 使用交互表格预览；TXT 保留原始换行、缩进。

Word 与 Excel 支持审查意见和原布局双向定位。Word 预览副本按解析器的段落、表格单元格坐标加入零长度书签，仅对核对一致的原文高亮，并保留原字形和样式；编号标记可反向选择意见。缩放和滚动更新定位连线，放大窗口同样可用。图片意见可定位到其所在单元格。无引文的缺失项不伪造原文位置，无法核对渲染文本时回退到解析文字；PDF 暂仍使用解析文字定位。原文件、审核结论和证据引用均不改写，也不增加 AI 调用。

文档与图片预览默认适应宽度，提供缩放条和比例选择；放大后可按住鼠标左键拖动画面，触屏保留原生滚动，评论标记和其他交互控件仍可点击。“放大查看”打开大窗口。此交互复用于 Word、PDF、Excel、TXT、脚本图片、图文图片、Brief 原页和视频证据截图。

带单位的行距（例如 Strict 文档中的 `18pt`）只在 Word 预览副本中转成等值排版参数，避免预览库把 `18pt` 截成整数 `18` 再除以 240，产生 0.07 倍行距。标准的无单位自动行距不改变。原文件、解析文本、历史审核与送审材料均不改写。

对于记录为零宽度、但保存了有效列宽的表格，预览按原文 `tblGrid` 的列宽求和恢复宽度；已有的正宽度不覆盖，不用页面宽度猜补。固定表格布局也会适配给渲染器，避免其错误读取 `type` 后变成自动挤压布局。此修复同样只作用于预览副本。

## 转换依赖

- DOCX 默认在浏览器用 `docx-preview` 直接排版，不依赖本机 Office。文档样式通过 Shadow DOM 隔离，关闭 HTML 附件渲染和外链跳转，不执行宏。库按需加载。
- 旧 DOC/XLS 用 LibreOffice 生成 DOCX/XLSX 副本，解析和预览共用转换缓存。Linux / Docker 的 `deploy/backend.Dockerfile` 包含 Writer、Calc 和中文字体，下次重建镜像生效。
- 手动安装 LibreOffice 后，可通过 `REVIEW_LIBREOFFICE_PATH` 指定 `soffice` 可执行文件。`REVIEW_DOCX_PREVIEW=pdf` 可选用 LibreOffice 生成分页 PDF。此模式需转换依赖就绪，默认不启用。
- 文件不上传到外部转换服务，不调用 AI。原件不覆盖；派生文件缓存于数据目录的 `preview-cache/office-preview-v1`，按原文件内容哈希和格式复用。原件每次经过完整性与任务归属检查，跨任务不可读取。
- 转换超时、加密、损坏或依赖不可用时，页面保留解析文字、原件下载和重试入口。扫描 PDF 可以预览，但无文字层时不会冒充脚本解析成功。

不同 Office 版本、缺失字体可能影响换行或分页；浏览器 Word 排版保留已有分页，不能保证与桌面 Word 自动分页完全一致。预览用于对照，原件仍可下载核对。转换器不是隔离沙箱；沿用当前材料上传和访问边界。

## 本地验证

`pytest backend/tests/test_word_preview_spacing.py backend/tests/test_document_original_preview.py backend/tests/test_script_layout.py backend/tests/test_script_versions.py`

`npx playwright test e2e/document-original-preview.spec.ts`

固定响应测试不调用计费模型，覆盖分页尺寸、页图、越界、跨任务访问、原件变更、失败回退及原布局切换、缩放、重试。
