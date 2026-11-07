# 更新日志

## v0.7.15（负向注入事故：写进 ZeroOut、甚至覆盖正向）

用户反馈：**有的工作流不需要负向；有的工作流把负向填到了正向里**，并给出了一份
真实工作流（krea2 / Qwen 系）：

```
KSampler.positive = ["8", 0]        # 8 = CLIPTextEncode（唯一文本节点）
KSampler.negative = ["6", 0]        # 6 = ConditioningZeroOut(["8", 0])
steps=8, cfg=1
```

### 根因

1. `locate_nodes` 直接拿采样器 `negative` 的引用当文本节点，没穿透
   `ConditioningZeroOut` 这层包装 → 把节点 6 当负向文本节点；
2. `inject` 无脑执行 `wf[neg]["inputs"]["text"] = negative` —— 节点 6 没有 `text`
   输入，等于写了个无效字段；
3. 更严重：一旦负向引用**指回正向同一个节点**（本工作流里 ZeroOut 的目标就是正向
   节点 8；模板里 `negative_node` 填错也会这样），负向写在后、正向写在前，
   **正向被整段负向覆盖** —— 于是负面词出现在正面提示词里，画出来自然一塌糊涂。

### 修复

- 新增 `_resolve_text_node()`：沿 `conditioning` 链穿透
  `ConditioningZeroOut / SetArea / SetAreaPercentage / SetTimestepRange /
  Combine / Concat / Average / SetMask / ControlNetApply` 等包装类，找到真正带
  `text/prompt` 字段的节点；
- 负向判定「不可用」并**跳过注入**的三种情况：被 `ConditioningZeroOut` 零化、
  与正向指向同一节点、模板 `negative_node` 覆盖后指回正向节点；
- `inject` 三重校验：目标存在、不是正向节点、**确实有**该文本字段才写；
- 不可用不算错误：不再报「找不到负向文本节点」，改成一条软提示
  「不使用负向：…（已跳过负向注入）」，模板行可见；
- **不按 CFG 禁用**：模板的采样参数档可能把 CFG 从 1 改回 6，那时负向就该生效，
  所以只处理「结构上写不进去」的情况。

### 顺带修复：底模误判

`UpscaleModelLoader / VAELoader / CLIPLoader / LoRA / ControlNet` 也带 `model_name`，
此前会把放大模型（如 `RealESRGAN_x4plus.pth`）当成底模，导致提示词风格与尺寸预设
跟着错。现在这些加载器会被跳过（找不到再回退），认得真正的 UNET / Checkpoint。

### 测试

- `test_workflow_mgr` 用**用户给的真实工作流结构**做 fixture，增补 12 条：
  正向解析、负向判定与跳过、节点 6 不再被塞 `text`、正向不被覆盖、
  同节点保护、包装类（SetArea）穿透、底模不被放大模型抢走、正常工作流负向照常写入。
- 9 个自检全 PASS。

## v0.7.14（立即画一张可以选择工作流）

需求：「怎么在 WebUI 中手动选择工作流立即画一张」—— 之前只能按
`workflow_selection`（随机 / 顺序轮换）走，想单独用某个模板试画只能去改配置。

- 总览页「立即画一张」旁边新增工作流下拉：**工作流：自动**（按原规则）+ 所有
  **启用且可用**的模板名。
- 链路：`/control {action:"trigger", theme, template}` → `trigger_manual(template=)` →
  `generate_one(template=)` → `_plan(template_hint=)`：命中就直接用该模板，
  不推进轮换游标（手动出图本来就不该占位）。
- 兜底：指定的模板不存在或被停用时**退回原规则**照常画，并在调试日志里写一句
  「指定的工作流「X」不可用」，不会因为选错名字让整轮失败。
- 主题词框仍然支持 `--no / --style / --ratio / --seed / --char`。

### 测试

- 9 个自检全 PASS（本次是接线改动，纯函数未变；编译 + 前端构建通过）。

## v0.7.13（3D 风格事故：风格层与画风锚定互斥）

用户定位到根因：**风格层抽到了 3D 类型**（「皮克斯式 3D 质感」/「游戏 CG 质感」）。

链路：`STYLES_ANIME` 里含 3D/CG 风格 → 抽中后简报写「皮克斯式 3D 质感」→ LLM 写
`3d, pixar, octane render, cg` → 而动漫硬锚定的负向同时压着 `3d render, cgi`。
**正负向自相矛盾**，模型只能折中：3D 的高光塑料感 + cel shading 的硬边 → 脸色发灰、
对比过强、既不插画也不 3D，正是「说不出来哪里不对、一点都不柔和」。

### 修复（两层）

1. **抽签层**（`style_pool_for(..., allow_3d=False)`）：有硬锚定时过滤风格池 ——
   动漫锚定剔除 `3d / 三维 / c4d / cgi / cg / 渲染 / 皮克斯 / blender / unreal / octane`，
   写实锚定剔除插画类（`插画 / 赛璐璐 / 厚涂 / 水彩 / 浮世绘 / 漫画 / 动画 / 立绘 / 轻小说`）。
   `scope=both` 没有硬锚定，不过滤（两种都能画）；过滤后为空时退回原池，绝不抽不出。
2. **提示词层**（`strip_scope_conflicts`，挂在冲突压缩里）：兜底把与锚定矛盾的词
   从正向挑出来写进负向 —— 动漫锚定删 `3d / 3d render / cgi / cg / render /
   octane render / unreal engine / blender / pixar / photorealistic / photograph /
   realistic / live action`；写实锚定删 `anime / 2d / illustration / cel shading /
   watercolor / manga…`。仅在「画风硬锚定」开启时生效。

新增开关 `prompt.style_allow_3d`（默认关）：想画 3D 二次元（游戏 CG 风）时打开，
两层限制同时放开。用户自己在「主题词池 → 高级 → 风格」里加的 3D 词不过抽签层过滤
（显式意图），但提示词层仍会删 —— 要 3D 请用开关。

### 前端

- 提示词页「提示词冲突压缩」卡新增「允许 3D / CG 风格」开关。

### 测试

- `test_prompter` 增补 16 条：动漫池不含 3D/CG、允许开关后恢复、both 不过滤、
  50 次抽签不出现 3D 风格、动漫/写实两个方向的剔除集合、allow_3d 只放开 3D 类、
  `scope=both` 不剔除、锚定关闭时不剔除、冲突压缩里的 3D 词进负向。
- 旧断言按新语义调整（风格池现在返回过滤后的副本，用集合包含而非 `is`）；
  `test_schema` 增补 1 个新键；9 个自检全 PASS。

## v0.7.12（图库筛选修历史工作流；提示词冲突压缩，治「又艳又深像赛博朋克」）

### 一、图库筛选只能选到启用中的工作流

`workflowOptions` 原来只取 `status.templates`（= 当前启用的模板池），停用或删除的
模板对应的历史图片就再也筛不出来了。

改为三方合并：`stats.by_workflow`（全量历史）+ 当前模板 + 本页记录；不在启用池里的
标成「（已停用）」，选中后照常按 `workflow=` 过滤。

### 二、anima 底模出图「色彩艳丽、太深、像赛博朋克、不柔和」

用户给了一份真实提示词，问题一眼可见：**一句话里塞了 7 个光**。

```
rainy night, neon lights, twilight, golden hour, warm light, candlelight,
cinematic lighting, glowing signs ... simple background, wet pavement, empty street,
banner composition, game promo, close up, upper body
```

- 7 个光互相矛盾（雨夜 / 黄昏 / 暖光 / 烛火 / 霓虹 / 电影光）→ CLIP 只能取平均，
  结果是又艳又脏、对比拉满、橙+霓虹混成赛博朋克；
- `simple background` 与「湿漉漉的空街」互斥 → 背景被折中成一团；
- `banner composition` / `game promo` 与 `close up` / `upper body` 互斥 → 构图被拉成
  宣传图那种高对比怪构图。

修复（`prompter.reduce_conflicts`，`prompt.conflict_cleanup` 默认开，仅 danbooru）：

- **光源做减法**：按简报光色选主组（霓虹 → 只留 neon / glowing signs），同组最多 2 个，
  其它组（golden hour / warm light / candlelight）删掉；
- **时段只留一个**：雨夜保留 `rainy night`，`twilight` 删掉；
- **背景互斥**：有具体场景就删 `simple background`；纯色与 `blurry background` 二选一；
- **取景互斥**：特写/半身时删 `full body / banner composition / game promo / wallpaper`；
- 被删的标签**写进负向**（只删不压制，模型会自己补回来），调试页显示「压掉 N 个冲突词」。

顺带两处相关修正：

- 人物锚定的 `asian / east asian` 是摄影线标签，会往写实与偏深肤色拉，而负向里还写着
  `dark skin` —— danbooru 线默认剔除（`prompt.persona_strip_photo`）；
- 动漫「成品形态」在人物近景时改抽立绘 / 关键帧 / 分镜特写，不再抽壁纸 / 游戏宣传图 /
  跨页这类横幅构图（它们会跟 `close up` 打架）。

另加可选项 `prompt.soft_color`（默认关）：追加 `soft lighting, pastel colors,
gentle shading`，负向加 `oversaturated, high contrast, harsh shadows`。嫌艳就直接开。

### 前端

- 图库筛选：历史/已停用工作流可筛（标注「已停用」）。
- 提示词页新增「提示词冲突压缩」卡（压缩开关、柔和色彩、剔除摄影标签）。
- 调试页 LLM 试跑新增「压掉 N 个冲突词」标签（悬停看具体词）。

### 测试

- `test_prompter` 用**真实翻车提示词**当 fixture，增补 25 条：霓虹/黄昏/烛火三种主光
  下的增减、无简报时的收敛、背景互斥、取景互斥（特写/全景双向）、负向承接、
  自然语言不处理、开关关闭、柔和色彩、摄影标签剔除、近景成品形态池。
- `test_schema` 增补 3 个新键守卫；9 个自检全 PASS。

## v0.7.11（工作流留档：每张图都能看到真正提交的完整工作流）

需求：「有的图片有问题，感觉是提示词的问题，但没有完整工作流看不出来」。

问题本质：记录表里只有 `prompt` / `negative` / `seed` 这几个**结果字段**，真正决定
画面的是**注入后的那份 ComfyUI 图**（文本节点被替换成最终串、latent 被换成实际宽高、
所有 seed 节点被统一、采样参数可能被模板档覆盖）。没有这份图，就无法区分「LLM 写错」
「注入写错」「参数档写错」「模型画不出」。

### 一、留档

- 新增表 `run_workflows(rec_id, kind, ts, bytes, template, json)`，主键 `(rec_id, kind)`：
  `main` = 出图那张，`refine` = 精修那张（开了精修才有）。
- 在 `inject()` 之后、`queue_prompt()` 之前留档：存的是**真正提交**的那一版；
  空图重试时会覆盖成最后成功的那版（同主键 UPSERT），不会留下没跑过的旧图。
- 独立表而不是给 `generations` 加列：图库列表是 `SELECT *`，几百 KB 的 JSON 挂主表会
  让每次翻页白读一遍。
- 单条上限 512KB（正常 4~60KB），超过跳过并记日志；删除记录时连带删快照
  （`delete_record` 一并处理）。

### 二、查看

- 新接口 `POST /record/workflow {id}` → 返回该记录全部快照（含模板名、字节数）。
- 图库详情新增 **「查看工作流」**：弹窗里是缩进后的完整 JSON，可切换出图/精修，
  支持**复制**与**下载 .json**；`has_workflow` 由列表查询用主键子查询带出，
  旧记录按钮自动置灰并给出原因。
