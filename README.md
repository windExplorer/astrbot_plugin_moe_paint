# 萌萌绘卷（astrbot_plugin_moe_paint）

> 状态：**PRD 阶段，未动工**。PRD 见工作区 `docs/astrbot_plugin_moe_paint_PRD.md`（跨插件文档，不入本仓库，动工时在本仓库 `docs/` 落架构要点）。

定时自动绘图插件：按计划（固定间隔 / 每日时刻）用 LLM 生成高质量提示词与尺寸，
选定工作流交给 ComfyUI 出图，成功后投递到白名单群聊/私聊；每次生成全量记录落库，
配图库 WebUI（模型控制台同款插件 Page 形态）。

- **不依赖萌绘（astrbot-comfyui-anima）**：直连 ComfyUI HTTP API（客户端改编自萌绘的
  零依赖 `comfyui_client.py`，AGPL-3.0 同源，文件头注明出处）；两者共享同一台 ComfyUI 服务器时串行排队。
- LICENSE：AGPL-3.0（跟随萌绘同源代码要求）。
- 作者：云端之风 <1479221500@qq.com>（windExplorer）
