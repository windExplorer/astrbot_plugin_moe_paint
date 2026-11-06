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

from . import daylight

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
    # v0.6.0：把「少女」拆成二次元与真人两条线，权重可分别调（动漫多一点、真人少一点）
    "动漫少女": [
        "樱花树下回眸的少女与飘落花瓣", "雨夜霓虹街角撑着透明伞的少女",
        "夏日祭典穿浴衣仰望烟花的少女", "车站月台上抱着纸袋等车的少女",
        "教室窗边被午后阳光照亮的少女", "天台边缘张开双臂迎风的少女",
        "冬日暖炉边织围巾的少女", "星空下躺在草地上的少女",
        "图书馆梯子上取书的少女", "海边堤坝上被风吹起裙摆的少女",
        "秋日银杏道上骑单车的少女", "清晨厨房里系围裙煎蛋的少女",
    ],
    "真人少女": [
        "清晨窗边自然光的素颜写真", "咖啡馆里低头看手机的侧脸特写",
        "街头抓拍的走路瞬间", "海边黄昏的逆光侧影",
        "图书馆书架间的安静半身像", "雨天玻璃窗后的少女",
        "居家白衬衫的慵懒午后", "天台栏杆边的城市背景人像",
        "秋日公园长椅上的毛衣少女",         "夜晚便利店门口的霓虹光人像",
    ],
    # v0.7.3：写真/角色三条线。题材一律写成成年、生活化、自然光的日常表达；
    # 泳装与室内写真只给「清爽 / 剪影 / 晨光」这类中性描述，不写任何露骨内容。
    "写真摄影": [
        "居家白衬衫的自然光写真", "窗边逆光的安静侧脸", "胶片质感的街头抓拍",
        "夏日泳池边的清爽泳装写真", "海边黄昏的泳装剪影", "旅拍小镇的午后街角",
        "影棚柔光的极简人像", "咖啡馆靠窗的阅读侧影", "雨天玻璃窗后的柔和人像",
        "卧室晨光里的慵懒居家写真", "公园树影下的连衣裙人像", "日系清新的天台随拍",
    ],
    # 角色线：内置的是「通用原型 + 场景」，具体角色名由 theme.character_names 注入，
    # 让用户能自由删改，也避免把一长串 IP 名单写死在代码里。
    "动漫角色": [
        "魔法少女在夜色屋顶抬手", "神社巫女在樱花树下回身", "学院偶像在舞台聚光灯下",
        "女仆在复古洋馆端着红茶", "剑士在晨雾山道拔剑", "异世界冒险者在营火旁摊开地图",
        "机甲驾驶员在驾驶舱回望", "和服少女在祭典灯火间", "图书委员在闭馆前的书架间",
        "乐队主唱在排练室调弦", "白衣研究员在温室里记笔记", "猫耳少女在雨后巷口撑伞",
    ],
    "游戏角色": [
        "弓手在雪山垭口拉弓", "法师在星夜塔顶点亮法阵", "骑士在古城台阶上持盾",
        "街头赛车手在霓虹隧道回眸", "咖啡店店员在打烊后擦拭杯子", "潜水员在沉船舷窗边",
        "机械师在机库里检修装甲", "舞者在空中回廊旋身", "占卜师在烛光下摊牌",
        "忍者在竹林月下跃起", "旅行者在驿站摊开地图", "治疗师在晨光病房里整理药箱",
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

# 按类别收窄风格池：动漫题材别抽到「胶片颗粒感」，真人写真别抽到「赛璐璐动画风」。
# 不在表里的类别（含用户自定义类别）用全局 STYLES。
STYLES_ANIME: list[str] = [
    "赛璐璐动画风", "厚涂插画", "水彩淡彩", "新海诚式光影", "吉卜力式温暖",
    "浮世绘色块", "皮克斯式 3D 质感", "动态漫画分镜感",
    "角色立绘感", "游戏 CG 质感", "轻小说插画风", "动画关键帧质感",
]
STYLES_PHOTO: list[str] = [
    "胶片摄影颗粒感", "自然光人像摄影", "电影感浅景深", "柔焦逆光写真",
    "窗边柔光写真", "日系清新写真", "杂志人像大片", "黑白人像摄影",
    "旅拍纪实感", "室内自然光写真",
]

CATEGORY_STYLE_GROUP: dict[str, str] = {
    "美少女": "anime",
    "动漫少女": "anime",
    "动漫角色": "anime",
    "游戏角色": "anime",
    "真人少女": "photo",
    "写真摄影": "photo",
    "人物肖像": "photo",
}

# 可画类型：类别需要什么类型的底模。未列出的类别一律 both。
# anime = 只能由动漫系底模画（角色、二次元少女）；photo = 只能由写实底模画（写真、肖像）。
CATEGORY_SCOPE: dict[str, str] = {
    "美少女": "anime",
    "动漫少女": "anime",
    "动漫角色": "anime",
    "游戏角色": "anime",
    "真人少女": "photo",
    "写真摄影": "photo",
    "人物肖像": "photo",
}

# 人物锚定的硬约束只对「明确要画年轻女性」的类别生效：
# 风景/星空/生态是纯景物，硬塞 1girl 会毁掉画面；人物肖像里有老人与男性，也不能套少女标签。
PERSON_CATEGORIES: set[str] = {
    "美少女", "动漫少女", "真人少女", "写真摄影", "动漫角色", "游戏角色",
}

SCOPES_VALID = ("anime", "photo", "both")

SCOPE_LABEL_ZH: dict[str, str] = {
    "anime": "只画动漫（二次元插画）",
    "photo": "只画真人（写实摄影）",
    "both": "两者皆可",
}

# --------------------------------------------------------------------------- #
# 画风硬锚定（v0.7.6）
# 可画类型此前只是「题材闸门」，完全不进提示词：设了只画动漫，LLM 收到的简报和
# 系统提示词仍和没设一样，natural_en 那套甚至主动往写实写。这里把它升级成
# 「输入写进规则 + 输出硬拼字符串」的双保险，不依赖 LLM 自觉。
# 风景 / 星空 / 艺术 / 生态不是中性题材：它们同样要跟着工作流的画风走。
# --------------------------------------------------------------------------- #

ANCHOR_TAGS_ANIME = "anime style, anime, 2d, illustration, cel shading, drawn"
ANCHOR_TAGS_PHOTO = "photorealistic, photograph, realistic, photo, live action"
ANCHOR_NEG_ANIME = (
    "photorealistic, realistic, photo, photograph, 3d render, live action, real life, cgi"
)
ANCHOR_NEG_PHOTO = "anime, cartoon, illustration, 2d, drawing, painting, cel shading, sketch"
ANCHOR_PREFIX_NATURAL_ANIME = "一幅精美的二次元动漫插画，"
ANCHOR_PREFIX_NATURAL_PHOTO = "一张高质量的写实摄影作品，"
ANCHOR_PREFIX_EN_ANIME = "An exquisite anime-style illustration, "
ANCHOR_PREFIX_EN_PHOTO = "A high-quality photorealistic photograph, "
ANCHOR_NEG_ZH_ANIME = "写实照片，真实人像，3D 渲染，实拍，真人"
ANCHOR_NEG_ZH_PHOTO = "动漫，卡通，插画，二次元，手绘"

_SCOPE_RULES: dict[str, dict[str, str]] = {
    "anime": {
        "danbooru": (
            "【画风硬约束·最高优先级】本轮必须画成二次元动漫插画：prompt 里必须有 "
            "anime / 2d / illustration 类标签，禁止写实照片、真实人像、3D 渲染、实拍。"
            "风景、星空、静物等无人物题材也必须是动画/插画风格，不能是摄影照片。"
        ),
        "natural": (
            "【画风硬约束·最高优先级】本轮必须画成二次元动漫插画：描述开头就要点明"
            "「二次元动漫插画」，禁止写实照片、真实人像、实拍、3D 渲染。"
            "风景、星空、静物等无人物题材也必须是动画/插画风格。"
        ),
        "natural_en": (
            "[STYLE LOCK - HIGHEST PRIORITY] This image MUST be a 2D anime illustration, "
            "not a photograph. Begin the prompt with \"An anime-style illustration\". "
            "Never describe photorealistic / real-life / live-action / 3D-render content. "
            "Landscapes, starry skies and still life must also be drawn in animation / "
            "illustration style, never photographic."
        ),
    },
    "photo": {
        "danbooru": (
            "【画风硬约束·最高优先级】本轮必须画成写实摄影：prompt 里必须有 "
            "photorealistic / photograph / realistic 类标签，禁止二次元、动漫、插画、手绘。"
            "风景、星空、静物等无人物题材也必须是真实摄影质感。"
        ),
        "natural": (
            "【画风硬约束·最高优先级】本轮必须画成写实摄影：描述开头就要点明"
            "「写实摄影作品」，禁止二次元动漫、插画、手绘、卡通。"
            "风景、星空、静物等无人物题材也必须是真实摄影质感。"
        ),
        "natural_en": (
            "[STYLE LOCK - HIGHEST PRIORITY] This image MUST be a photorealistic photograph, "
            "not an anime illustration. Begin the prompt with \"A photorealistic photograph\". "
            "Never describe anime / cartoon / 2D illustration / painting content. "
            "Landscapes, starry skies and still life must also look like real photographs."
        ),
    },
}


def normalize_scope(scope) -> str:
    """把任意输入收敛成 anime / photo / both；未知值一律当不限制。"""
    v = str(scope or "both").strip().lower()
    return v if v in ("anime", "photo") else "both"


DEFAULT_PERSONA_TEXT = (
    "【人物设定·最高优先级】若画面中出现人物，人物一律为东亚面孔的年轻成年女性"
    "（18 岁以上），单人，无男性、无儿童、无中老年，不要其他人种或族裔。"
    "此条优先于题材与风格描述里的任何人物设定。涉及泳装、居家或室内写真时，"
    "保持自然、生活化的日常表达，不写任何露骨或性暗示内容。"
)
DEFAULT_PERSONA_TAGS = "1girl, solo, asian, east asian, young adult woman, adult female"
DEFAULT_PERSONA_NEGATIVE = (
    "old woman, elderly, mature female, milf, child, loli, underage, toddler, "
    "1boy, male, caucasian, western, dark skin"
)
DEFAULT_PERSONA_NEGATIVE_ZH = "儿童，未成年，中老年女性，男性，其他族裔"
# 与 _conf_schema.json 的 theme.character_names 默认值保持一致（test_schema 会校验）。
# 老配置没这个键时用它兜底，升级后不用手动补配置也能抽到角色名。
DEFAULT_CHARACTER_NAMES = (
    "动漫角色=初音未来,巡音流歌,阿尔托莉雅·潘德拉贡,葛城美里,蕾姆,拉姆,"
    "壹原侑子,牧濑红莉栖,时崎狂三,波雅·汉库克,妮可·罗宾,娜美\n"
    "游戏角色=甘雨,胡桃,雷电将军,刻晴,八重神子,神里绫华,荧,莫娜,优菈,宵宫,2B,"
    "蒂法·洛克哈特,爱丽丝·盖恩斯巴勒,玛修·基列莱特,贞德·达尔克,贝优妮塔"
)


# --------------------------------------------------------------------------- #
# v0.7.8：负面基线 / 标签预算 / danbooru 规范化
#
# 为什么要有负面基线：此前 negative 只有「LLM 自己写的 + 人物排除词 + 画风反向词」，
# 一旦模型偷懒给空串（契约允许），出图就完全没有质量兜底。这里给一套与风格匹配的
# 通用缺陷词，永远垫在最前（用户可在配置里改或清空）。
# --------------------------------------------------------------------------- #

NEGATIVE_BASE_EN = (
    "worst quality, low quality, lowres, jpeg artifacts, blurry, watermark, "
    "signature, text, username, bad anatomy, bad hands, extra fingers, "
    "fewer fingers, deformed, cropped"
)
NEGATIVE_BASE_ZH = (
    "低画质，模糊，噪点，水印，签名，文字，畸形，多余的手指，"
    "残缺的手指，结构崩坏，裁切不当"
)

# 标签预算：SD1.5 的 CLIP 只有 77 token，超出部分完全不生效；SDXL 分块编码但
# 越靠后的标签权重越低。默认 75 是「留一格给质量前缀」的经验值。
DEFAULT_MAX_TAGS = 75

# —— danbooru 标签清洗 ——
# CLIP 把 ( ) 当权重语法：角色 tag 不转义会被当权重算坏（萌绘同款事故）。
_WEIGHT_RE = re.compile(r"\(\s*([^():]*?)\s*:\s*[0-9]*\.?[0-9]+\s*\)")
_ARTIST_TAG_RE = re.compile(r"^(?:artist\s*:|by\s+)", re.I)
_PLACEHOLDER_L = "\x00L\x00"
_PLACEHOLDER_R = "\x00R\x00"


def split_tag_list(text) -> list:
    """逗号（中英文）/ 换行 / 顿号 → 去空标签列表。"""
    raw = str(text or "").replace("，", ",").replace("、", ",")
    return [t.strip() for t in re.split(r",|\n|\r", raw) if t.strip()]


def normalize_tags(
    text,
    *,
    escape_parens: bool = True,
    strip_weights: bool = True,
    drop_artists: bool = True,
    banned=None,
    max_tags: int = 0,
) -> "tuple[str, list]":
    """清洗 danbooru 串，返回 (清洗后文本, 被禁词剔除的标签列表)。

    顺序很关键：**先解权重再转义括号**。反过来的话 `(tag:1.2)` 会先变成
    `\\(tag:1.2\\)`，权重语法就再也解析不出来了。
    """
    tags = split_tag_list(text)
    out: list[str] = []
    seen: set[str] = set()
    banned_set = {str(b).strip().lower() for b in (banned or []) if str(b or "").strip()}
    dropped: list[str] = []
    for tag in tags:
        t = tag
        if strip_weights:
            # (tag:1.2) → tag；嵌套写法迭代两次足够常见情况
            for _ in range(2):
                t2 = _WEIGHT_RE.sub(r"\1", t)
                if t2 == t:
                    break
                t = t2
        if drop_artists and _ARTIST_TAG_RE.match(t.strip()):
            continue
        t = t.strip().strip(",").strip()
        if not t:
            continue
        if escape_parens:
            # 先把已转义的括号保护起来，避免 \\( 变成 \\\\(
            t = t.replace("\\(", _PLACEHOLDER_L).replace("\\)", _PLACEHOLDER_R)
            t = t.replace("(", "\\(").replace(")", "\\)")
            t = t.replace(_PLACEHOLDER_L, "\\(").replace(_PLACEHOLDER_R, "\\)")
        # 下划线写法与空格等价，统一成空格（dedupe 与可读性都更好）
        t = t.replace("_", " ").strip()
        low = t.lower()
        if banned_set and low in banned_set:
            dropped.append(t)
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(t)
        if max_tags and len(out) >= int(max_tags):
            break
    return ", ".join(out), dropped


def normalize_payload(payload: dict, tag_style: str, opts: dict | None = None) -> dict:
    """按风格清洗 LLM 输出：danbooru 走标签管道，禁词剔除并塞进 negative。

    自然语言风格原样返回 —— 中文正文里的括号/下划线是正常标点，不能按标签处理。
    """
    out = dict(payload or {})
    opts = opts or {}
    key = _style_key(str(tag_style or ""))
    if key != "danbooru":
        return out
    banned = split_tag_list(opts.get("banned_tags"))
    # 缺键 → 用代码默认；显式 0 → 不限（别把 0 当成「没填」）
    raw_max = opts.get("max_tags", DEFAULT_MAX_TAGS)
    try:
        max_tags = int(raw_max)
    except (TypeError, ValueError):
        max_tags = DEFAULT_MAX_TAGS
    if max_tags < 0:
        max_tags = 0
    prompt, dropped = normalize_tags(
        out.get("prompt", ""),
        drop_artists=opt_bool(opts.get("strip_artists"), True),
        banned=banned,
        max_tags=max_tags,
    )
    negative, _ = normalize_tags(
        out.get("negative", ""), drop_artists=False, banned=[], max_tags=0
    )
    if dropped:
        # 用户明确不要的东西，顺手写进负向（只删正向等于没管住）
        negative = _merge_tags(negative, ", ".join(dropped), False)
    out["prompt"] = prompt
    out["negative"] = negative
    return out


def apply_negative_base(payload: dict, tag_style: str, opts: dict | None = None) -> dict:
    """把通用缺陷词垫到 negative 最前（english 线 / 中文线各一套，可清空）。

    缺键用代码常量兜底（老配置升级后没这个键也要有质量保底）；
    显式空串 = 用户主动关掉，就不再追加。
    """
    out = dict(payload or {})
    opts = opts or {}
    key = _style_key(str(tag_style or ""))
    field = "negative_base_zh" if key == "natural" else "negative_base_en"
    raw = opts.get(field)
    if raw is None:
        base = NEGATIVE_BASE_ZH if key == "natural" else NEGATIVE_BASE_EN
    else:
        base = str(raw)
    base = base.strip()
    if not base:
        return out
    out["negative"] = _merge_tags(base, out.get("negative", ""), False)
    return out


def style_pool_for(category: str, scope: str = "both") -> list:
    """类别 × 工作流可画类型 → 风格池。

    v0.7.6：可画类型优先。风景/星空等「两者皆可」的题材不是中性画风，
    只画动漫时它们也得用动漫风格池（此前会抽到「胶片摄影颗粒感」而画成照片）。
    """
    sc = normalize_scope(scope)
    if sc == "anime":
        return STYLES_ANIME
    if sc == "photo":
        return STYLES_PHOTO
    group = CATEGORY_STYLE_GROUP.get(str(category or ""))
    if group == "anime":
        return STYLES_ANIME
    if group == "photo":
        return STYLES_PHOTO
    return STYLES


def scope_of(category: str, scopes: dict | None = None) -> str:
    """类别 → 可画类型（配置 theme.category_scopes 可覆盖内置映射）。"""
    v = str((scopes or {}).get(category) or CATEGORY_SCOPE.get(category, "both")).strip().lower()
    return v if v in SCOPES_VALID else "both"


def scope_allows(workflow_scope: str, category: str, scopes: dict | None = None) -> bool:
    """工作流能力 × 类别需求：both 永远放行，其余必须同类。"""
    ws = str(workflow_scope or "both").strip().lower()
    if ws not in ("anime", "photo"):
        return True
    need = scope_of(category, scopes)
    return need in ("both", ws)


def parse_scopes(raw) -> dict:
    """解析「类别=anime|photo|both」文本（或字典）；非法值丢弃。"""
    items = list(raw.items()) if isinstance(raw, dict) else [
        (p.partition("=")[0], p.partition("=")[2])
        for p in re.split(r"[,，;；\n\r]+", str(raw or "")) if p.strip()
    ]
    out: dict[str, str] = {}
    for name, val in items:
        key, v = str(name).strip(), str(val).strip().lower()
        if key and v in SCOPES_VALID:
            out[key] = v
    return out


def parse_kv_words(raw) -> dict:
    """「键=词1,词2」每行一条（也兼容逗号分隔多行）→ {键: [词…]}。

    用于 theme.character_names：角色名单按类别注入题材池，用户可直接删改配置文本。
    """
    out: dict[str, list[str]] = {}
    for line in str(raw or "").replace("，", ",").splitlines():
        name, sep, val = line.partition("=")
        if not sep:
            name, sep, val = line.partition("：")
        key = str(name).strip()
        if not sep or not key:
            continue
        words = [w.strip() for w in str(val or "").split(",") if w.strip()]
        if words:
            out.setdefault(key, []).extend(words)
    return out


def theme_pool(scopes: dict | None = None) -> dict:
    """给 WebUI 的词池全貌（内置类别 / 题材 / 风格组 / 能力 / 四个附加层）。"""
    return {
        "categories": [
            {
                "name": name,
                "subjects": list(words),
                "style_group": CATEGORY_STYLE_GROUP.get(name, "all"),
                "scope": scope_of(name, scopes),
            }
            for name, words in CATEGORIES.items()
        ],
        "layers": {
            "风格": {"all": list(STYLES), "anime": list(STYLES_ANIME), "photo": list(STYLES_PHOTO)},
            "光色": list(LIGHTS),
            "氛围": list(MOODS),
            "构图": [f"{n}@{o}" for n, o in COMPOSITIONS],
        },
    }

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

# 镜头层（只给写实线）：写真与肖像差一口气常常就差「什么镜头」。
# 写成中文短语，让 LLM 自己转成目标语言；danbooru 系会写成 85mm lens / depth of field 类标签。
LENS_PHOTO: list[str] = [
    "85mm 定焦 f/1.8 浅景深", "50mm 标准镜头 f/2.8", "135mm 长焦压缩感 f/2",
    "35mm 环境人像 f/2.8", "广角 24mm 低角度", "微距特写浅景深",
    "胶片 35mm 颗粒感", "中画幅柔和高宽容度", "长焦逆光轮廓光",
]

# 媒介层（只给动漫线）：插画的「成品形态」——立绘 / 关键帧 / 封面 / 壁纸
MEDIUM_ANIME: list[str] = [
    "角色立绘（无背景或渐变背景）", "动画关键帧质感", "轻小说封面插画",
    "游戏宣传图构图", "桌面壁纸构图", "画集跨页插画", "分镜特写画面",
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


# 默认类别权重：少女题材占大头（动漫:真人 ≈ 3:1），其余类别各 1 保底多样性。
# 用户可在「主题词池」页改成任意「类别=权重」。
DEFAULT_CATEGORY_WEIGHTS = (
    "美少女=5,动漫少女=4,真人少女=3,写真摄影=2,动漫角色=1,游戏角色=1,"
    "人物肖像=1,风景场景=1,宇宙星空=1,艺术创意=1,自然生态=1"
)


def parse_weights(raw) -> dict:
    """解析「类别=权重」文本（或字典）；非法项丢弃，负值归零，上限 100。"""
    if isinstance(raw, dict):
        items = list(raw.items())
    else:
        items = []
        for part in re.split(r"[,，;；\n\r]+", str(raw or "")):
            if not part.strip():
                continue
            name, sep, val = part.partition("=")
            if not sep:
                name, sep, val = part.partition("：")
            if not sep:
                name, sep, val = part.partition(":")
            items.append((name, val))
    out: dict[str, float] = {}
    for name, val in items:
        key = str(name).strip()
        if not key:
            continue
        try:
            w = float(str(val).strip())
        except (TypeError, ValueError):
            continue
        out[key] = min(100.0, max(0.0, w))
    return out


def weighted_choice(rng: random.Random, keys: list, weights: dict) -> str:
    """按权重抽一个键；没配置 / 全为 0 → 退回均匀随机（绝不抽不出）。"""
    pairs = [(k, float(weights.get(k, 1.0))) for k in keys]
    pairs = [(k, w) for k, w in pairs if w > 0]
    if not pairs:
        return rng.choice(list(keys))
    total = sum(w for _, w in pairs)
    r = rng.random() * total
    acc = 0.0
    for k, w in pairs:
        acc += w
        if r <= acc:
            return k
    return pairs[-1][0]


def category_distribution(categories: list, weights: dict) -> list:
    """给 UI 用：[{name, weight, pct}]，权重缺省为 1，全 0 时按均匀算。"""
    pairs = [(str(c), float((weights or {}).get(c, 1.0))) for c in categories]
    pairs = [(c, w) for c, w in pairs if w > 0]
    total = sum(w for _, w in pairs) or 1.0
    return [
        {"name": c, "weight": w, "pct": round(w / total * 100, 1)}
        for c, w in pairs
    ]


def draw_brief(
    rng: random.Random,
    disabled_categories: set[str] | None = None,
    extra: dict | None = None,
    weights: dict | None = None,
    scope: str = "both",
    scopes: dict | None = None,
    phase: str = "",
) -> dict:
    """分层抽签生成创作简报。

    extra（配置 theme_extra）的键：
    - 内置类别名（如「宇宙星空」）→ 值为逗号分隔的追加题材；
    - 其他键 → 新类别名，值为逗号分隔的题材；
    - 「风格」「光色」「氛围」→ 追加对应层的词；
    - 「构图」→ 追加构图项，格式「名称@landscape|portrait|square」。
    weights（配置 theme.category_weights）：类别 → 权重，缺省 1.0；停用的类别无论如何都不抽。
    scope（当次工作流的能力 anime / photo / both）：先按能力筛掉画不了的类别再加权抽。
    scopes（配置 theme.category_scopes）：类别 → 能力覆盖，自定义类别可用它标记。
    """
    extra = extra or {}
    disabled = disabled_categories or set()
    weights = weights or {}
    scopes = scopes or {}
    wf_scope = str(scope or "both").strip().lower()
    if wf_scope not in ("anime", "photo", "both"):
        wf_scope = "both"

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

    # 工作流能力闸门：动漫底模不抽写真、写实底模不抽二次元角色。
    # 筛完为空说明用户把该能力下的类别全停了 —— 退回原池，绝不抽不出题材。
    if wf_scope in ("anime", "photo"):
        filtered = {k: v for k, v in cat_pool.items() if scope_allows(wf_scope, k, scopes)}
        if filtered:
            cat_pool = filtered

    category = weighted_choice(rng, sorted(cat_pool.keys()), weights)
    subject = rng.choice(cat_pool[category])
    style = rng.choice(_merge_words(style_pool_for(category, wf_scope), extra.get("风格")))
    # 光色池按时段收窄（v0.7.9）：凌晨三点不该抽到「黄昏暖光」。
    # 用户自己追加的光色词不参与过滤 —— 那是显式意图，不该被时段否决。
    extra_lights = _merge_words([], extra.get("光色"))
    light_pool = daylight.filter_lights(LIGHTS, phase) if phase else list(LIGHTS)
    light = rng.choice(light_pool + extra_lights) if (light_pool or extra_lights) else "自然光"
    mood = rng.choice(_merge_words(MOODS, extra.get("氛围")))

    comp_pool = list(COMPOSITIONS)
    for item in _merge_words([], extra.get("构图")):
        # 格式：名称@orientation
        name, _, ori = item.partition("@")
        if name and ori in ("landscape", "portrait", "square"):
            comp_pool.append((name, ori))
    comp_name, orientation = rng.choice(comp_pool)

    # 镜头 / 媒介层（v0.7.8）：写真线差一口气常常就差「什么镜头」，
    # 动漫线差的是「成品形态」（立绘/封面/壁纸）。写实与动漫各抽一条，
    # both 且没有风格组的题材（风景/星空…）不硬加。
    group = "photo" if wf_scope == "photo" else (
        "anime" if wf_scope == "anime" else CATEGORY_STYLE_GROUP.get(category, "all")
    )
    lens = rng.choice(LENS_PHOTO) if group == "photo" else ""
    medium = rng.choice(MEDIUM_ANIME) if group == "anime" else ""

    brief = {
        "category": category,
        "subject": subject,
        "style": style,
        "composition": comp_name,
        "orientation": orientation,
        "light": light,
        "mood": mood,
        "scope": wf_scope,
        "lens": lens,
        "medium": medium,
    }
    if phase:
        brief["phase"] = str(phase)
    return brief


def brief_text(brief: dict, theme: str = "") -> str:
    """把简报排成给 LLM 的一行简报文本。theme 非空时覆盖题材（指令「画一张 主题词」）。

    v0.7.6：带上「可画类型」。此前 brief 里有 scope 却被丢弃，LLM 根本不知道
    这一轮要画二次元还是写实。
    """
    subject = theme.strip() or brief.get("subject", "")
    scope = SCOPE_LABEL_ZH[normalize_scope(brief.get("scope"))]
    text = (
        f"类别={brief.get('category', '')}；题材={subject}；风格={brief.get('style', '')}；"
        f"可画类型={scope}；"
        f"构图={brief.get('composition', '')}；光色={brief.get('light', '')}；"
        f"氛围={brief.get('mood', '')}"
    )
    # 镜头 / 媒介只在抽到时有（写实线才有镜头，动漫线才有媒介）
    if str(brief.get("lens") or "").strip():
        text += f"；镜头={brief['lens']}"
    if str(brief.get("medium") or "").strip():
        text += f"；成品形态={brief['medium']}"
    if str(brief.get("phase") or "").strip():
        text += f"；时段={daylight.phase_label(brief['phase'])}"
    if str(brief.get("character") or "").strip():
        text += f"；角色={brief['character']}"
    return text


# --------------------------------------------------------------------------- #
# 系统提示词（三套内置 + 自定义规则 / 完全覆盖）
# --------------------------------------------------------------------------- #

_SYSTEM_COMMON_TAIL = """
【输出契约】只输出一个 JSON 对象，禁止任何解释、前后缀或代码围栏：
{"prompt": "...", "negative": "...", "caption": "..."}
- caption：发到群里的一句话，中文 20~40 字，有画面感，不要罗列标签。
- negative 不确定就给空串。JSON 以外的任何字符都算错误。
"""

# 三套「主体」：输出契约由 build_system_prompt 统一拼在末尾，附加规则插在契约前。
_SYSTEM_BODIES: dict[str, str] = {
    "natural": (
        "你是一位顶级 AI 绘画提示词工程师，为支持中文的模型（如 Qwen-Image）写文生图提示词。\n"
        "用户会给你一份「创作简报」，请把它写成高质量的中文画面描述。\n"
        "【prompt 要求】2~4 句连贯中文：主体与细节 → 环境与背景 → 镜头与构图 → "
        "光线与氛围。严格贴合简报的题材/风格/构图/光色/氛围（简报给了镜头或成品形态就照写），不要偏题，"
        "不要写成标签堆砌，总长 80~200 字。\n"
        "【negative 要求】中文，只写常见缺陷（如：模糊，畸形，多余的手指，"
        "低画质，水印），不确定就空串。\n"
    ),
    "natural_en": (
        "You are a top-tier prompt engineer writing text-to-image prompts for "
        "natural-language models (Flux / SD3 / photorealistic SDXL checkpoints).\n"
        "The user gives you a Chinese creative brief. Turn it into a high-quality "
        "English prompt.\n"
        "【prompt】2-4 flowing English sentences: subject and details -> environment "
        "and background -> camera and composition -> lighting and mood. Follow the "
        "brief's subject/style/composition/light/mood (and lens/format when given) "
        "exactly; do not drift into a "
        "generic portrait; no tag dumps, no artist names, 60-150 words.\n"
        "【negative】English, short, only common defects (blurry, deformed, extra "
        "fingers, low quality, watermark); empty string if unsure.\n"
        "【caption】Remains Chinese, 20-40 characters, evocative, no tag lists.\n"
    ),
    "danbooru": (
        "你是一位顶级 AI 绘画提示词工程师，为动漫风格模型（Danbooru 标签系）写文生图提示词。\n"
        "用户会给你一份「创作简报」，请把它扩展成一条高质量英文 Danbooru 标签串。\n"
        "【prompt 要求】\n"
        "1. 小写英文标签，逗号分隔，不要写成句子；30~55 个标签。\n"
        "2. 从主到次：内容型主标签（1girl/1boy/scenery/no humans 等按内容定）→ "
        "主体外观细节 → 服装 → 动作姿势 → 场景背景 → 构图视角 → 光线氛围。\n"
        "3. 严格贴合简报的题材/风格/构图/光色/氛围（简报给了镜头或成品形态就照写），"
        "不要偏题，不要偷换成「站在街边的普通少女」。\n"
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
    ),
}


def _style_key(tag_style: str) -> str:
    if tag_style in ("natural", "natural_zh"):
        return "natural"
    if tag_style == "natural_en":
        return "natural_en"
    return "danbooru"


def opt_bool(v, default: bool = True) -> bool:
    """配置里的 bool 可能是字符串（AstrBotConfig / WebUI 都出现过）。"""
    if v is None or (isinstance(v, str) and not v.strip()):
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on", "开启", "开")


def build_system_prompt(
    tag_style: str, opts: dict | None = None, scope: str = "both", phase: str = ""
) -> str:
    """按提示词风格 + 工作流可画类型给系统提示词。

    opts（配置 prompt 分组）：
    - ``override_<style>`` 非空 → 完全替换该风格的系统提示词；
      若替换文本没写 JSON 输出契约，自动补上，避免整轮判失败。
    - ``persona_enabled`` / ``persona_text`` → 人物锚定规则段；
    - ``extra_rules`` → 用户自定义规则段；
    - ``style_anchor_enabled`` → 是否插入画风硬规则（默认开）。
    ``scope`` 为 anime / photo 时追加画风硬规则段；它是插件级硬约束，
    即使完全覆盖系统提示词也照样追加，避免用户改完提示词又画成真人。
    """
    opts = opts or {}
    key = _style_key(str(tag_style or ""))
    sc = normalize_scope(scope)
    rule = ""
    if sc != "both" and opt_bool(opts.get("style_anchor_enabled"), True):
        rule = _SCOPE_RULES[sc][key]
    # 时段硬规则（v0.7.9）：与画风规则同级，完全覆盖系统提示词时也照样追加
    time_rule = daylight.rule_for_phase(phase) if phase else ""

    override = str(opts.get(f"override_{key}") or "").strip()
    if override:
        if "json" not in override.lower():
            override = override.rstrip() + "\n" + _SYSTEM_COMMON_TAIL.strip()
        if rule:
            override = override.rstrip() + "\n" + rule + "\n"
        if time_rule:
            override = override.rstrip() + "\n" + time_rule + "\n"
        return override

    body = _SYSTEM_BODIES[key]
    rules: list[str] = []
    if opt_bool(opts.get("persona_enabled"), True):
        persona = str(opts.get("persona_text") or "").strip()
        if persona:
            rules.append(persona)
    extra = str(opts.get("extra_rules") or "").strip()
    if extra:
        rules.append(extra)
    if rule:
        rules.append(rule)
    if time_rule:
        rules.append(time_rule)
    if rules:
        body += "\n【附加规则（优先于以上通用要求）】\n" + "\n".join(rules) + "\n"
    return body + _SYSTEM_COMMON_TAIL


def _merge_tags(text: str, tags: str, front: bool) -> str:
    """把标签补进文本，已存在的（不区分大小写）不重复加。

    中英文逗号都认：中文排除词（如「儿童，未成年」）此前整段被当成**一个**标签，
    dedupe 等于没做，还会把整串粘在末尾。
    """
    cur = str(text or "").strip().strip(",，").strip()
    parts = split_tag_list(tags)
    have = {t.lower() for t in split_tag_list(cur)}
    missing = [t for t in parts if t.lower() not in have]
    if not missing:
        return cur
    if not cur:
        return ", ".join(missing)
    return (", ".join(missing) + ", " + cur) if front else (cur + ", " + ", ".join(missing))


def apply_persona(
    payload: dict, tag_style: str, brief: dict, opts: dict | None = None,
    scope: str = "both",
) -> dict:
    """硬约束落地：人物锚定 + 画风锚定，全部直接改字符串，不依赖 LLM 自觉。

    人物锚定（``persona_enabled``）：
    - 正面人物标签只给 PERSON_CATEGORIES（景物类硬塞 1girl 会毁画面）；
    - 排除负面词 **所有类别都加**。v0.7.6 之前非人物类整段 return，导致景物图里
      冒出老人 / 男人 / 儿童，而系统提示词里的中文规则对 natural_en 又太弱。

    画风锚定（``style_anchor_enabled``，独立开关）：
    - scope=anime / photo 时把画风标签或句首画风前缀硬拼进 prompt、把反向词加进
      negative；风景 / 星空 / 艺术 / 生态这类 both 题材也照样跟着工作流的画风走。
    - 自然语言风格不插标签（会破坏句子），改在句首点明画风。
    """
    out = dict(payload or {})
    opts = opts or {}
    key = _style_key(str(tag_style or ""))
    cat = str((brief or {}).get("category") or "")

    if opt_bool(opts.get("persona_enabled"), True):
        if cat in PERSON_CATEGORIES and key == "danbooru":
            out["prompt"] = _merge_tags(
                out.get("prompt", ""),
                str(opts.get("persona_tags") or DEFAULT_PERSONA_TAGS),
                True,
            )
        neg = (
            str(opts.get("persona_negative_zh") or DEFAULT_PERSONA_NEGATIVE_ZH)
            if key == "natural"
            else str(opts.get("persona_negative") or DEFAULT_PERSONA_NEGATIVE)
        )
        out["negative"] = _merge_tags(out.get("negative", ""), neg, False)

    if not opt_bool(opts.get("style_anchor_enabled"), True):
        return out
    sc = normalize_scope(scope)
    if sc == "both":
        return out

    if key == "danbooru":
        out["prompt"] = _merge_tags(
            out.get("prompt", ""),
            ANCHOR_TAGS_ANIME if sc == "anime" else ANCHOR_TAGS_PHOTO,
            True,
        )
        out["negative"] = _merge_tags(
            out.get("negative", ""),
            ANCHOR_NEG_ANIME if sc == "anime" else ANCHOR_NEG_PHOTO,
            False,
        )
        return out

    cur = str(out.get("prompt", "")).strip()
    low = cur.lower()
    hinted = (
        ("anime" in low or "插画" in cur or "二次元" in cur)
        if sc == "anime"
        else ("photorealistic" in low or "photograph" in low or "写实" in cur)
    )
    if not hinted:
        if key == "natural":
            prefix = ANCHOR_PREFIX_NATURAL_ANIME if sc == "anime" else ANCHOR_PREFIX_NATURAL_PHOTO
        else:
            prefix = ANCHOR_PREFIX_EN_ANIME if sc == "anime" else ANCHOR_PREFIX_EN_PHOTO
        out["prompt"] = (prefix + cur.lstrip("，,。. ")) if cur else prefix.rstrip("，, ")
    neg = (
        (ANCHOR_NEG_ZH_ANIME if sc == "anime" else ANCHOR_NEG_ZH_PHOTO)
        if key == "natural"
        else (ANCHOR_NEG_ANIME if sc == "anime" else ANCHOR_NEG_PHOTO)
    )
    out["negative"] = _merge_tags(out.get("negative", ""), neg, False)
    return out


def apply_character(payload: dict, card: dict | None, tag_style: str) -> dict:
    """把角色卡并进 payload（v0.7.9）。

    - danbooru：角色标签前置（`hatsune miku, twintails…`），负向合并卡片的排除词；
    - 自然语言：不插标签串（会破坏句子），角色名已经写进简报交给 LLM 描述。
    负向对所有风格都合并（挡住「画错人」的常见描述）。
    """
    out = dict(payload or {})
    if not card:
        return out
    tags = str(card.get("tags") or "").strip()
    neg = str(card.get("negative") or "").strip()
    if tags and _style_key(str(tag_style or "")) == "danbooru":
        out["prompt"] = _merge_tags(out.get("prompt", ""), tags, True)
    if neg:
        out["negative"] = _merge_tags(out.get("negative", ""), neg, False)
    return out


def build_user_prompt(brief: dict, theme: str = "") -> str:
    return "创作简报：" + brief_text(brief, theme)


# --------------------------------------------------------------------------- #
# JSON 契约解析
# --------------------------------------------------------------------------- #

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _extract_json_object(raw: str) -> str:
    """从可能带围栏/解释的文本里抠出最外层 {…}；抠不出返回空串。"""
    text = str(raw or "").strip()
    m = _FENCE_RE.search(text)
    if m:
        text = m.group(1).strip()
    if text.startswith("{"):
        return text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return ""
    return text[start : end + 1]


def parse_score_payload(text) -> "dict | None":
    """解析质检输出：{"score": 0~100, "issues": [...]}；不合契约返回 None。

    宽容处理（质检模型不保证听话）：允许围栏/前后解释、score 写成 "82" 或 82.0、
    issues 写成字符串或数组。分数越界直接夹到 0~100。
    """
    raw = _extract_json_object(str(text or ""))
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        score = float(str(data.get("score")).strip())
    except (TypeError, ValueError):
        return None
    score = int(max(0, min(100, round(score))))
    issues = data.get("issues")
    if isinstance(issues, (list, tuple)):
        text_issues = "；".join(str(x).strip() for x in issues if str(x or "").strip())
    else:
        text_issues = str(issues or "").strip()
    return {"score": score, "issues": text_issues[:300]}


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
    sizes: dict, orientation: str, composition_name: str, rng: random.Random,
    ratio: float | None = None,
) -> tuple[int, int]:
    """按「朝向 + 构图」从模板白名单落宽高 —— 每张图各不相同。

    1. 朝向（横/竖/方）决定取哪一组候选，朝向由当次抽签的构图决定；
    2. 组内按构图名匹配目标长宽比（超广角/全景/俯瞰偏横，全身像偏瘦高，
       特写/半身像偏 3:4），选出最接近的若干档随机取一个；
    3. 构图认不出（自定义词池）→ 组内纯随机；
    4. 用户用 `--ratio 3:2` 显式指定比例时，以它为准（覆盖构图推断）。
    """
    cands = (sizes or {}).get(orientation) or (sizes or {}).get("square") or [[1024, 1024]]
    cands = [[int(c[0]), int(c[1])] for c in cands if len(c) >= 2]
    if not cands:
        return 1024, 1024
    if len(cands) == 1:
        return cands[0][0], cands[0][1]
    try:
        target = float(ratio) if ratio else composition_ratio(composition_name)
    except (TypeError, ValueError):
        target = composition_ratio(composition_name)
    if target is None:
        pick = rng.choice(cands)
    else:
        scored = sorted(cands, key=lambda wh: abs(_ratio_of(wh) - target))
        best = abs(_ratio_of(scored[0]) - target)
        pool = [wh for wh in scored if abs(_ratio_of(wh) - target) <= best + _RATIO_TOLERANCE]
        pick = rng.choice(pool)
    return int(pick[0]), int(pick[1])


# --------------------------------------------------------------------------- #
# 指令参数（v0.7.8）：/绘卷 画一张 少女 --no blurry --style 胶片 --ratio 3:2 --seed 42
# --------------------------------------------------------------------------- #

_RATIO_ALIAS = {
    "横": (1.78, "landscape"), "横图": (1.78, "landscape"), "横版": (1.78, "landscape"),
    "竖": (1.78, "portrait"), "竖图": (1.78, "portrait"), "竖版": (1.78, "portrait"),
    "方": (1.0, "square"), "正方": (1.0, "square"), "正方形": (1.0, "square"),
    "方形": (1.0, "square"),
}


def parse_ratio(text) -> "tuple[float | None, str]":
    """"3:2" / "3x2" / "横" / "竖" / "方" → (长宽比, 朝向)；认不出返回 (None, "")。"""
    raw = str(text or "").strip().lower().replace("：", ":").replace("×", "x").replace("x", ":")
    raw = raw.replace("比", ":")
    if not raw:
        return None, ""
    if raw in _RATIO_ALIAS:
        return _RATIO_ALIAS[raw]
    if ":" in raw:
        a, _, b = raw.partition(":")
        try:
            w, h = float(a), float(b)
        except (TypeError, ValueError):
            return None, ""
        if w <= 0 or h <= 0:
            return None, ""
        if abs(w - h) < 1e-9:
            return 1.0, "square"
        return round(max(w, h) / min(w, h), 3), ("landscape" if w > h else "portrait")
    return None, ""


def parse_draw_args(text) -> dict:
    """解析「画一张」后面的主题词与开关（顺序任意，`--k v` 与 `--k=v` 都认）。

    --no     追加负向词（逗号分隔）
    --style  强制风格，覆盖抽签的风格层（如 "胶片摄影颗粒感"）
    --ratio  长宽比（3:2 / 横 / 竖 / 方），同时决定朝向
    --seed   固定种子，便于复现
    --char   指定角色卡（按名字或别名命中）
    认不出的开关写进 error，主题词是其余所有文本。纯函数，便于自检。
    """
    tokens = str(text or "").split()
    out: dict = {
        "theme": "", "negative": "", "style": "", "ratio": None,
        "orientation": "", "seed": None, "char": "", "error": "",
    }
    rest: list[str] = []
    neg: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if not tok.startswith("--"):
            rest.append(tok)
            i += 1
            continue
        key, _, inline = tok.partition("=")
        key = key.strip().lower()
        vals: list[str] = [inline] if inline else []
        i += 1
        while i < len(tokens) and not tokens[i].startswith("--"):
            vals.append(tokens[i])
            i += 1
        value = " ".join(v for v in vals if v).strip()
        if key in ("--no", "--negative"):
            if value:
                neg.append(value)
        elif key in ("--style", "--风格"):
            out["style"] = value[:30]
        elif key in ("--ratio", "--ar", "--比例"):
            ratio, ori = parse_ratio(value)
            if ratio is None:
                out["error"] = f"看不懂的长宽比：{value}（写 3:2 / 横 / 竖 / 方）"
            else:
                out["ratio"], out["orientation"] = ratio, ori
        elif key in ("--seed", "--种子"):
            try:
                out["seed"] = abs(int(float(value))) % (2**63)
            except (TypeError, ValueError):
                out["error"] = f"种子必须是整数：{value}"
        elif key in ("--char", "--角色"):
            out["char"] = value[:30]
        else:
            out["error"] = (
                f"不认识的开关：{key}（可用 --no / --style / --ratio / --seed / --char）"
            )
    out["theme"] = " ".join(rest).strip()
    out["negative"] = ", ".join(x for x in neg if x)
    return out