- 「调试与日志」页新增「工作流留档」卡：`debug.save_workflows`（默认开）、
  `debug.keep_workflows`（默认 200 条，按最旧清理，0 = 不限）。

### 三、排查建议（写进 README）

| 现象 | 在 JSON 里看 |
| --- | --- |
| 画面不像提示词 | `prompt` 节点：确认注入文本，注意质量词/锚定词是否把它冲掉 |
| 缺东西/多东西 | `negative`：负面基线、禁词、角色排除词、动物反拟人词是否加过头 |
| 尺寸构图不对 | `width` / `height` |
| 每张都糊 | `steps` / `cfg` / `sampler_name`（模板参数档、turbo 底模） |
| 画面抖动 | `seed`（所有 seed 节点应一致） |
| 精修后细节怪 | 切到「精修图」看 `denoise` / `scale_by` |

### 测试

- `test_store` 增补 8 条：快照写入/读回、同键覆盖、main+refine 两路、列表
  `has_workflow` 标志、保留条数清理、删记录级联删除。
- `test_schema` 增补 2 个新键守卫；9 个自检全 PASS。

## v0.7.10（主体收口：动物不拟人、人物拍近景）

两条来自实际使用的反馈：

> 「有时候画动物就画动物，为啥动物变成了福瑞」
> 「画人的话多来点近景，多一点人物细节，背景其次」

### 一、动物题材不再画成福瑞

根因：动漫底模对「人形主体」有极强先验。只写「竹林间回望的赤狐」，模型会自动
补成人形兽耳娘 / 兽人（danbooru 里的 `anthro` / `furry` / `kemono`）。此前插件
既没有正向硬标签、也没有任何反拟人反向词，光靠简报文字压不住。

修复（`prompter.subject_kind` / `apply_subject` / `_SUBJECT_RULES`）：

- 正向前置 `animal focus, no humans`（位置紧跟质量词，权重最高）；
- 反向词一整套：`human, humanoid, anthro, furry, kemono, personification, human face,
  human hands, clothes, standing on two legs, 1girl, 1boy`；
- 自然语言线用中文/英文各一套反向词（福瑞 / 拟人化 / 穿衣服的动物 / bipedal animal…）；
- 简报补 `主体=动物（真实形态）`、`形态要求=真实动物（自然姿态，非拟人、不穿衣服、不直立）`；
- 系统提示词追加主体硬约束，即使「完全覆盖系统提示词」也照样追加。

判定还看题材：自然生态里的「苔藓与蘑菇的微距世界」不套动物护栏（套了会画错），
只有题材里出现动物词（狐/犬/鸟/水母…）才生效。类别可用
`prompt.animal_categories` 扩展，护栏可用 `prompt.animal_guard` 关闭。

### 二、人物题材默认近景 + 细节 + 背景次要

根因：构图此前是从 10 项里**均匀抽**（超广角远景、全景、俯瞰、中景、特写、半身、
全身、仰视、对称、留白），人物题材有一半概率抽到横幅远景，人物只占画面一小块；
提示词里也没有任何面部/皮肤细节词。

修复（`prompt.person_closeup` / `prompt.person_detail`）：

- 人物类别构图池改为加权：特写×2 / 半身像×2 / 中景×1 / 全身像×1（约 83% 竖幅近景）；
- danbooru 追加 `detailed face, beautiful detailed eyes, detailed skin, sharp focus,
  depth of field`，负向压掉 `busy background, cluttered background`；
- 简报追加「取景=近景优先（特写/半身像），人物占满画面，背景次要可虚化」；
- 系统提示词追加取景要求（close-up / upper body，背景只写一句）。

两个开关都能单独关：关 `person_closeup` 恢复原构图池，两个都关即完全回到 v0.7.9。

### 前端

- 「提示词」页新增「主体收口（动物 / 人物取景）」卡：动物护栏、动物类别、
  人物偏近景、人物细节优先。
- 调试页 LLM 试跑新增「主体」标签（动物 / 人物 / 景物）。

### 测试

- `test_prompter` 增补 22 条：主体类型判定（含「蘑菇微距不算动物」「自定义动物类别」）、
  动物正负向拼接与关闭开关、自然语言反向词、人物细节词与背景负向、
  人物构图池子集与竖幅占比、系统提示词主体规则（含完全覆盖时仍追加）。
- `test_schema` 增补 4 个新键守卫；9 个自检全 PASS。

## v0.7.9（时间一致性、角色卡、提示词 A/B）

补齐 v0.7.8 末尾列的三个「下一步」：让图对得上时间、对得上人、比得出哪套提示词更好。

### 一、时间一致性（`daylight.py` + `time_sense.*`，默认开）

问题：定时出图不看时钟——凌晨三点抽到「黄昏暖光」，或让 LLM 写出「正午的烈日」，
画面与时间打架，而插件里没有任何一处知道「现在几点」。

做法（不引入天文库/时区库，纯标准库）：

- `daylight.sun_hours(month, latitude)` 用太阳赤纬估算日出日落（纬度夹到 ±66° 避开
  极昼夜奇点），`phase_of()` 把时刻折成 深夜 / 黎明 / 上午 / 白天 / 黄昏 / 入夜 六段；
- 抽签的光色池按时段收窄（深夜只剩月光/霓虹/星云/极光/烛火），**用户自己追加的光色
  词不过滤**——那是显式意图；
- 系统提示词追加一条时段硬规则（三套提示词与「完全覆盖」都会带上）；
- 出图前扫一遍提示词，命中「深夜写正午烈日」这类矛盾就带原因纠偏重写一次；
  重写失败保留原样（出图比卡住重要）。
- `sync_light` / `guard_retry` / `enabled` 三级开关，全关即回到 v0.7.8 行为。

### 二、角色卡（`character.*` + 「主题词池」页卡片）

问题：想让同一个人物跨图长相一致，只能每次在主题词里手写标签，且标签会散落在
LLM 自由发挥的提示词里。

做法：一张卡 = 名称 + 别名 + 固定标签 + 排除词，存 `meta`（`char:<名>`，不占主表）。

- 名称写进创作简报（LLM 知道画谁），danbooru 风格把固定标签**前置**进正向；
- 排除词并入负向，挡住「画成别人」的常见描述；
- 自然语言风格不插标签串（会破坏句子），只靠简报里的角色名；
- 三种用法：`random` 每轮抽一张 / `fixed` 固定某张 / 指令 `--char 名字` 临时指定
  （优先级最高，命中别名也算）。

### 三、提示词 A/B（`prompt.ab_*`）

问题：改系统提示词全靠感觉，改完不知道是不是真的更好。

做法：两套「附加规则」按 A→B 轮换追加到系统提示词末尾，用哪一版记进记录
（新增 `ab` 列，老库自动补列）。配合 v0.7.8 的成图自检，`/stats` 的 `by_ab`
给出各变体的平均分与样本数；总览「平均质检」直接显示 `A 78.2（12） B 74.9（12）`。
A、B 都填才生效，只统计有分数的样本。

### 四、前端

- 生成服务页新增「时间一致性」卡（纬度 / 光照跟随 / 矛盾重写）。
- 主题词池页新增「角色卡」卡（使用方式、固定角色、逐张编辑标签与排除词）。
- 提示词页新增「A/B 测试」卡（开关与两版规则）。
- 总览「平均质检」带 A/B 对比；调试页 LLM 试跑显示「时段 / 角色 / 已按时段纠偏」。

### 打包

- `daylight.py` 已加入 `build_zip.ps1` 的 `$includeList` 与 `main.py` 的热重载列表
  （新增顶层模块最容易漏的两处）。

### 测试

- `test_prompter.py` 增补 30 条：日出日落区间、六个时段判定、光照池收窄、
  时段规则文本、矛盾命中/不命中、简报与系统提示词带时段、角色标签前置与负向合并、
  `--char` 解析。
- `test_store.py` 增补：`ab` 列落库与 `by_ab` 统计口径。
- `test_schema.py` 增补新键守卫（time_sense / character / ab / quality_guard / refine）。
- 9 个自检全 PASS（`test_store` 建议带 `--with pillow` 才会跑像素体检分支）。

## v0.7.8（画质流水线：提示词清洗、负面基线、空图体检、成图自检、连拍选优、精修、重画与收藏）

需求：「还有什么可以加的功能吗，特别是图片质量相关的、提示词相关的」。按「提示词质量 →
出图质量 → 采样参数与精修」三批做完，共 16 项。

### 一、提示词质量

1. **danbooru 标签清洗管道**（`prompter.normalize_tags` / `normalize_payload`）。
   此前 LLM 输出是**原样**注入的：`(tag:1.2)` 权重、`hatsune miku (vocaloid)` 的括号、
   重复标签、`artist:xxx`、超长标签串全都直接进 CLIP。其中括号最致命 —— CLIP 把 `()`
   当权重语法，角色 tag 会被算坏（萌绘踩过同一个坑）。现在按「解权重 → 转义括号 → 去重
   → 下划线转空格 → 剔除画师名 → 按预算截断」处理，自然语言风格原样跳过（中文正文里
   的括号是正常标点）。
2. **负面基线**（`prompt.negative_base_en` / `negative_base_zh`）：此前 negative 只有
   「LLM 自己写的 + 人物排除词 + 画风反向词」，模型偷懒给空串时毫无兜底。现在英文线
   （danbooru / natural_en）与中文线（natural）各一套通用缺陷词，永远垫在最前；缺键用
   代码常量兜底，显式空串 = 用户主动关掉。
3. **禁词与追加质量词**（`prompt.banned_tags` / `quality_extra` / `strip_artists` /
   `max_tags`）：禁词从正向剔除**并自动写进负向**（只删不加等于没管住）；标签上限默认
   75（SD1.5 的 CLIP 只有 77 token），显式 0 = 不限。
4. **镜头 / 成品形态层**：写真线多抽一层镜头（85mm f/1.8 浅景深…），动漫线多抽一层
   成品形态（角色立绘 / 轻小说封面 / 壁纸构图），写进简报由 LLM 转成目标语言。
5. **指令开关**：`/绘卷 画一张 少女 --no blurry,watermark --ratio 3:2 --seed 42`
   （还支持 `--style`），控制台「立即画一张」的主题词同样认；`--ratio` 会覆盖构图推断的
   长宽比。纯函数 `prompter.parse_draw_args`。

### 二、出图质量

6. **空图体检**（`gallery_store.suspect_blank`）：ComfyUI 在底模/VAE 不匹配时**不会报错**，
   而是返回一张全黑 PNG，以前会被当成功存档并投进群里。现在按像素分布判全黑 / 全白 /
   纯色 / 体积过小，命中就换种子重试一次，仍失败则写清原因。
7. **成图自检**（`quality_guard.*`，默认关）：多模态模型给成品打分（结构崩坏 / 清晰度 /
   与描述相符 / 完成度），分数与理由落库（新增 `score` / `score_reason` 列，老库自动补列），
   图库卡片显示、详情可看、可「试评分」校准阈值。
8. **连拍选优**（`quality_guard.shots`）：同一份提示词换种子连拍 1~4 张，只投递分数最高
   的那张，其余留在图库（可手动重投）。低分时按 `retry` 次数换种子重画（复用提示词，
   不再多花一次 LLM）。
9. **重画与提示词收藏**：图库详情新增「同 seed 重画」（几乎复现）/「换 seed 变体」/
   「收藏此提示词」；收藏存 meta（`preset:<名字>`），图库顶部可一键再画。重画跳过抽签与
   LLM，也**不占轮换位**（用户只是想再来一张，不是「轮到下一张」）。
