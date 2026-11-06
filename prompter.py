"""LLM 提示词工程：多样性抽签 → 创作简报 → 严格 JSON 契约。

核心思路（PRD §4）：直接让 LLM「画张高质量图」必然塌缩成「一个美少女半身像」，
所以插件先分层抽签给出**具体的创作简报**，LLM 只负责把简报写成高质量提示词。
尺寸不让 LLM 报数字：朝向由构图层决定，插件按工作流模板的尺寸白名单落具体宽高
（16 对齐），从根上杜绝「LLM 报奇怪分辨率」。

本模块除 draw/parse 依赖 random 外全部是纯函数，tests/ 可独立跑。
"""

import json
import random
import re

# --------------------------------------------------------------------------- #
# 内置词池（配置 theme_extra 可追加；disabled_categories 可停用整类）
# --------------------------------------------------------------------------- #

CATEGORIES: dict[str, list[str]] = {
    "风景场景": [
        "雨后小巷的霓虹倒影", "晨雾中的山谷梯田", "黄昏海边的孤独灯塔",
        "雪后初晴的森林木屋", "深夜便利店的暖光窗台", "雨天车站的告别月台",
        "沙漠尽头的孤独邮筒", "春日樱花夹岸的河道", "古镇石桥上的清晨薄雾",
        "高山垭口的经幡与云海", "湖心孤舟与晨光", "老城钟楼下的石板广场",
    ],
    "宇宙星空": [
        "银河拱桥下的湖面", "深空星云与尘埃带", "极光下的雪原",
        "流星划过破败神殿", "月球环形山看到的地球升起", "气态巨行星的光环近景",
        "空间站舷窗外的日出", "彗星掠过夜空海面", "双星系统下的轨道废墟",
        "星际尘埃中的一点孤灯",
    ],
    "美少女": [
        "窗边读书的银发少女", "撑伞走过雨巷的和服少女", "天台眺望夕阳的校服少女",
        "花田里追蝴蝶的元气少女", "咖啡馆里写字的围巾少女", "雪地里回头的红衣少女",
        "灯下修理发条的机械少女", "泳池边晒太阳的少女", "图书馆书架间找书的少女",
        "夜色摩天轮上的双马尾少女",
    ],
    "人物肖像": [
        "老画师沾满颜料的手与画笔", "爵士酒吧里的萨克斯手", "图书管理员与高耸书架",
        "舞台幕后候场的舞者", "老船长望着雾中的港口", "街头棋摊的两位老人",
        "修表匠的特写工作台", "深夜电台的主播",
    ],
    "艺术创意": [
        "悬浮在云端的倒悬城市", "纸鹤组成的巨浪", "巨大鲸鱼游过城市上空",
        "打字机里长出藤蔓与花", "镜面迷宫里的无限倒影", "齿轮驱动的空中花园",
        "墨滴入水晕染成山水", "书页折叠成的森林",
    ],
    "自然生态": [
        "浅滩觅食的火烈鸟群", "雪枝上的红雀", "深海发光水母群",
        "竹林间回望的赤狐", "雨后叶背的树蛙", "花丛中悬停的蜂鸟",
        "苔藓与蘑菇的微距世界", "极地冰面上的企鹅家族",
    ],
}

# 风格层：纯词表
STYLES: list[str] = [
    "赛璐璐动画风", "厚涂插画", "水彩淡彩", "新海诚式光影", "吉卜力式温暖",
    "胶片摄影颗粒感", "极简扁平插画", "浮世绘色块", "暗调油画", "蒸汽波霓虹",
    "水墨晕染", "皮克斯式 3D 质感", "动态漫画分镜感",
]

# 构图层：每项带朝向（orientation 是尺寸白名单的键，LLM 不参与决定）
COMPOSITIONS: list[tuple[str, str]] = [
    ("超广角远景·横幅", "landscape"),
    ("全景·横幅", "landscape"),
    ("中景·横幅", "landscape"),
    ("俯瞰视角·横幅", "landscape"),
    ("特写·竖幅", "portrait"),
    ("半身像·竖幅", "portrait"),
    ("全身像·竖幅", "portrait"),
    ("仰视视角·竖幅", "portrait"),
    ("对称构图·方形", "square"),
    ("留白极简·方形", "square"),
]