10. **精修 pass**（`refine.*`，默认关）：出图后上传回 ComfyUI，走插件内置的标准精修图
    （CheckpointLoaderSimple → 放大 → VAEEncode → KSampler denoise 0.35 → Save），
    不需要用户模板配合；失败一律退回原图。精修产物固定 PNG（扩展名跟着改）。

### 三、采样参数

11. **采样参数档**：`locate_nodes` 现在会记下采样器节点，`inject` 支持 steps / cfg /
    sampler_name / scheduler 覆盖，但**只写工作流里真有的字段**（异形采样链硬塞会让
    ComfyUI 直接拒绝），写不进去时在模板行给警告。模板新增 `params_preset`
    （keep / fast / standard / high）与可选的 sampler / scheduler 覆盖。

### 四、前端

- 图库：卡片带质检分（低分红字）、详情新增三个按钮与提示词收藏、顶部「提示词收藏」卡。
- 生成服务页：新增「成图自检」与「精修 pass」两张卡（含阈值、连拍、模型、检查点）。
- 工作流模板页：每行新增「采样参数档」下拉。
- 提示词页：新增「负面基线与标签清洗」卡（中英负面基线、追加质量词、禁词、去画师、
  标签上限）。
- 总览：新增「平均质检」指标（已评分数 / 低分张数）。
- 调试页：LLM 试跑显示「标签 N」（超 75 变黄）与指令参数错误提示。

### 测试

- `test_prompter.py`：规范化（解权重 / 括号转义 / 幂等 / 去重 / 去画师 / 禁词 / 截断）、
  负面基线（英中 / 去重 / 可关）、镜头与成品形态按 scope 抽、指令参数与比例解析、
  质检契约解析（围栏 / 字符串分数 / 越界夹取 / 垃圾输入）、`pick_size` 显式比例。
- `test_workflow_mgr.py`：采样器定位、参数注入只写已有字段、preset 与显式值优先级、
  内置精修图结构（节点 / 连线 / 边界夹取）。
- `test_store.py`：meta 前缀列表与删除、质检分落库与统计、空图体检（全黑 / 纯色 /
  正常图不误判；无 Pillow 时只跑体积兜底）。
- 9 个自检全 PASS。

## v0.7.7（稳定性整修：投递不再改写状态、图库淘汰可解释、手动出图加闸门）

一次通读全模块后的整修，共 15 处，按「会写错数据」→「能刷爆」→「看起来不对」排序。

### 一、会写错数据

1. **投递异常会把成功记录改成失败**（`main.py::_generate`）。原先生成与投递共用同一个
   `try`，`event.send` / `deliver_record` 一抛异常就落进 `except` 把刚写完的 `success`
   改成 `failed`。后果：图已经在 `gallery/` 里、`image_relpath` 也有，但记录状态是失败，
   而图库前端只给 `success` 加载缩略图 —— 用户看到「画出来了但显示失败且没图」，
   调度器那边却按 `summary["ok"]` 算成功，两边口径不一致。
   现在投递独立成一段，失败只写 `delivery_error` + 日志 + `delivered_json`，绝不动状态。
2. **图库 LRU 淘汰只删文件、不处理记录**（`gallery_store.prune_gallery`）。被淘汰的行
   一直指着已删除的路径，图库里就是永远加载失败的破图，且不会自愈。
   新增 `pruned` 列（老库自动 `ALTER TABLE` 补列）+ `Store.mark_pruned()`：淘汰时同步
   标记并清空 `image_relpath`，前端显示「已按容量清理」而不是转圈。
3. **关键词搜索的 `%`/`_` 没转义**（`list_records`）：搜「100%」等于搜全部。
   改成 `LIKE ? ESCAPE '\'` + 模式串转义。
4. **`update_generation` 静默丢弃白名单外的列**：以后加字段忘了改 `_UPDATE_COLS` 会
   表现成「写成功但没存」。现在会打 warning。
5. **`asyncio.CancelledError` 会留下「生成中」死行**：`except Exception` 抓不到它
   （3.8+ 是 BaseException）。`_generate` 补了专门分支，取消时也把记录收尾成失败。

### 二、能被刷爆 / 白等

6. **`/绘卷 画一张` 没有权限与冷却**：任何群成员都能触发，每次独占生成锁几十秒到几分钟，
   而调度器遇到锁占用会把到点的那一轮整轮顺延 —— 等于谁都能挤掉定时出图。
   新增 `manual_draw_admin_only`（默认开）+ `manual_draw_cooldown_sec`（默认 60s，
   按会话 UMO 记账），控制台「生成服务」页有开关。
7. **暂停很久后恢复会连续空转 + 刷日志**：四个「跳过」分支（安静时段 / 上一张仍在生成 /
   每日上限）都是 `advance(cfg, 陈旧的 next_due)`，久停后会在一瞬间连跳上百次、每次都写
   一条 info。新增纯函数 `scheduler.skip_next()`：一次追到未来（仍按原网格步进，只保留
   一条日志），`tests/test_scheduler.py` 覆盖。
8. **控制台每 10 秒做一次全量模板重载 + 两次 ComfyUI 请求**（`/status`）：重载要读 JSON /
   SQLite；跟随萌绘时还会走 `_load_from_base()`，那个方法内部每次都打「【底模】…」日志，
   于是用户开着总览页，AstrBot 日志被刷成流水。现在 `/status` 用
   `templates_for_ui()`（只在从未加载时补一次），探活结果缓存 20 秒
   （调试页的「测试连接」仍走 `debug_comfy()` 真探）。
9. **内容寻址去重只在本月生效**：`archive_image` 只看当月目录，上个月的同一张图再抽到会
   重新落盘。现在先 `glob` 历史月份复用已有文件。
10. **图库淘汰的扫描成本**：仍是每次成功出图后全量 `rglob`（要算总量才知道有没有超限），
    没超限会立刻返回。数据量到万张级别再考虑改成 meta 记账式统计，现在不动。

### 三、一致性与小坑

11. **配置布尔强转**：`delivery.caption_on` / `enabled` 用 `bool()`，字符串 `"false"`
    被当成真（开关形同没有）。`config_coerce.truthy()` 统一收敛，`delivery` / `main` /
    `scheduler` / `webui_api` 全部改用它（之前每个模块各抄一份，这次删掉重复实现）。
12. **`ensure_future` 不接住返回值**：asyncio 只持弱引用，task 可能被 GC，异常也无人检索。
    新增 `plugin.spawn()` 统一记账（`terminate` 时取消），`trigger_manual` 与客户端重建都走它。
13. **`context.get_using_provider()` 已废弃**（4.28 起标 `@deprecated`），而它每次 `/status`
    都被调用。`_provider()` 改 async，优先 `get_using_provider_async()`，老版本再退回同步版。
14. **跟随萌绘时 danbooru 质量词没兜底**：本地库认不出底模仍会补
    `masterpiece, best quality…`，跟随萌绘却给空串。两边口径统一。
15. **杂项**：投递名单去重（手改配置重复一行不再重复发）、模板改名后清理不再被引用的
    旧 JSON（只删这一条留下的那个，绝不批量清用户素材）、缩略图回退时按真实后缀给
    MIME（不再一律 `image/png`）、`main.py` 顶部补上 `config_coerce` 导入
    （此前只有 `global` 声明）。

### 前端

- 调度页切换「分钟 / 小时 / 天」时**保持总时长不变**（6 小时 → 0.25 天，除不尽退回分钟）。
  以前点一下「天」，6 小时直接变成 6 天（8640 分钟）。
- 「随机浮动」文案 `±N 分钟` → `（+0~N 分钟）`：实现一直只往后加，写 ± 会让人以为会提前。
- 图库「类别」下拉改用 `stats.by_category`（全量历史），不再只从当前页 24 条里现算。
- 图库/总览对 `pruned` 记录显示「已按容量清理」，详情里禁止重投（图已不在）。

### 测试

- 新增：`truthy` 7 条（`test_config_coerce`）、`skip_next` 4 条（`test_scheduler`，含久停
  追平与 daily 落回未来）、跨月去重与淘汰标记 4 条（`test_store`）、新配置键守卫 2 条
  （`test_schema`，含「手动出图默认仅管理员」）。
- `tests/` 9 个自检全 PASS。

## v0.7.6（画风硬锚定：只画动漫不再只是一句空话）

现象：「我虽然设置了全部都用动漫风格，但是提示词里面并没有任何『动漫』相关的字眼，
导致有的工作流还是画的真人，不仅如此，还画的很丑，甚至出现老人、男人之类的」。

根因（六处叠加）：

1. `scope`（可画类型）**从来没有进过提示词**。`brief["scope"]` 在 `brief_text()` 里被丢掉；
   `build_system_prompt(tag_style, opts)` 根本不接收它。设「只画动漫」只是让抽签跳过写真/肖像类，
   LLM 收到的简报与系统提示词跟没设之前完全一样。
2. `tag_style` 是按底模**独立**自动识别的。某条工作流被认成 Flux / 写实底模时，
   系统提示词用的是 `natural_en` 那套，里面明写 "photorealistic SDXL checkpoints"、
   "no tag dumps" —— 等于主动往写实方向写，和可画类型完全脱节。
3. `style_pool_for(category)` 只看类别不看工作流可画类型。风景 / 星空 / 艺术 / 生态是
   `both` 类，设了只画动漫照样抽得到，用的是全局风格池（含「胶片摄影颗粒感」
   「暗调油画」「皮克斯式 3D 质感」）—— 动漫工作流抽到「胶片摄影颗粒感」，
   简报里就写着胶片，LLM 自然往照片写。这是「画得很丑 / 画真人」的主因。
4. `apply_persona()` 第一道 `if category not in PERSON_CATEGORIES: return`：景物类、
   艺术类、人物肖像类**完全不加**任何排除负面词，正面人物标签也没有。
5. 系统提示词里虽有中文的 `persona_text` 规则，但 `natural_en` 主体是英文、插一段中文
   效果弱，LLM 也常不遵守纯自然语言约束；用了 `override_*` 完全覆盖时这段规则整段消失。
6. `natural_en` 风格不加质量词（只有 danbooru 会补 `masterpiece, best quality...`），画质没兜底。

### 修复

**A. 写进输入**

- `brief_text()` 输出新增「可画类型=只画动漫（二次元插画）/ 只画真人（写实摄影）/ 两者皆可」。
- `build_system_prompt(tag_style, opts, scope)` 新增 scope 参数，按三套提示词风格各给一段
  中/英文画风硬规则（必须二次元插画 / 必须写实摄影、禁止反向画风）。
  **完全覆盖时也照样追加**——它是插件级硬约束，不是提示词模板内容，避免用户改完提示词又画成真人。
- 用户补充的边界：风景 / 星空 / 艺术 / 生态**不是中性画风**，也得跟着工作流的可画类型走，
  所以画风规则里明确写了「无人物题材同样适用」。

**B. 输出硬拼接（不靠 LLM 自觉）**

- `apply_persona(..., scope)` 新增画风锚定层：Danbooru 风格前置
  `anime style, anime, 2d, illustration, cel shading, drawn`（只画真人则前置写真实标签）；
  `natural` / `natural_en` 不插标签串，改在句首点明「一幅精美的二次元动漫插画，」/
  "An exquisite anime-style illustration, "，已写过画风则不重复。
- negative 一律追加反向画风排除词（`photorealistic, photo, 3d render, live action…` /
  `anime, cartoon, illustration, 2d…`），写实线完全对称处理。
- 句首画风前缀同时承担自然语言风格的质量兜底（精美 / exquisite / high-quality）。

**C. 风格池按可画类型收窄**

- `style_pool_for(category, scope)`：只画动漫一律 `STYLES_ANIME`、只画真人一律
  `STYLES_PHOTO`、`both` 才按类别原有的收窄规则。动漫工作流不会再抽到「胶片摄影颗粒感」。

**D. 人物锁全局化**

- 人物排除负面词改为**所有类别都加**（此前非人物类整段跳过，正是景物图冒出老人/男人的原因）；
  正面人物标签仍只给 `PERSON_CATEGORIES`，景物类不塞 `1girl` 以免毁风景。
- 人物锚定与画风锚定是两个独立开关，互不影响：关掉人物锚定仍会拼画风标签，反之亦然。

**E. 可回退 + 可观测**

- 新增 `prompt.style_anchor_enabled`（bool，默认 `true`），关掉即退回 v0.7.5 行为
  （可画类型只筛题材、不改提示词）；「提示词」页有开关与完整说明。
- `debug_llm` 返回 `scope`，调试试跑页显示「可画类型」标签，并注明下面展示的是
  **最终注入 ComfyUI 的文本**（含硬拼接），「LLM 原始返回」才是模型自己写的。
- 模板行「生效：…」新增错配提示：可画类型=只画动漫却用自然语言/写实底模时，
  提示换动漫底模——画风锚定不会换底模，写实底模变不出二次元。

### 测试

- `tests/test_prompter.py` 增补约 45 条：scope 归一化、简报带可画类型、三套系统提示词的
  画风规则（含开关 / 完全覆盖 / 关闭）、风格池与 200 轮抽签不出现反向风格、
  Danbooru 硬标签与幂等、自然语言句首前缀与去重、两套锚定互不干扰、入参不被修改。
  旧断言 `persona_skips_scenery` 拆成「景物类仍不加 1girl」+「排除负面词现在全类别生效」两条。
- `tests/test_schema.py` / `tests/test_config_coerce.py` 补 `prompt.style_anchor_enabled` 守卫；
  README 更新「七·六」「八」两节、能力表、配置项速查与排障表。

## v0.7.5（可画类型：跟随萌绘也能逐条设，术语不再和「风格」打架）

现象：「怎么对单个工作流配置可绘制的风格：真人还是动漫，或者两者皆可」。

根因有两层：

1. **本插件库其实早就有这个配置**（v0.7.3 的 `subject_scope`），但它叫「题材能力」，
   而同一行里还有「标签风格」「提示词适配」两个带「风格」字样的字段 —— 用户按「风格」去找
   根本对不上号；而且 hint 没讲清它管的是「派什么题材」，容易被误以为是画风开关。
2. **跟随萌绘来源确实做不到逐条配**：工作流来自萌绘插件、本插件没有逐条清单，
   只有一个整体的 `anima.subject_scope`，代码注释里也写着「没法逐条标记能力」。

### 修复

- 新增 `anima.workflow_scopes`（逐条可画类型，格式「工作流名或id=auto|anime|photo|both」，
  逗号或换行分隔）。纯函数 `workflow_mgr.parse_scope_overrides()` 解析、
  `workflow_scope_for()` 按「名字 → workflow_id」查逐条覆盖，
  **都没命中才回落到整体值** `anima.subject_scope`（默认 `auto`，再交给 `resolve_scope` 按底模推断）。
  这是用户选定的回落策略，升级前后行为完全平滑。
- 「工作流模板」页「轮换顺序」列表每行加一个下拉：跟随整体设置 / 自动判断 / 只画动漫 /
  只画真人 / 两者皆可，旁边显示后端算出的「生效」标签；选「跟随整体设置」即从名单里删掉该条。
  下拉结果写进 `anima.workflow_scopes`，点「保存」生效。
- 全站术语统一：「题材能力」→**「可画类型」**，`anime/photo/both` 的文案改成
  「只画动漫 / 只画真人 / 两者皆可」；主题词池页的「能力标签 / 类别能力覆盖」同步改名。
  **配置字段名 `subject_scope` / `scope` 与内部枚举一律不改**，避免升级时数据迁移。
- 把边界写进 UI hint 与 README：可画类型管的是**派什么题材**，不是提示词语言
  （那是「提示词适配」），也不会给底模加能力 —— 给动漫底模标「只画真人」只会让它被派去画写真，
  出来的仍是二次元脸。附「怎么给单条工作流设」的两种来源入口对照表。

### 测试

- `tests/test_workflow_mgr.py` 增补 15 条：解析（含中文冒号 / 非法值丢弃 / dict 输入）、
  按名字与 id 命中、回落整体值、空/坏回落归 auto、与 `resolve_scope` 串联。
- `tests/test_schema.py` / `tests/test_config_coerce.py` 加 `anima.workflow_scopes` 与前端点号键守卫；
  README 更新「七·六」章节、配置项速查与排障表。

## v0.7.4（轮换顺序看得懂也排得动：游标改记「上一张是谁」）

现象：「按照工作流顺序生图，顺序是什么，怎么排序，现在的排序看不懂」。

根因（追完整条链路后确认有六处）：

1. `workflow_mgr.pick_template` 用 `cursor % len(pool)` 索引 `enabled_pool`，而 `pool` 的顺序其实只是
   **配置清单的物理行顺序**（跟随萌绘时是萌绘 `_workflows()` 的返回顺序），并没有「排序」这一步；
2. 游标是**绝对计数**（meta 表 `rr_cursor`），`pool` 又会剔除未启用 / 校验失败的模板，
   停用任意一条就让后面的顺序整体错位一位；
3. `select_workflows(cfgs, names)` 拿名单当集合做 `name in want` 判断、遍历的是 `cfgs`，
   所以跟随萌绘时**按什么顺序勾都不影响轮换**；
4. WebUI 没有序号、也没有「当前轮到哪 / 下一张是谁」，`rr_cursor` 只躺在数据库里，接口没暴露；
5. 手动「立即画一张」与指令出图走同一个 `generate_one`，游标在生成**之前**就 +1 落库，
   失败轮也占位，看起来像「跳过了一个」；
6. 切到「随机」期间游标停滞，切回轮换从旧值接着走；下拉只写「按顺序轮换（重启续轮）」，
   没说顺序依据是清单行顺序。

### 修复

**A. 顺序可见**

- 新增纯函数 `workflow_mgr.rotation_state()`：返回有序可用名单（含 #N / 稳定 key / 能力）、
  `next_index` / `next_name` / `last_found`。
- `/workflows` 附带 `rotation` 字段；「工作流模板」页第一张卡改为「轮换顺序」：
  `#1 A → #2 B → #3 C ↻` 链、高亮下一张、无可用模板提示、从 #1 重置；
  模板清单每行显示「轮换 #N」。

**B. 顺序可排**

- local：模板清单每行加 ↑↓，直接改 `workflow_templates` 的行顺序（保存后下一张起生效）；
- anima：`select_workflows` 改为**按名单顺序**查表返回，`anima.workflow` 从集合升级为有序名单；
  控制台新增「轮换顺序」有序列表（↑↓ / 移出）与「固定当前顺序以便排序」按钮
  （把「空名单 = 全部可用」固化成显式名单后才能排）。

**C. 游标不再跳位**

- `Template` 新增稳定 `key`（local 用文件名、anima 用萌绘的 `workflow_id` —— 多条工作流可能
  共用同一个 `base_id`，不能用它）；`to_public()` 一并带出。
- `pick_template` 改为记「上一张的 key」，用 `next_index()` 在**当前可用池**里找它的下一条；
  找不到（首次 / 被删 / 被停用）就从 #1 可预期地重来。库里新键 `rr_last`，
  旧整数 `rr_cursor` 只在升级后第一轮作过渡，随后自动切换。
- `random` 分支不再推进游标，切回轮换时从上次轮换选中的那条继续。

**D. 手动出图占位开关**

- 新增 `rotation_manual_advances`（bool，默认 `false`）：「立即画一张」/ `/绘卷 画一张`
  默认不推进轮换；判定抽成 `should_advance_rotation(reason, mode, has_tpl)`，
  兼容 `reason="manual"`（指令）与 `"manual:webui"` 两种写法。
- 游标写入从「选中时」挪到「出图成功后」，**失败轮一律不占位**（下一轮仍是同一条，
  连续失败在图库里一眼可见）。
- 新增 `/workflows/rotation/reset`，UI 上「从 #1 重置」。

### 测试

- `tests/test_rotation.py`（新）：用 AST 从 `main.py` 原样抠出 5 个方法再 exec，
  覆盖手动占位开关、新旧游标过渡、轮换视图、重置、停用中间一条不跳位；
  本机没有 AstrBot 运行时也能测磁盘上的真代码。
- `tests/test_workflow_mgr.py`：`pick_template` 断言改按名字游标，新增
  `template_key` / `next_index` / `rotation_state` / 名单有序与去重 / 加载后自动带 key 等 25 条。
- `tests/test_schema.py` / `tests/test_config_coerce.py` 补 `rotation_manual_advances`
  与前端点号键守卫；README 新增「七·七、轮换顺序」章节与排障条目。

## v0.7.3（题材能力 + 人物锚定 + 词池可读：六个反馈一次收口）

一轮使用反馈里的六个问题，归结为三处结构性缺口：

- 「能不能自定义系统提示词」→ 不能。`prompter.build_system_prompt()` 三套提示词全硬编码，
  `main._llm_prompt` 直接调用，配置里没有任何入口。
- 「有的工作流能画真人和动漫、有的只能动漫，怎么区分」→ **完全没区分**。
  `generate_one` 先 `pick_template()` 再独立 `draw_brief()`，抽签不知道当次用的是哪条工作流；
  已有的 `prompt_profile` / `tag_style` 只管「提示词是标签串还是自然语言」，不是题材能力。
  结果动漫底模会被派去画「真人少女·胶片写真」，写实底模会被派去画「动漫少女·赛璐璐」。
- 「一般是亚洲少女，不是其他人种和老女人」→ 没有人物约束，人种与年龄全交给底模先验。
- 「词池是什么，感觉复杂」→ 停用类别 / 类别权重 / 词池扩充三块其实是同一批类别的三个视角，
  还都要求手写 `键=词`；内置词池在前端只是一排标签，看不到里面到底有什么。
- 「加写真、泳装、私房、摄影场景」→ `STYLES_PHOTO` 只有 5 个词、真人少女只有 10 个题材。
- 「加动漫 / 游戏角色」→ 没有角色线，也没有能自己删改的角色名单。

### 修复

**A. 系统提示词可定制 + 人物锚定（新增 `prompt` 配置分组 / 控制台「提示词」页）**

- `prompter.build_system_prompt(tag_style, opts)` 把三套内置提示词拆成「主体 + 附加规则 + 输出契约」：
  `prompt.extra_rules` 追加自定义规则；`prompt.override_*` 三个框可**完全替换**对应风格；
  替换文本若没写 JSON 输出契约会自动补上，避免用户改完提示词整轮判失败。
- 人物锚定默认开启：`prompt.persona_text` 写进三种系统提示词（东亚面孔的年轻成年女性，
  排除其他人种 / 中老年 / 男性 / 未成年）；`apply_persona()` 在 Danbooru 风格下固定前置
  `persona_tags`、追加 `persona_negative`（中文自然语言风格改用 `persona_negative_zh`），自动去重且幂等。
- 硬约束**只对少女 / 写真 / 角色类生效**：风景、星空、生态是纯景物，硬塞 `1girl` 会毁画面；
  人物肖像类里有老人与男性，也不套少女标签。涉及泳装与室内写真的题材一律写成
  成年、生活化、自然光的日常表达，不写任何露骨或性暗示内容。