LIGHTS: list[str] = [
    "黄昏暖光", "月光冷调", "清晨薄雾光", "霓虹夜色", "逆光剪影",
    "丁达尔光束", "极光绿紫", "星云紫蓝", "烛火暖光", "阴天柔光",
]

MOODS: list[str] = [
    "宁静", "史诗感", "孤独感", "明快", "神秘", "温柔", "荒凉", "奇幻",
]

# --------------------------------------------------------------------------- #
# 尺寸白名单（按模型家族预设；16 对齐）
# --------------------------------------------------------------------------- #

SIZE_PRESETS: dict[str, dict[str, list[list[int]]]] = {
    "sdxl": {
        "landscape": [[1216, 832], [1152, 896], [1344, 768]],
        "portrait": [[832, 1216], [896, 1152], [768, 1344]],
        "square": [[1024, 1024]],
    },
    "flux": {
        "landscape": [[1344, 768], [1216, 832]],
        "portrait": [[768, 1344], [832, 1216]],
        "square": [[1024, 1024]],
    },
    "sd15": {
        "landscape": [[768, 512], [912, 512]],
        "portrait": [[512, 768], [512, 912]],
        "square": [[512, 512]],
    },
    # Qwen-Image 原生分辨率（1328² / 1664×928），16 对齐
    "qwen": {
        "landscape": [[1664, 928], [1328, 928]],
        "portrait": [[928, 1664], [928, 1328]],
        "square": [[1328, 1328]],
    },
}

QUALITY_PREFIX_DANBOORU = "masterpiece, best quality, very aesthetic, absurdres"


def apply_quality_prefix(prompt: str, prefix: str) -> str:
    """把质量词拼到提示词最前；已含质量词则不重复（模型自己写了就尊重它）。"""
    text = str(prompt or "").strip()
    pre = str(prefix or "").strip()
    if not text or not pre:
        return text
    low = text.lower()
    if "masterpiece" in low or "best quality" in low:
        return text
    return f"{pre}, {text}"

# --------------------------------------------------------------------------- #
# 抽签
# --------------------------------------------------------------------------- #

_LAYER_WORDS = {
    "风格": None,  # None = 用模块默认池
    "光色": None,
    "氛围": None,
}


def _merge_words(default: list, extra: str | None) -> list:
    extra_items = [w.strip() for w in str(extra or "").replace("\n", ",").split(",")]
    return default + [w for w in extra_items if w]


def draw_brief(
    rng: random.Random,
    disabled_categories: set[str] | None = None,
    extra: dict | None = None,
) -> dict:
    """分层抽签生成创作简报。

    extra（配置 theme_extra）的键：
    - 内置类别名（如「宇宙星空」）→ 值为逗号分隔的追加题材；
    - 其他键 → 新类别名，值为逗号分隔的题材；
    - 「风格」「光色」「氛围」→ 追加对应层的词；
    - 「构图」→ 追加构图项，格式「名称@landscape|portrait|square」。
    """
    extra = extra or {}
    disabled = disabled_categories or set()

    # 类别池：内置 + extra 新类别，跳过停用
    cat_pool: dict[str, list[str]] = {}
    for name, words in CATEGORIES.items():
        if name not in disabled:
            cat_pool[name] = list(words)
    for key, val in extra.items():
        items = [w.strip() for w in str(val or "").replace("\n", ",").split(",") if w.strip()]
        if not items:
            continue
        if key in ("风格", "光色", "氛围", "构图"):
            continue
        if key in cat_pool:
            cat_pool[key] = cat_pool[key] + items
        else:
            cat_pool[key] = items
    if not cat_pool:
        cat_pool = dict(CATEGORIES)

    category = rng.choice(sorted(cat_pool.keys()))
    subject = rng.choice(cat_pool[category])
    style = rng.choice(_merge_words(STYLES, extra.get("风格")))
    light = rng.choice(_merge_words(LIGHTS, extra.get("光色")))
    mood = rng.choice(_merge_words(MOODS, extra.get("氛围")))

    comp_pool = list(COMPOSITIONS)
    for item in _merge_words([], extra.get("构图")):
        # 格式：名称@orientation
        name, _, ori = item.partition("@")
        if name and ori in ("landscape", "portrait", "square"):
            comp_pool.append((name, ori))
    comp_name, orientation = rng.choice(comp_pool)

    return {
        "category": category,
        "subject": subject,
        "style": style,
        "composition": comp_name,
        "orientation": orientation,
        "light": light,
        "mood": mood,
    }