- 新增 `/prompt/preview`，控制台可看三种风格下 LLM 真正收到的系统提示词。

**B. 工作流题材能力（问题 2，也是 5 / 6 生效的前提）**

- 模板清单与上传新增 `subject_scope = auto|anime|photo|both`；跟随萌绘时用
  `anima.subject_scope` 整体指定（萌绘工作流无法逐条标记）。
- `workflow_mgr.resolve_scope()`：`auto` 按底模家族推断（动漫系 → 动漫、写实系 → 真人、
  Flux / Qwen / SD3 → 通吃、认不出 → 不限制）；`Template.scope` 随模板加载算出。
- `prompter.draw_brief(scope=..., scopes=...)` 在加权抽签**之前**先按能力筛类别：
  动漫工作流不抽写真 / 真人 / 肖像，真人工作流不抽二次元角色 / 少女，双方都能抽「通吃」类
  （风景、星空、艺术、生态）。筛完为空会退回原池，绝不抽不出题材。
- 内置 `CATEGORY_SCOPE` 映射 + `theme.category_scopes`（`类别=anime|photo|both`）供自定义类别
  覆盖；`/theme/pool` 把能力回显到词池页。

**C. 词池扩充 + 界面简化（问题 4 / 5 / 6）**

- 新增 3 类：`写真摄影`（居家自然光、泳池清爽泳装、海边泳装剪影、旅拍、影棚柔光…）、
  `动漫角色` / `游戏角色`（各 12 条「通用原型 + 场景」）；摄影风格池 5 → 10 个词、
  动漫风格池 8 → 12 个词；默认权重同步纳入新类别（少女题材仍占大头）。
- `theme.character_names` 预置一份**可删改**的知名角色名单（每行 `类别=名字,名字`），
  启动时并入对应类别题材池；删掉整行即停用，内置通用原型仍在。老配置没有该键时
  用 `prompter.DEFAULT_CHARACTER_NAMES` 兜底，升级后不必手动补配置。
- 「主题词池」页重做成**一张题材清单**：每行含启用开关、权重、能力标签、题材数，
  展开可看内置题材（`/theme/pool` 真实数据）并直接追加词 / 新增自定义类别；三块保存合并成一次。
  角色名单独立卡片，风格 / 光色 / 氛围 / 构图与能力覆盖收进「高级」。
- 「工作流模板」页每行与上传表单加题材能力下拉，`effHint` 显示生效能力；
  控制台新增第 10 个页面「提示词」。

### 测试

- `tests/test_prompter.py` 增补约 60 条：能力映射与覆盖、动漫 / 真人 / 通吃三种工作流的
  300 轮抽签不越界、空池回退、系统提示词追加 / 覆盖 / 契约兜底、人物锚定（幂等、纯景物不生效、
  关闭、中文负面词）、角色名单与 `theme_pool`。
- `tests/test_workflow_mgr.py` 增补 `scope_for_family` / `resolve_scope` / `load_templates`
  写入 `scope` 与 `to_public` 暴露；`tests/test_schema.py` 加新键与「schema 默认值 == 代码兜底常量」
  防漂移断言；`tests/test_config_coerce.py` 加 `prompt.*` 点号键与模板 `subject_scope` 保存校验。
- 女孩题材统计口径把新加的「写真摄影」计入（`test_prompter.weight_girl_dominant`），
  避免新增类别稀释权重后被误判为回归。

## v0.7.2（间隔以「上一张的时间」为基准：改配置不再从保存时刻重算）

现象：改完调度设置后，下次生成不是「上一张 + 间隔」，而是从**配置生效的那一刻**
重新起算；间隔一旦调短，也照样再等一整个新周期，等于白白跳过一轮。

根因：`reschedule()` 里写的是

```python
self.sched.next_due = scheduler.advance(cfg, datetime.now(), _PICK_RANDOM)
```

基准是 `datetime.now()`（保存时刻）。同理，循环里跑完一张后也是
`advance(cfg, datetime.now())` —— 等于「出图结束时刻 + 间隔」，
周期被出图耗时额外撑长（画 5 分钟就变成 6 小时 5 分一轮）。

### 修复

- 新增纯函数 `scheduler.due_from_last(cfg, last_at, now, rng)`，统一口径：
  **下一次 = 上一张的开始时刻 + 间隔**；
  - 没有历史（首次安装）→ now + 间隔；
  - 算出来已经过期（间隔改短 / 停机很久）→ 立刻（now + 3 秒），不再推后一整个周期；
  - `daily` 模式例外：它是「墙上的时刻」，过了就等下一个时刻，**不补画**。
- `ScheduleState` 新增 `last_started_at`（上一张开始时刻）；调度循环在开画前记录，
  结束后用 `due_from_last(cfg, last_started_at, now)` 重排。
- `reschedule()` 改为以上一张为基准（`last_started_at` → `last_run_ts` → 无历史则 now），
  日志与事件里写明「以上一张 HH:MM 为基准 / 无历史，从当前时间起算」，便于核对。
- **重启也接得上**：新增 `Store.last_generation_ts()`，插件启动时从库里取最近一条记录的
  开始时间，恢复 `last_started_at`，不再出现「重启后从启动时刻重新等一整个间隔」。
- 控制台调度页文案同步：「保存后立即按『上一张的时间 + 新间隔』重算（已过期则马上画）」，
  保存成功提示也改为这句话。

### 测试

- `tests/test_scheduler.py` 增补 7 条：无历史、正常续算、已过期立即、jitter 只加不减、
  daily 取下一个时刻不补画、daily 无历史、间隔调短后立刻补上。
- `tests/test_store.py` 增补 1 条：`last_generation_ts` 能取到最近记录时间。

## v0.7.1（修「下次生成 15:40」卡着不动：出图期间显示正在生成 + 30 分钟硬超时）

现象：15:43 时页面仍显示「下次生成 15:40」，过了几分钟一动不动，也没有任何「正在画」的提示。

根因：调度循环的 `next_due` **要等整轮生成结束才推进**

```python
summary = await plugin.generate_one(reason="schedule")   # ← 出图期间卡在这里
...
state.next_due = advance(cfg, datetime.now(), rng)       # ← 才更新
```

而一轮生成包含 LLM、ComfyUI 排队、等待预算（`draw_timeout + 队列 × 60s`，封顶
`hard_cap`，默认 600s）与下载，几分钟很正常。这段时间里：

- `next_due` 还停在过去的 15:40；
- `last_run_note` 也只在结束后才写；
- `/status` 里没有任何「正在生成」字段。

于是 UI 只能把过期时间反复展示，看起来就是卡死。v0.7.0 加的 15 秒自动刷新反而让它更明显
（一直在刷同一个过期值）。

### 修复

- `ScheduleState` 新增 `running_since` / `running_note` 与 `running_for_sec()`；
  `generate_one` 在 `finally` 里清理（取消/异常都不会留下假状态），
  因此**定时、指令、WebUI 手动触发都算**。
- 调度循环到点后先把 `last_run_note` 写成「正在生成（15:40 开始）」，再执行生成。
- `/status` 新增 `running` / `running_since` / `running_for_sec` / `running_note` /
  `next_due_overdue_sec`，`last_run_note` 在生成中自动换成「正在生成（… 开始，已 N 分钟）」。
- 控制台三处（总览卷首卡、调度页「现在」卡、左侧栏）在生成中改为显示
  「正在生成 · 已 3 分 12 秒」，并提示「画完会自动排下一轮」；
  顶部状态标签在生成中显示「生成中…」。
- 若 `next_due` 已过期超过 60 秒却**没有**在生成 → 明确警示
  「计划已过期 X，调度可能卡住（看『调试与日志』）」，不再默默展示过期时间。
- 兜底：单轮生成加 30 分钟总硬超时（`GEN_TIMEOUT_SECONDS = 1800`），
  超时按失败收尾（写「生成超时：超过 30 分钟未完成，已放弃本轮」），
  下一轮照常排，避免任何环节被彻底拖死。

### 测试

- `tests/test_scheduler.py` 增补 3 条：`running_since` 默认空、`running_for_sec()` 计算、
  硬超时常量存在且 ≥600s。

## v0.7.0（实时刷新 + 失败理由不再空白 + 图库全屏看大图）

三条体验问题（用户反馈）：日志/图库/任务要手动点刷新；有些失败记录的错误理由是空的；
图库看不了大图。

### 一、自动刷新（不用再点「刷新」）

- 新增 `src/polling.ts` 的 `useAutoRefresh(fn, intervalMs)`：定时跑、**页面切到后台自动跳过**、
  正在跑时不叠加、失败静默（不弹 toast，网络抖动不该刷屏）。
- 图库：每 8 秒探测最新记录 id，**只有第一页 + 无筛选 + 未打开详情/大图**时才静默重载，
  不打断浏览；筛选栏右侧有开关与「自动刷新 · 最近更新时间」。
- 调试与日志：事件日志每 3 秒刷新（内存日志，请求很轻），同样带开关。
- 总览：状态/统计从 30s 缩短到 10s，并在有 running 记录时顺带刷新「最近出图」。
- 调度计划：下次生成时间、今日张数每 15 秒刷新（倒计时不再过期）。
- 所有页面都遵守「不可见不请求」，标签页切回来时会自动补一次。

### 二、失败理由不再空白

- 新增 `errfmt.py`（纯函数，带单测）：`format_exc()` 保证异常文本永远是
  「类型: 说明」，说明为空时给「未知错误（异常没有附带任何信息）」；`truncate()` 统一截断。
  `main.py` / `delivery.py` 里所有 `f"{type(e).__name__}: {e}"` 都换成它。
- **卡在 running 的旧记录**（插件重启/进程退出留下）是「状态=失败、理由为空」的主要来源：
  * 启动时 `Store.fail_stale_running()`（>15 分钟）把它们收尾为失败，理由写
    「生成被中断（插件重启或进程退出），本轮没有结果」；
  * `Store.update_generation()` 在 `status=failed` 且没给 error 时自动补
    「未知错误（未记录原因）」。
- **ComfyUI 400 不再只显示「Bad Request」**：`queue_prompt` 改为读响应体，用
  `summarize_error_body()` 抽出 `error.message/details` 与逐节点的
  `node_errors`（如 `节点 9（UNETLoader）: Value not in list，unet_name: ... 不存在`）。
- `task_error()` 补全：`exception_message` 为空时写「ComfyUI 未返回异常信息」，
  并附节点号/类型；`status_str=error` 但没有任何 message 时给
  「ComfyUI 报告执行失败，但没有返回具体原因（先看 ComfyUI 控制台日志）」。
- 前端兜底：图库卡片/详情、「LLM 试跑」在 error 为空时显示
  「（无错误详情：可能是旧版本记录或进程中断）」而不是留白。

### 三、图库全屏看大图

- 新增 `components/ImageViewer.vue`：铺满窗口的看图层，**滚轮缩放（1~5x）、放大后拖动、
  双击/「复位」还原、←/→ 切上一张下一张、Esc 关闭**，顶部显示标题/工作流/尺寸/序号，
  手机端自适应。
- 入口：缩略图左下角「⤢」按钮，或详情抽屉里点大图（图片右下角有「点击看大图」提示）。
- 看图时按 2400px 长边请求（原抽屉 1600px），足够看清细节。

### 测试

- 新增 `tests/test_errfmt.py`（11 条：空 message / None / 空白字符串 / 截断等）。
- 新增 `tests/test_comfy_errors.py`（15 条：ComfyUI 校验响应体、HTML 错误页、空响应、
  execution_error 各种缺字段情况）。跑法需 `--with aiohttp`。