def brief_text(brief: dict, theme: str = "") -> str:
    """把简报排成给 LLM 的一行简报文本。theme 非空时覆盖题材（指令「画一张 主题词」）。"""
    subject = theme.strip() or brief.get("subject", "")
    return (
        f"类别={brief.get('category', '')}；题材={subject}；风格={brief.get('style', '')}；"
        f"构图={brief.get('composition', '')}；光色={brief.get('light', '')}；"
        f"氛围={brief.get('mood', '')}"
    )


# --------------------------------------------------------------------------- #
# 系统提示词（按模板 tag_style 二选一）
# --------------------------------------------------------------------------- #

_SYSTEM_COMMON_TAIL = """
【输出契约】只输出一个 JSON 对象，禁止任何解释、前后缀或代码围栏：
{"prompt": "...", "negative": "...", "caption": "..."}
- caption：发到群里的一句话，中文 20~40 字，有画面感，不要罗列标签。
- negative 不确定就给空串。JSON 以外的任何字符都算错误。
"""


def build_system_prompt(tag_style: str) -> str:
    """按提示词风格给系统提示：danbooru=英文标签，natural/natural_zh=中文自然语言，
    natural_en=英文自然语言（Flux / SD3 / 写实类底模）。"""
    if tag_style in ("natural", "natural_zh"):
        return (
            "你是一位顶级 AI 绘画提示词工程师，为支持中文的模型（如 Qwen-Image）写文生图提示词。\n"
            "用户会给你一份「创作简报」，请把它写成高质量的中文画面描述。\n"
            "【prompt 要求】2~4 句连贯中文：主体与细节 → 环境与背景 → 镜头与构图 → "
            "光线与氛围。严格贴合简报的题材/风格/构图/光色/氛围，不要偏题，"
            "不要写成标签堆砌，总长 80~200 字。\n"
            "【negative 要求】中文，只写常见缺陷（如：模糊，畸形，多余的手指，"
            "低画质，水印），不确定就空串。\n" + _SYSTEM_COMMON_TAIL
        )
    if tag_style == "natural_en":
        return (
            "You are a top-tier prompt engineer writing text-to-image prompts for "
            "natural-language models (Flux / SD3 / photorealistic SDXL checkpoints).\n"
            "The user gives you a Chinese creative brief. Turn it into a high-quality "
            "English prompt.\n"
            "【prompt】2-4 flowing English sentences: subject and details -> environment "
            "and background -> camera and composition -> lighting and mood. Follow the "
            "brief's subject/style/composition/light/mood exactly; do not drift into a "
            "generic portrait; no tag dumps, no artist names, 60-150 words.\n"
            "【negative】English, short, only common defects (blurry, deformed, extra "
            "fingers, low quality, watermark); empty string if unsure.\n"
            "【caption】Remains Chinese, 20-40 characters, evocative, no tag lists.\n"
            "【output】A single JSON object, no prose, no code fence:\n"
            '{"prompt": "...", "negative": "...", "caption": "..."}\n'
            "Any character outside that JSON object is a failure.\n"
        )
    return (
        "你是一位顶级 AI 绘画提示词工程师，为动漫风格模型（Danbooru 标签系）写文生图提示词。\n"
        "用户会给你一份「创作简报」，请把它扩展成一条高质量英文 Danbooru 标签串。\n"
        "【prompt 要求】\n"
        "1. 小写英文标签，逗号分隔，不要写成句子；30~55 个标签。\n"
        "2. 从主到次：内容型主标签（1girl/1boy/scenery/no humans 等按内容定）→ "
        "主体外观细节 → 服装 → 动作姿势 → 场景背景 → 构图视角 → 光线氛围。\n"
        "3. 严格贴合简报的题材/风格/构图/光色/氛围，不要偏题，不要偷换成「站在街边的普通少女」。\n"
        "4. 质量词（masterpiece/best quality 等）由系统自动添加，你不要写。\n"
        "5. 不写 artist 名。\n"
        "【negative 要求】英文小写标签串（如 lowres, bad anatomy, bad hands, "
        "watermark, jpeg artifacts），不要过长。\n"
        "【示例】简报=类别=宇宙星空；题材=银河拱桥下的湖面；风格=新海诚式光影；"
        "构图=超广角远景·横幅；光色=星云紫蓝；氛围=宁静\n"
        '输出：{"prompt": "scenery, night, lake, milky way, starry sky, reflection, '
        'lakeshore, mountain silhouette, gradient sky, deep blue and purple, glowing '
        'horizon, thin clouds, no humans, wide shot, cinematic composition, '
        'beautiful detailed sky", "negative": "lowres, bad anatomy, watermark, '
        'jpeg artifacts", "caption": "银河在湖面落下一条光的路，山影安静得能听见星星。"}\n'
        + _SYSTEM_COMMON_TAIL
    )


def build_user_prompt(brief: dict, theme: str = "") -> str:
    return "创作简报：" + brief_text(brief, theme)


# --------------------------------------------------------------------------- #
# JSON 契约解析
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_llm_payload(text: str) -> dict:
    """解析 LLM 输出为 {"prompt","negative","caption"}；不合法抛 ValueError（中文原因）。"""
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("LLM 返回为空")
    m = _FENCE_RE.search(raw)
    if m:
        raw = m.group(1).strip()
    if not raw.startswith("{"):
        # 宽容：截取第一个 { 到最后一个 }
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM 输出里找不到 JSON 对象")
        raw = raw[start : end + 1]
    try:
        data = json.loads(raw)
    except Exception as e:
        raise ValueError(f"JSON 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("LLM 输出的 JSON 不是对象")
    prompt = str(data.get("prompt") or "").strip().strip('"')
    if not prompt:
        raise ValueError("prompt 为空")
    if len(prompt) > 4000:
        prompt = prompt[:4000]
    negative = str(data.get("negative") or "").strip().strip('"')
    caption = str(data.get("caption") or "").strip()
    return {"prompt": prompt, "negative": negative, "caption": caption}


# --------------------------------------------------------------------------- #
# 尺寸落值
# --------------------------------------------------------------------------- #

# 构图 → 目标长宽比（长边/短边，恒 ≥1）。朝向（横/竖/方）由白名单分组决定，
# 这里只决定「同一朝向里该挑多宽/多瘦」：超广角要最横，全身像要最瘦高，特写偏 3:4。
_COMPOSITION_RATIO: tuple = (
    ("超广角", 1.78),
    ("全景", 1.65),
    ("俯瞰", 1.55),
    ("中景", 1.40),
    ("全身像", 1.78),
    ("半身像", 1.33),
    ("特写", 1.33),
    ("仰视", 1.50),
    ("留白", 1.00),
    ("对称", 1.00),
)

# 允许「差不到这么多」的候选一起参与随机：既贴合构图，又不至于每张一模一样
_RATIO_TOLERANCE = 0.10


def composition_ratio(composition_name: str) -> "float | None":
    """构图名 → 目标长宽比；认不出返回 None（调用方保持随机）。"""
    name = str(composition_name or "")
    for key, ratio in _COMPOSITION_RATIO:
        if key in name:
            return float(ratio)
    return None


def _ratio_of(wh) -> float:
    w, h = max(1, int(wh[0])), max(1, int(wh[1]))
    return max(w, h) / min(w, h)


def pick_size(
    sizes: dict, orientation: str, composition_name: str, rng: random.Random
) -> tuple[int, int]:
    """按「朝向 + 构图」从模板白名单落宽高 —— 每张图各不相同。

    1. 朝向（横/竖/方）决定取哪一组候选，朝向由当次抽签的构图决定；
    2. 组内按构图名匹配目标长宽比（超广角/全景/俯瞰偏横，全身像偏瘦高，
       特写/半身像偏 3:4），选出最接近的若干档随机取一个；
    3. 构图认不出（自定义词池）→ 组内纯随机。
    """
    cands = (sizes or {}).get(orientation) or (sizes or {}).get("square") or [[1024, 1024]]
    cands = [[int(c[0]), int(c[1])] for c in cands if len(c) >= 2]
    if not cands:
        return 1024, 1024
    if len(cands) == 1:
        return cands[0][0], cands[0][1]
    target = composition_ratio(composition_name)
    if target is None:
        pick = rng.choice(cands)
    else:
        scored = sorted(cands, key=lambda wh: abs(_ratio_of(wh) - target))
        best = abs(_ratio_of(scored[0]) - target)
        pool = [wh for wh in scored if abs(_ratio_of(wh) - target) <= best + _RATIO_TOLERANCE]
        pick = rng.choice(pool)
    return int(pick[0]), int(pick[1])