- `tests/test_store.py` 增补 4 条：失败无理由兜底、陈旧 running 收尾、新鲜 running 保留。

## v0.6.0（题材倾向可调：少女双线 + 类别权重，默认动漫多、真人少）

需求：希望多画美少女，动漫和真人都要，动漫多一点、真人少一点 —— 但不要变成一个模子，
也别把其它题材全掐掉。

### 新增两个类别（原来只有一个「美少女」）

| 类别 | 定位 | 题材示例 |
| --- | --- | --- |
| 美少女 | 二次元少女日常（保留原池） | 窗边读书的银发少女、雨巷和服、天台夕阳 |
| **动漫少女**（新） | 更插画/风格化的二次元少女，12 条题材 | 樱花树下的回眸、祭典浴衣仰望烟花、星空草地、海边堤坝 |
| **真人少女**（新） | 写真向，10 条题材 | 清晨窗边自然光素颜、咖啡馆侧脸、街头抓拍、雨窗人像 |

### 风格池随类别收窄（避免错配）

- 新增 `STYLES_ANIME` / `STYLES_PHOTO` 与 `CATEGORY_STYLE_GROUP`：
  美少女/动漫少女只从动漫风格池抽（赛璐璐、厚涂、新海诚、吉卜力、浮世绘、皮克斯 3D…），
  真人少女/人物肖像只从写真池抽（胶片颗粒、自然光人像、电影感浅景深、柔焦逆光、窗边柔光）。
- 其它类别与用户自定义类别仍用全局 `STYLES`；`theme.extra_words` 里「风格」追加的词对所有池生效。

### 类别权重（新配置 `theme.category_weights`）

- 纯函数 `prompter.parse_weights()` / `weighted_choice()` / `category_distribution()`：
  支持「类别=权重」文本（逗号/换行/中文逗号）、字典；非法丢弃、负值归零、上限 100；
  没写到的类别按 1；**全为 0 时回落到均匀**（绝不抽不出）；停用类别优先排除。
- 默认值 `美少女=5, 动漫少女=4, 真人少女=3, 人物肖像=1, 风景场景=1, 宇宙星空=1, 艺术创意=1, 自然生态=1`
  → 少女题材 ≈ 70%，其中动漫 : 真人 = 3 : 1，其余各类各约 6%。
- 控制台「主题词池」页新增顶部「类别权重」卡片：文本框 + 「少女向 / 均衡」预设按钮 +
  实时分布条（含「少女题材合计 xx%」），改完点保存下一张生效。
- 停用类别（`theme.disabled_categories`）优先级高于权重，两者不冲突。

### 测试

- `tests/test_prompter.py` 增补 22 条：新类别存在与题材数量、权重解析（多分隔符/空/垃圾/负数/字典/上限）、
  默认权重抽样 800 次（少女 > 60%、动漫 > 真人×2、其它类别仍出现）、全 0 回落、0 权重不出现、
  停用优先、动漫/真人风格池只出对应风格、自定义类别回落全局池、分布百分比计算。

## v0.5.2（修：调度页「生成间隔」的单位下拉一打开就自动关闭）

现象：调度计划页选「生成间隔」时，点右边的单位下拉，菜单刚展开就自己收起，
根本选不到「分钟 / 天」；同一行左侧的数字框也会莫名抢走焦点。

根因（HTML label 的激活行为）：那一行用了

```html
<label class="mp-field">
  <span>生成间隔</span>
  <n-input-number/> <n-select/>   <!-- 两个可交互控件 -->
</label>
```

`<label>` 会把它收到的点击**转发给它关联的第一个表单控件** —— 这里关联到的是左边的
`n-input-number` 内部 `<input>`。点下拉触发器时，浏览器把点击转给那个数字输入框 →
它获得焦点 → Naive UI 的 select 失去焦点 → 下拉立即关闭。
「随机浮动」那一行同理（开关 + 数字框）：点数字框还会顺手把开关切一下。

### 修复

- 一行里有多个控件时改用 `<div class="mp-field">`（视觉完全一致，`.mp-field` 是类选择器）；
  只有单个控件时才保留 `<label>`（那时转发目标就是它自己，无副作用）。
- 全量扫了一遍 `webui-src/src` 下所有 `<label>`：其余位置的 label 里都只有一个控件，
  不存在同类问题；只有「生成间隔」「随机浮动」两处改掉。
- 加了行内注释说明这个坑，避免以后又把多控件塞回 label。

### 验证

- 无头 Chrome 实测：注入脚本模拟点击单位下拉 → 菜单保持展开（修复前会立刻关闭）；
- 控制台其余页面路由冒烟无回归。

## v0.5.1（尺寸真正跟着「这一张画」走，不再只按三个关键词分宽窄）

问题：尺寸本来是「构图抽签决定朝向 → 白名单里挑一档」，方向确实每张会变，但档位选择很粗：
只有「超广角/俯瞰/全景」三个词会取最宽，其余构图（中景、特写、半身像、全身像、仰视）
一律组内纯随机 —— 竖构图里最瘦高的 768×1344 可能被分配给「特写」，也可能抽给「全身像」，
本质上没有按画面内容挑比例。

### 改动

- `prompter` 新增 `_COMPOSITION_RATIO` 与 `composition_ratio()`：每个构图对应一个目标长宽比
  （超广角 1.78 / 全景 1.65 / 俯瞰 1.55 / 中景 1.40 / 全身像 1.78 / 仰视 1.50 /
  半身像 1.33 / 特写 1.33 / 对称与留白 1.00）。
- `pick_size()` 改为「朝向分组 → 按长宽比匹配」：取最接近目标的档，允许 `0.10` 容差内的相邻档
  一起随机（既贴合构图，又不至于同名构图永远同一个像素值）；认不出构图（自定义词池）仍组内随机。
- 结果举例（sdxl 预设）：超广角 → 1344×768；全身像 → 768×1344；特写 → 896×1152（偶尔 832×1216）；
  中景 → 1216×832 或 1152×896；方形构图 → 1024×1024。
- 保持不变的边界：`follow`（跟随萌绘基础工作流）仍严格用 latent 的分辨率 —— 横竖互换、方图取短边；
  想看尺寸随构图变化请用 `sdxl`/`flux`/`qwen` 预设。
- 测试增补 13 条：构图比例映射（含未知/空）、超广角与全身像的确定档、特写落 3:4、
  未知构图仍在白名单内、不同构图能得到 ≥3 种尺寸组合。

## v0.5.0（提示词按底模自动适配：三种风格 + 尺寸 + 质量词）

问题：提示词规则此前**只跟一个模板字段 `tag_style` 走**，而这个字段要靠人手工判断 ——
换了底模（比如从 Anima 换成 Flux）不提醒、不跟随，出图就会出现「给 Flux 喂一堆 danbooru 标签」
「用 Qwen-Image 却吃英文标签」「给写实底模加 masterpiece, best quality」这类明显偏差；
另外系统提示里写着「质量词由系统自动添加」，代码其实从没加过。

### 底模画像（自动识别）

- 新增 `workflow_mgr.guess_profile(model_file)`：按工作流里识别出的**底模文件名**归类
  （`family_of` → anime / flux / qwen / sd3 / photoreal / unknown），给出该家族的：
  提示词风格、尺寸预设、质量前缀。
- 识别表（关键词 → 家族 → 生效值）：

  | 家族 | 关键词（小写包含） | 提示词 | 尺寸 | 质量词 |
  | --- | --- | --- | --- | --- |
  | anime | anima / illustrious / noob / pony / animagine / anything / counterfeit / meina / hassaku … | danbooru 英文标签 | 保持模板 | 自动加 `masterpiece, best quality, very aesthetic, absurdres` |
  | flux | flux / schnell / kontext | natural_en 英文自然语言 | flux | 不加 |
  | qwen | qwen | natural_zh 中文自然语言 | qwen | 不加 |
  | sd3 | sd3 / sd_3 | natural_en | sdxl | 不加 |
  | photoreal | realvis / juggernaut / epicrealis / realistic / chillout / majicmix … | natural_en | sdxl | 不加 |
  | unknown | 其它 | **保持模板手填值**（不瞎猜） | 保持 | 不加 |

- 每个模板新增 `prompt_profile`（`auto` / `danbooru` / `natural` / `natural_en`，默认 `auto`）：
  `auto` 时才用画像覆盖；显式指定则强制。识别不出家族时一律回退手填值。
- 「跟随萌绘」时优先级：**萌绘底模库的判定**（`_load_from_base` 注水的 `is_anima`）→ 底模文件名 → 模板兜底；
  尺寸仍以 `follow`（基础工作流 latent 宽高）优先。
- `Template` 新增 `profile` / `quality_prefix` / `prompt_profile`，`to_public()` 一并返回；
  模板清单行会显示「生效：底模画像 Flux · 提示词 natural_en · 尺寸 flux」，与被覆盖的填写值一眼对上。

### 三种提示词风格

- `danbooru`：动漫标签串（原有）。
- `natural`（= `natural_zh`）：中文自然语言，适合 Qwen-Image 这类中文模型（原有语义保留，旧值不改）。
- **新增 `natural_en`**：英文自然语言 2~4 句，适合 Flux / SD3 / 写实 SDXL；`caption` 仍要求中文。
- 质量词真正落地：danbooru 风格出图前自动补质量前缀（已含 `masterpiece`/`best quality` 则不重复），
  自然语言风格不加 —— 与系统提示里「质量词由系统自动添加」的承诺一致。

### 尺寸

- `SIZE_PRESETS` 新增 `qwen`（1664×928 / 928×1664 / 1328×1328，16 对齐），`size_preset` 选项同步。
- `auto` 识别到 Flux/Qwen/SD3/写实时自动切到对应尺寸档（Flux 1344×768、Qwen 1664×928、写实 1216×832），
  避免「Flux 工作流套 1216×832/SDXL 档」这类隐性不匹配；识别不到不动用户的设置。

### 调试可见性

- 「调试与日志 → LLM 试跑」现在展示的是**最终注入值**（含质量词），并显示该模板生效的 `tag_style`；
  模板行与试跑结果能互相印证。

### 测试

- `tests/test_workflow_mgr.py` 增补 18 条：六个家族的画像判定、空文件名、`load_templates` 的
  auto 覆盖（Flux 工作流 → natural_en/flux、质量词为空且带提示）、强制 `danbooru` 时不覆盖。
- `tests/test_prompter.py` 增补 15 条：qwen 尺寸档（含 16 对齐）、三种系统提示（中文/英文/标签）、
  质量词拼接与去重、qwen 三朝向落值。

## v0.4.1（跟随萌绘可多工作流轮换，不再只能选一条）

问题：v0.4.0 的「跟随萌绘」只装配了一条工作流（`anima.workflow` 单值），
把原本的「多工作流随机 / 轮换」设计挡住了 —— 本地库能轮换，跟随萌绘却退化成单模板。

### 修复与增强

- `anima.workflow` 语义升级为**多值**（名称或 id，逗号/分号/换行分隔，去重）：
  - 留空 = 萌绘里**所有可用的新版工作流**都参与（不再是「第一条」）；
  - 填了 = 只有命中的参与，按 `workflow_selection`（random / round_robin）挑。
- `main.py` 的 `_anima_template()` 拆成 `_anima_templates()` + `_build_anima_template()`，
  逐条经萌绘的 `_load_from_base` + `_apply_base_overrides` 组装（覆盖参数仍然各自生效），
  单条失败只影响自己那条，不影响整池。
- 新增纯函数 `workflow_mgr.split_names()`（多值解析、去重）与
  `workflow_mgr.select_workflows()`（按名称/ID 挑，自动跳过萌绘里已停用或未关联基础工作流的），
  逻辑从 `main.py` 抽出以便单测。
- 失败提示按原因细分：找不到指定名字 / 萌绘里全是旧版 / 全是停用，
  出图失败记录里也能看到具体是哪一种。
- 控制台「工作流模板」页：来源=萌绘时改为**多选下拉** + 每行「加入轮换 / 移出轮换」+
  「参与中」标记 + 「恢复全部参与」按钮；顶部实时显示「已选 N 条 / 实际参与 N 条 / 可用 N 条」。
  「模板轮换」下拉移到两种来源共用，跟随萌绘时也能看到并调整。
- `GET /anima/workflows` 增加 `picked`（当前参与名单）与 `usable`（符合条件的总数）。

### 测试

- `tests/test_workflow_mgr.py` 增补 13 条：`split_names` 空值/混合分隔/去重/数组输入；
  `select_workflows` 全部/按名/多选/按 id/跳过停用/跳过旧版/未知名字；
  以及「萌绘多模板 round_robin 依次轮换」。
- 页面冒烟：来源=萌绘、已选两条的场景下，多选控件、参与标记、按钮文案均正确渲染。

## v0.4.0（工作流：WebUI 上传解析 + 可直接用萌绘的工作流）

需求（用户反馈）：
1. 工作流只能自己往服务器目录丢文件，**WebUI 里不能上传和解析**，太原始；
2. 既然联动了萌绘，**就应该能用萌绘里配好的工作流**，而不是各维护一套。

### 一、顺手修掉的严重 bug：嵌套配置项根本存不进去

- 现象：v0.3.0 之后每个配置页点「保存」都只弹一句「未知配置项: schedule.quiet_enabled」，
  嵌套键（`comfyui.*` / `llm.*` / `schedule.*` / `anima.*` / `theme.*` / `delivery.*` / `gallery.*`）**一个都写不进去**。
- 根因：`h_set_config` 用 `schema.get(key)` 只查顶层键，而前端提交的是扁平点号键。
  冒烟测试的桩桥把 POST 直接回 `{status:"ok"}`，所以 UI 冒烟测不出来。
- 修复：把配置读写的纯逻辑抽到新模块 `config_coerce.py`（`resolve_spec` / `cast_value` /
  `coerce_template_list` / `build_writes` / `apply_writes`），点号键按「父键 + 子键」解析，
  并新增 `tests/test_config_coerce.py`（22 条断言，含「前端用到的 25 个点号键都必须能解析」）。
  这类"保存静默失败"只能靠单测守住。
- 新模块已同步进 `build_zip.ps1` 的 `$includeList` 与 `main.py` 的热重载列表。

### 二、工作流可以在 WebUI 上传 + 解析入库

- 新增 `POST /workflows/parse`（只解析不入库）与 `POST /workflows/upload`（解析 → 落盘 → 更新清单 → 重载）。
- `workflow_mgr` 新增：
  - `parse_upload(text)`：识别 ComfyUI **API 格式**，遇到界面格式（含 `nodes`/`links`）直接给
    「请在 ComfyUI 里用导出 (API)」的人话提示；兼容 `/history` 那种 `{prompt: {...}}` 包一层；
  - `analyze(wf)`：在 `locate_nodes` 之上补出**底模文件名 / 保存节点 / latent 类名与默认宽高 / 文生图·图生图判定 / 软警告**；
    图生图链路（latent 无 width/height）**拒绝入库**，不会等到出图时才炸；
  - `save_workflow_file()`（文件名清洗 + 同名覆盖）、`upsert_entry()`（同名覆盖/追加）、`follow_sizes()`（按 latent 默认宽高派生横竖方三档）。
- 「工作流模板」页改造：拖拽/选择 `.json` 或直接粘贴 JSON → **解析预览**（正向/负向/latent/保存节点、底模、默认尺寸、seed 数、问题与警告）→ 确认入库；
  清单行下方常驻一行解析摘要，失败原因就地显示。

### 三、可以直接用萌绘的工作流（follow 模式）

- 新增 `workflow_source`（`local` / `anima`）与 `anima.workflow`（选萌绘哪条工作流）、`anima.size_preset`（默认 `follow`）。
- 实现方式：拿到萌绘插件实例后调用它的 `_load_from_base` + `_apply_base_overrides` ——
  于是**萌绘里配的采样器/步数/CFG/放大三态/清理注入/保存格式覆盖都会生效**，与萌绘自己出图一致，
  不需要在本插件复刻那套规则。两个方法是半公开的，调用失败只降级为「只用基础工作流底图」并记警告，不会把插件带崩。
- 尺寸策略 `follow`：直接用基础工作流 latent 的默认宽高（横/竖自动互换、方图取短边），
  不再强行套 sdxl/flux 白名单 —— 换了底模或分辨率时不会再被正方形挤变形。
- 新增 `GET /anima/workflows`：列出萌绘的出图工作流、其关联的基础工作流名/底模/解析状态，
  旧版（无 `base_id`）或基础工作流解析失败的在 UI 里标「不可用」并给出原因；支持一键「用这条」。
- 「关联萌绘」页新增「工作流」小节，显示当前来源并可跳转设置。
- 跟随模式下出图失败的记录会写明真实原因（例如「跟随萌绘模式：读取萌绘工作流失败：…」），不再是含糊的「没有可用模板」。

### 工程

- 新增 `tests/test_config_coerce.py`；`tests/test_workflow_mgr.py` 增补 20 条（上传解析/analyze/跟随尺寸/入库文件名/清单覆盖）；
  `tests/test_schema.py` 覆盖新增的三个键。
- 九条页面路由 + 「工作流来源=萌绘」模式经桩桥冒烟通过（本地/萌绘两种模式各一轮）。

## v0.3.0（控制台重构：左右结构九页 + 调度配置讲故事 + 关联萌绘/调试）

现象与诉求（用户实测反馈，逐条对应）：

1. README 停在「PRD 阶段，未动工」；
2. 打开图库页就报错 —— `webui_api.py:149 TypeError: _qi() missing 2 required positional arguments: 'lo' and 'hi'`；
3. 调度配置项「看不懂」：模式、间隔、抖动、时刻表、安静时段全平铺在一起；停用安静时段得清空时间；
4. 没有页面路由，全部内容塞在两个 Tab 里；
5. 界面没主题、不像「绘卷」；
6. 找不到「关联萌绘」的配置项，也没有任何调试入口。

### 修复

- **`/records` 崩溃**：`_qi(name, default, lo, hi)` 是四参签名，调用处漏了 `lo/hi` 且后面还跟着一段多余的 `int()` 转换。改为 `_qi("starred", -1, -1, 1)`（`-1` 不筛 / `0` 只看未收藏 / `1` 只看收藏，与 `gallery_store.list_records` 的 `starred in (0,1)` 口径一致）。这类错误纯函数测试测不到（`webui_api` 依赖 astrbot 运行时而 `tests/` 不加载它），属于「装得上、点开就炸」的接口层疏漏。

### 新功能：控制台重做（左右结构 + 主题化）

- **页面路由**：引入 `vue-router`（hash 模式 —— AstrBot 按真实文件路径提供静态资源，history 模式刷新会 404），左侧导航 + 右侧内容，共九页：总览 / 图库 / 调度计划 / 投递 / 生成服务 / 工作流模板 / 主题词池 / 关联萌绘 / 调试与日志。移动端（≤900px）导航收进抽屉。
- **视觉主题**：宣纸底 + 墨色文字 + 卷轴渐变（墨蓝→紫→樱粉），卡片浮起、圆角统一、颜色全部走 CSS 变量，暗色只换变量不写两套布局；`version.ts` 改由 `build_webui.ps1` 从 `metadata.yaml` 注入（新增文件已进 `.gitignore`），右上角版本号不再手写。
- **总览页**：下次生成倒计时、今日/累计/成功率/可用工作流四张指标卡、最近出图、最近事件、工作流健康 + 快捷「立即画一张 / 暂停恢复」。
- **图库页**：筛选放进悬浮卡片，缩略图统一 4:3 裁切 + 悬停放大与「查看详情」遮罩，详情抽屉保留全元数据/收藏/重投/删除。

### 新功能：调度配置重讲一遍

- **模式二选一**改成两块大卡片（隔一段时间 / 每天固定时刻），只显示当前模式相关字段；间隔改为「数字 + 单位（分钟/小时/天）」，页面实时显示「约等于每 6 小时画一张」。
- **安静时段加开关**：新增 `schedule.quiet_enabled`（默认 `true`，保持老行为）。关掉开关后时间设置**原样保留但一律不生效**，不再需要靠清空时间来停用 —— 这正是用户要求的「加开关而不是清空」。调度循环改走纯函数 `scheduler.quiet_span_of(cfg)`，开关判断与时间解析集中在一处。
- **随机浮动 / 每日上限**同样给了开关：关掉即准点 / 不限张数，分钟数与张数原样保留，重新打开不用重填。
- **保存即重算**：`POST /config` 检测到 `schedule.*` 变更后调用 `plugin.reschedule()` 按新配置立即重排下次计划点，不再等旧时刻自然到期；页脚常驻一句话计划摘要（下次时间 / 今日已画 / 当前计划）。

### 新功能：关联萌绘（astrbot_plugin_comfyui_anima）

- 只读萌绘插件 `comfyui_servers`（`StarMetadata.config`）拿服务器地址，**不写萌绘配置、不调它的绘图接口** —— 萌绘是 2 万行单体，内部契约随时会变，共用「同一台 ComfyUI 服务器」这个事实最稳。
- 新增配置 `anima.follow_server` / `anima.server_key`：开启后每次出图现读萌绘当前启用的那台地址（萌绘换地址自动跟上），萌绘未安装/未配置时回落本插件 `comfyui.url`。
- 新增接口 `GET /anima`（是否安装、版本、服务器列表、当前生效地址与来源）、`POST /anima/sync`（`follow` / `copy` / `off` 三动作）。「复制」把地址抄一份到本插件后各配各的，适合不想让两边联动的用户。
- 控制台新增「关联萌绘」页：服务器列表只读展示 + 一键「用这台」/「跟随这台」/「复制地址」/「关闭跟随」+ 连接测试，并写明联动边界（共享队列靠避让阈值协调，工作流与图库各自独立）。

### 新功能：调试与日志

- 后端新增调试接口：`GET /debug/env`（目录、生效地址与来源、投递名单、模板状态、避让阈值）、`POST /debug/comfy`（可临时测任意地址，返回延迟与队列）、`POST /debug/llm`（真跑一次「抽签 → LLM → 尺寸落值」，**不提交 ComfyUI、不写库**，返回简报 / 原始返回 / 耗时）、`POST /debug/delivery`（发一条文本验证 UMO）、`GET /debug/logs`（内存环形日志，最新在前）。
- `main.py` 增加 400 条环形事件日志（`debug_log`，出图开始/成功/失败、LLM 试跑、投递自检、配置保存、萌绘联动都会留痕），重启插件即清空，不占磁盘。
- 控制台「调试与日志」页把这些串起来：环境快照、ComfyUI 连通性与队列、LLM 试跑结果（简报 / 尺寸 / prompt / negative / caption / 原始返回）、投递自检、事件日志（可按类型过滤）。

### 工程

- 新增 `tests/test_schema.py`：复刻 AstrBot 装载期的 `_parse_schema` 规则（每个节点必须有合法 `type`、`object` 必须有 `items`、`default` 必须落在 `options` 内），并校验关键键仍在 —— `_conf_schema.json` 少一个 `type` 会让整插件装不上，而只测业务代码永远发现不了。
- `tests/test_scheduler.py` 新增 `quiet_span_of` 6 条断言（默认开 / `false` / 字符串 `"false"` / 字符串 `"true"` / 无时间 / 空配置）。
- README 重写为可用文档（安装 / 指令 / 九个页面 / 配置速查 / 数据目录 / 排障 / 开发打包）。
- 九条 hash 路由经「静态服务 + 桩桥 + 无头 Chrome」冒烟：每页均渲染出预期内容，控制台无业务报错。

## v0.2.0

新功能：WebUI 新增「设置」页（图库 / 设置双 Tab），全部插件配置可在面板内查看与修改，不再需要去 AstrBot 的插件配置页。

- **配置分区卡片**（model_panel 的分区风格 + user_gateway 的 schema 回显模式）：调度 / ComfyUI 服务器 / 提示词模型（LLM）/ 投递 / 主题词池 / 图库与管理，每卡独立「保存」——**只提交本卡字段**（扁平点号键），绝不整份回传，防「一次坏保存清空配置」（box v0.12.7 教训）。
- **后端 GET/POST /config**：GET 把嵌套配置展平成点号键并附原始 schema；POST 按 `_conf_schema.json` 白名单**先整体校验再统一写入**（类型转换 / options 校验 / 未知键直接报错而非静默跳过），`workflow_templates` 走整清单替换且拒绝空清单（防一键清空），保存后自动重载模板并回填校验状态。
- **提示词模型选择器**：`GET /providers` 枚举 AstrBot 已加载的对话提供商（`Provider.meta()` 取 id + 绑定模型，标出 AstrBot 默认项），下拉选择（model_panel 约定：一提供商 = 一模型，按 id 选）；名单外的 id 可手输（tag 模式）。
- **工作流模板编辑器**：清单可视化编辑（名称 / 文件名 / 标签风格 / 尺寸预设 / 默认负面词 / 启用 / 五个节点覆盖项），逐行显示 `/workflows` 的校验结果（校验通过 / 失败原因），增删改保存即生效。
- **验证**：桩桥扩展 /config /providers /workflows，无头 Chrome 冒烟：7 张设置卡渲染、模板校验标签、调度卡保存 toast（7 项）全过，控制台无业务报错；后端四套纯函数测试全绿。

## v0.1.3

新特性：远程队列避让（与萌绘共用 ComfyUI 时，定时出图先给交互出图让路）。

- **背景**：绘卷与萌绘提交的是**同一台 ComfyUI 服务器的同一个队列**。绘卷定时出图若插在交互出图后面，既拖长自己的等待预算，也把用户手动让萌绘画的图顶到后面。此前只有「提交后」用远程 /queue（或中转站 X-Queue-Position 头）算等待预算，没有「提交前」的避让。
- **变更**：定时到点后、提交前先查远程 `/queue`（排队 + 执行中，萌绘同口径），前方 ≥ `comfyui.queue_defer_threshold`（默认 2，0 = 关闭）个任务时，本轮**顺延 5 分钟再试**并在「最近一轮」留痕；连续避让不消耗每日上限、不计入连续失败。查询失败按不忙处理（宁试不误停）。手动「/绘卷 画一张」与 WebUI 触发不受避让限制（用户明确要求，照画——它的等待预算仍按队列现状放宽）。
- **与萌绘对齐的既有行为**（本次核实，非新改动）：客户端改编自萌绘 `comfyui_client.py`，同一套 `/prompt` 提交、`/history` 真实时间轮询（轮询网络抖动只重试不放弃等待）、`/view` 取图；等待预算同公式 `draw_timeout + 队列前方 × queue_extra_timeout`，封顶 `hard_cap`。差异点：萌绘的预算会按「本次已耗时 + 预留发消息时间」收紧，那是给交互回复留时间的口径；定时后台没有回复时限，不照搬。
- **验证**：test_scheduler 新增 `queue_too_busy` 7 条断言（达阈值/超阈/未达/关闭/查询失败/非法值/None），四套测试全绿。

## v0.1.2

新特性：定时间隔支持分钟粒度（每 X 分钟一张）。

- **背景**：原 interval 模式只有「间隔小时」一个整数字段，想要「每 30 分钟一张」只能填 0 撞内部的 0.25h 下限，既不直观也不够细。
- **变更**：`schedule.interval_hours`（小时）改为 `schedule.interval_minutes`（分钟，最低 1，默认 360 = 每 6 小时，默认节奏不变）；`jitter_minutes` 语义不变（间隔上随机加 0~N 分钟）。旧配置档里已存的 `interval_hours` 仍被识别（×60 换算），升级不会丢设置。
- **提醒**：间隔调小后记得同步调大「每日上限」——30 分钟间隔 + 上限 4 时每天仍只出 4 张。单张通常要画几十秒，而循环是「跑完一张再从当下起算下轮」，间隔低于单张耗时不会堆积并发，自然退化为连画。
- **验证**：test_scheduler 更新（分钟粒度基础值 / 最低 1 分钟钳制 / None 回落默认 / 抖动区间 / 旧键 ×60 换算），四套测试全绿。

## v0.1.1

修复：每日上限 `max_per_day` 只出现在配置与展示，调度循环没有执行。

- **现象**：配置里写 `max_per_day: 4`（WebUI 状态条与 `/绘卷` 状态也如实显示「今日 X / 上限 4」），但定时到点照样出图——上限形同虚设。
- **根因**：`run_loop` 在「安静时段跳过 → 占用顺延」之后直接调 `generate_one`，达限判定漏掉了。schema / 状态文本 / WebUI 接口三处都消费了这个键，唯独真正管流程的循环里没接线——「声明了但没执行」这类问题静态检查发现不了（键存在、类型正确、展示正常），是测试覆盖循环逻辑的成本太高才漏的。
- **修复**：新增纯函数 `cap_reached(cap, success_today)`：`cap <= 0` = 不限，非法值当不限，只数当天 `status='success'` 的张数（失败轮不占名额）。调度到点后先查 `count_day_success(今天)`，达限则顺延到下个计划点并留痕（`last_run_note` + INFO 日志）；跨零点后当天计数自然归零，自动恢复生成。今日张数查询异常时本次不执行上限（宁多画不误停）。手动 `/绘卷 画一张` 与 WebUI「立即画一张」不受限（与配置 hint 声明一致）。
- **验证**：`tests/test_scheduler.py` 新增 `cap_reached` 6 条断言（达限/未达/0 不限/None/非法值/字符串数字）；四套测试全绿；重新打包。

## v0.1.0

首个可用版本：定时用 LLM 生成高质量提示词自动绘图，成功后投递白名单会话，全部生成记录进图库 WebUI。

### 新功能

- **定时调度**：`interval`（每 N 小时 ± 抖动分钟）与 `daily`（每日时刻表）两种模式，统一带安静时段（默认 01:00–08:00，支持跨零点）、每日上限与连续失败自动暂停（3 次后暂停并可选通知管理员，成功复位）。调度循环按「目标时刻每轮现场重算 + 分片睡 ≤30 分钟 + 异常 300s 自愈」实现（规避星语 v1.11.3 踩过的「先算延迟再睡、永不到点」坑）。
- **LLM 提示词工程**（本插件核心）：插件先分层抽签（类别 → 题材 → 风格 → 构图 → 光色 → 氛围，内置 6 大类词池 + 配置扩充 + 类别停用）生成「创作简报」，LLM 只负责把简报写成高质量提示词——防「画来画去都是美少女半身像」的模式塌缩。LLM 按 JSON 契约输出（prompt / negative / caption），解析失败带原因重试 1 次；动漫模板走英文 Danbooru 标签、写实模板走中文自然语言（随模板 `tag_style`），质量前缀由系统自动补，不依赖 LLM。
- **尺寸白名单**：LLM 不报数字宽高，只按构图朝向（横/竖/方，由构图层决定）从工作流模板的尺寸白名单落值（16 对齐，sdxl / flux / sd15 三档预设；超广角/俯瞰/全景构图自动取长宽比最极端的一档）。
- **ComfyUI 直连**：HTTP 客户端改编自萌绘 `comfyui_client.py`（AGPL 同源，头注署名）：`POST /prompt` 提交、`/history/{id}` 真实时间轮询（超时临界点兜底再查一次）、`/view` 取图；等待预算 = 基础超时 + 队列前方数 × 每任务加时，封顶硬上限（排队数优先中转站 `X-Queue-Position` 头，回退 `/queue` 统计）。
- **工作流模板**：API 格式 JSON 放 `data/plugin_data/<名>/workflows/`，配置清单里写元信息（名称 / 标签风格 / 尺寸预设 / 默认负面词 / 启用）。节点定位自动进行（KSampler 的 positive/negative/latent_image 引用 + 全部 seed/noise_seed 节点），异形链路（CFGGuider / SamplerCustomAdvanced / 非 text 输入框）靠配置覆盖兜底，定位失败标红不参与轮换；`random` / `round_robin` 两种选择方式（游标落库，重启续轮）。内置 `anima_t2i.json` 起步模板（Anima Turbo 文生图，首启自动复制到数据目录）。
- **生成记录与图库 WebUI**：每轮生成全量落库（含失败行：简报 / 提示词 / 错误原因都留档，图库可查「死因」）；图片按 sha256 内容寻址归档（重复内容不重占空间），LRU 按容量淘汰且收藏永不淘汰。图库页（插件 Page，Vue3 + Naive UI）：调度状态卡（下次生成时间 / 暂停恢复 / 立即画一张）、筛选（工作流 / 类别 / 状态 / 关键词 / 只看收藏）、网格缩略图（服务端 PIL 缩图 + data URL，规避沙箱 iframe 直链不可靠）、详情抽屉（全元数据 / 复制提示词 seed / 收藏 / 重投白名单 / 两段确认删除）、统计（成功率口径：running 不进分母）。
- **投递**：`StarTools.send_message` 直发（不走事件管线，天然静默，不触发其他插件回复）；显式 opt-in 名单（空 = 只入库不投递），`/绘卷 投递 开启|关闭` 管理；投递结果按 UMO 合并进记录，WebUI 可对失败会话重投；文案模板可配（`{caption} {category} {subject} {workflow}`）。
- **指令**：`/绘卷`（状态）、`/绘卷 暂停|继续`、`/绘卷 画一张 [主题词]`（立即生成并直发当前会话）、`/绘卷 投递 开启|关闭`、`/绘卷 帮助`。

### 设计取舍

- 不联动萌绘的 `comfyui_draw` 通道：该工具签名要求 `event`，定时场景没有事件、伪造脆弱；直连 ComfyUI 只依赖其稳定的 HTTP API。萌绘 `external=True` 通道留作将来手动触发场景的备选。
- 数据全落 `data/plugin_data/`（安装目录更新即清零）；`main.py` 第一天就带 `importlib.reload` 热重载列表（依赖序）。
- 指令回执直发（`event.send`），绕开核心「回复时 @ 发送者」装饰（萌萌表情包踩过的坑）。

### 验证

- 纯函数自检 4 套全过（`tests/`：抽签与契约解析 / 节点定位与注入 / 调度判定 / 记录库与 LRU），`uv run --no-project [--with aiosqlite] python tests/test_xxx.py` 直接跑。
- 图库页经「本地服务 + sandbox iframe 包装页 + 桩桥」真浏览器冒烟：渲染、筛选、详情抽屉、触发 toast 全过，控制台无业务报错。
