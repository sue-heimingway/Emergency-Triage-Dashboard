"""底座一：医学术语语义库（把"大白话"翻成"黑话"）。

这一层不做任何判断，只负责四件事：
  1. 把患者/家属的口语，对齐到标准症状术语（含否定词识别）；
  2. 把"撕裂样 / 压榨样 / 放射到后背"这类修饰，挂到对应症状上；
  3. 从自由文本里抠出生命体征；
  4. 解析旧化验单（文本），标出异常项与危急值。

设计原则：
  - 词典数据与算法分离，加词只需要改 SYMPTOMS / HISTORY 列表；
  - 长词优先匹配，同一段文字不会被两个术语重复吃掉；
  - 宁可漏翻译，不可错否定（否定识别比正向识别保守）。
"""
from __future__ import annotations

import re
from typing import Any

# ======================================================================
# 一、症状词典
#   id     : 内部主键（规则库用它做判断）
#   term   : 标准术语（给医生看的中文）
#   lay    : 患者可能说出的口语（越长越优先）
#   urgent : 0 低危 / 1 需关注 / 2 高危 / 3 极危（用于兜底排序）
# ======================================================================

SYMPTOMS: list[dict[str, Any]] = [
    # ---------------- 胸部 ----------------
    {
        "id": "chest_pain", "term": "胸痛", "urgent": 2,
        "lay": ["胸口疼", "胸口痛", "胸口疼得厉害", "心口疼", "心口痛", "胸前痛", "前胸痛",
                "胸骨后痛", "胸里头疼", "胸口不舒服", "胸口堵", "胸口闷", "心口闷", "胸痛", "胸疼"],
        "note": "急诊胸痛必须先排除致命性病因：心梗、主动脉夹层、肺栓塞、张力性气胸",
    },
    {
        "id": "dyspnea", "term": "呼吸困难", "urgent": 2,
        "lay": ["喘不上气", "喘不上来", "喘不过气", "上不来气", "憋气", "憋得慌", "气短",
                "透不过气", "呼吸困难", "出气费劲", "吸不上氧", "喘得厉害"],
        "note": "呼吸困难 + 低氧需先判断气道与氧合",
    },
    {
        "id": "orthopnea", "term": "端坐呼吸", "urgent": 2,
        "lay": ["躺不下", "一躺下就喘", "不能平躺", "要坐着才能喘", "平躺就憋"],
        "note": "提示心源性肺水肿",
    },
    {
        "id": "palpitation", "term": "心悸", "urgent": 1,
        "lay": ["心跳得厉害", "心里发慌", "心慌", "心跳快", "心跳乱", "心脏乱跳", "扑通扑通"],
    },
    {
        "id": "syncope", "term": "晕厥", "urgent": 2,
        "lay": ["晕倒", "晕过去了", "眼前一黑", "眼前发黑", "失去意识", "断片", "摔倒没知觉", "晕厥"],
        "note": "晕厥需鉴别心源性、体位性、神经源性",
    },
    {
        "id": "hemoptysis", "term": "咯血", "urgent": 2,
        "lay": ["咳血", "吐血丝", "痰里带血", "咳出血", "咯血"],
    },
    {
        "id": "cough", "term": "咳嗽", "urgent": 0,
        "lay": ["咳嗽", "干咳", "老咳", "咳得不停"],
    },
    # ---------------- 神经 ----------------
    {
        "id": "headache_thunderclap", "term": "突发剧烈头痛", "urgent": 3,
        "lay": ["头要炸了", "头像炸开", "头要炸开", "脑袋要炸开", "头疼得要炸开", "炸裂样头痛",
                "突然头疼得厉害", "这辈子最痛的头痛", "雷击样头痛", "从没这么痛过",
                "突然剧烈头痛", "头快裂了", "爆炸样头痛"],
        "note": "雷击样头痛首选排除蛛网膜下腔出血",
    },
    {
        "id": "headache", "term": "头痛", "urgent": 1,
        "lay": ["头疼", "头痛", "脑袋疼", "头胀"],
    },
    {
        "id": "hemiparesis", "term": "肢体无力/瘫痪", "urgent": 3,
        "lay": ["半边身子没劲", "手抬不起来", "腿抬不起来", "胳膊抬不起来", "抬不起来",
                "一侧没力气", "半身不遂", "腿软走不了", "胳膊抬不动", "一边身体动不了",
                "手脚麻木没劲", "往一边倒", "走路跑偏"],
        "note": "卒中通道：记录最后正常时间（last known well）",
    },
    {
        "id": "speech_disturbance", "term": "言语不清/失语", "urgent": 3,
        "lay": ["说话不清楚", "说话说不清楚", "说话说不清", "说话说不利索", "说话含糊", "说话不清",
                "话说不清", "讲话不清楚", "说不出话", "舌头打结", "讲话大舌头", "吐字不清",
                "听不懂别人说话", "失语"],
    },
    {
        "id": "facial_droop", "term": "口角歪斜", "urgent": 3,
        "lay": ["嘴歪了", "脸歪了", "嘴巴歪", "嘴角下垂", "面瘫"],
    },
    {
        "id": "seizure", "term": "抽搐", "urgent": 3,
        "lay": ["抽风", "抽搐", "口吐白沫", "抽过去了", "四肢抽动", "癫痫发作"],
        "note": "注意与「腿抽筋」区分，后者多为局部肌肉痉挛",
    },
    {
        "id": "altered_mental", "term": "意识改变", "urgent": 3,
        "lay": ["叫不醒", "叫不太醒", "喊不太醒", "叫不应", "迷糊", "说胡话", "反应慢", "没反应",
                "神志不清", "昏昏沉沉", "意识模糊", "人不清醒", "嗜睡", "答非所问"],
    },
    {
        "id": "vertigo", "term": "眩晕", "urgent": 1,
        "lay": ["天旋地转", "头晕目眩", "房子在转", "站不稳", "头晕"],
    },
    {
        "id": "neck_stiffness", "term": "颈项强直", "urgent": 3,
        "lay": ["脖子硬", "脖子发硬", "脖子发僵", "脖子僵", "低头下巴碰不到胸口", "脖子转不动", "颈项强直"],
        "note": "脑膜刺激征：警惕颅内感染 / 蛛网膜下腔出血",
    },
    {
        "id": "vision_loss", "term": "视力改变", "urgent": 2,
        "lay": ["看不见了", "视力模糊", "突然眼黑", "一只眼看不见", "重影"],
    },
    # ---------------- 腹部 ----------------
    {
        "id": "abdominal_pain", "term": "腹痛", "urgent": 1,
        "lay": ["肚子疼", "肚子痛", "腹痛", "肚子不舒服", "肚子胀痛", "小肚子疼"],
    },
    {
        "id": "epigastric_pain", "term": "上腹痛", "urgent": 2,
        "lay": ["胃疼", "胃痛", "心窝下疼", "心窝疼", "上腹部疼", "中上腹疼", "剑突下疼"],
        "note": "「胃痛」要先排除下壁心梗，这是最经典的误诊",
    },
    {
        "id": "rlq_pain", "term": "右下腹痛", "urgent": 2,
        "lay": ["右下腹疼", "右下肚子疼", "右侧小肚子疼", "阑尾那疼"],
    },
    {
        "id": "ruq_pain", "term": "右上腹痛", "urgent": 1,
        "lay": ["右上腹疼", "右侧肋下疼", "胆那边疼"],
    },
    {
        "id": "diffuse_abdominal_pain", "term": "全腹痛", "urgent": 2,
        "lay": ["满肚子都疼", "全肚子疼", "整个肚子疼", "肚子到处都疼"],
    },
    {
        "id": "vomiting", "term": "呕吐", "urgent": 1,
        "lay": ["吐了", "呕吐", "恶心想吐", "吐得厉害", "干呕"],
    },
    {
        "id": "diarrhea", "term": "腹泻", "urgent": 0,
        "lay": ["拉肚子", "腹泻", "拉稀", "跑肚"],
    },
    {
        "id": "melena", "term": "黑便", "urgent": 2,
        "lay": ["大便发黑", "黑色大便", "柏油样便", "拉黑屎", "黑便"],
        "note": "上消化道出血线索，别当「吃错了东西」",
    },
    {
        "id": "hematemesis", "term": "呕血", "urgent": 3,
        "lay": ["吐血", "呕血", "吐出一口血", "吐咖啡色东西"],
    },
    {
        "id": "jaundice", "term": "黄疸", "urgent": 1,
        "lay": ["眼睛发黄", "脸发黄", "皮肤黄", "尿像浓茶"],
    },
    {
        "id": "distension", "term": "腹胀", "urgent": 1,
        "lay": ["肚子胀", "胀气", "肚子鼓起来", "肚子胀得难受"],
    },
    {
        "id": "obstipation", "term": "停止排气排便", "urgent": 2,
        "lay": ["不放屁", "没放屁", "不排气", "没排气", "好几天没拉", "不排便", "没排便",
                "不排气不排便", "肠子像堵了"],
    },
    {
        "id": "flank_pain", "term": "腰部/侧腹疼痛", "urgent": 1,
        "lay": ["腰疼", "腰侧疼", "肾那个位置疼", "侧腰疼", "后腰疼"],
    },
    # ---------------- 泌尿生殖 ----------------
    {
        "id": "amenorrhea", "term": "停经", "urgent": 2,
        "lay": ["月经没来", "大姨妈没来", "停经", "月经推迟", "一个多月没来月经", "两个月没来"],
        "note": "育龄女性腹痛 + 停经 = 必须查 β-hCG，排除宫外孕",
    },
    {
        "id": "vaginal_bleeding", "term": "阴道流血", "urgent": 2,
        "lay": ["下面出血", "阴道出血", "阴道流血", "私处流血", "月经量大", "来血不止"],
    },
    {
        "id": "hematuria", "term": "血尿", "urgent": 1,
        "lay": ["尿里有血", "尿血", "小便是红的", "尿是红的"],
    },
    {
        "id": "testicular_pain", "term": "睾丸疼痛/肿胀", "urgent": 3,
        "lay": ["蛋蛋疼", "睾丸疼", "阴囊肿", "阴囊疼", "睾丸肿大"],
        "note": "睾丸扭转的黄金抢救窗仅 6 小时",
    },
    {
        "id": "anuria", "term": "少尿/无尿", "urgent": 2,
        "lay": ["没尿", "尿不出来", "一天没小便", "尿很少", "没怎么尿", "尿不多", "尿量少", "一天没尿"],
    },
    # ---------------- 全身 / 感染 ----------------
    {
        "id": "fever", "term": "发热", "urgent": 1,
        "lay": ["发烧", "发热", "烧起来了", "高烧", "体温高", "烧到三十九"],
    },
    {
        "id": "chills", "term": "寒战", "urgent": 1,
        "lay": ["打寒战", "冷得发抖", "浑身发抖", "畏寒", "打摆子"],
    },
    {
        "id": "rash", "term": "皮疹", "urgent": 1,
        "lay": ["起疹子", "身上起红点", "风疙瘩", "荨麻疹", "皮肤起包", "起红疹"],
    },
    {
        "id": "sore_throat", "term": "咽痛", "urgent": 1,
        "lay": ["嗓子疼", "喉咙痛", "咽口水都疼", "嗓子疼得说不出话"],
    },
    {
        "id": "drooling", "term": "流涎/吞咽困难", "urgent": 3,
        "lay": ["流口水", "咽不下去", "吞不进去", "口水往外流"],
        "note": "急性会厌炎/咽后脓肿：气道随时可能梗阻，禁止强行压舌查喉",
    },
    {
        "id": "fatigue", "term": "乏力", "urgent": 0,
        "lay": ["没劲", "浑身没力气", "乏力", "提不起劲"],
    },
    {
        "id": "weight_loss", "term": "体重下降", "urgent": 0,
        "lay": ["瘦了", "体重掉了", "掉秤"],
    },
    {
        "id": "thirst_polyuria", "term": "口干多尿", "urgent": 1,
        "lay": ["口干", "老想喝水", "尿特别多", "一直口渴"],
    },
    # ---------------- 创伤 / 其他 ----------------
    {
        "id": "laceration", "term": "开放性伤口", "urgent": 1,
        "lay": ["划伤", "割伤", "扎伤", "切到", "切伤", "割到", "划到", "划破", "破了个口子",
                "破口子", "伤口", "磕破了"],
    },
    {
        "id": "active_bleeding", "term": "活动性出血", "urgent": 3,
        "lay": ["血止不住", "流血不止", "出了好多血", "一直流血"],
    },
    {
        "id": "burn", "term": "烧伤/烫伤", "urgent": 2,
        "lay": ["烫伤", "烧伤", "被开水烫", "火烧到"],
    },
    {
        "id": "fracture", "term": "骨折/畸形", "urgent": 2,
        "lay": ["骨折", "骨头断了", "腿变形", "手变形", "肿得老高"],
    },
    {
        "id": "head_trauma", "term": "头部外伤", "urgent": 2,
        "lay": ["摔到头", "撞到头", "脑袋磕了", "头被打了"],
    },
    {
        "id": "back_pain", "term": "背痛", "urgent": 1,
        "lay": ["后背疼", "背痛", "肩胛骨中间疼", "后背像撕开"],
    },
    {
        "id": "leg_swelling", "term": "单侧下肢肿胀", "urgent": 2,
        "lay": ["一条腿肿", "小腿肿胀", "腿肿了", "小腿发胀", "腿肚子疼"],
        "note": "单侧下肢肿痛 + 呼吸困难 = 肺栓塞组合拳",
    },
    {
        "id": "numbness", "term": "肢体麻木", "urgent": 2,
        "lay": ["手麻", "腿麻", "半边麻", "没知觉"],
    },
    {
        "id": "self_harm", "term": "自伤/自杀风险", "urgent": 3,
        "lay": ["自杀", "不想活", "割腕", "跳楼", "吃了很多药", "轻生"],
    },
    {
        "id": "intoxication", "term": "中毒/药物过量", "urgent": 3,
        "lay": ["中毒", "喝了农药", "吃了安眠药", "吃错药", "煤气中毒", "一氧化碳"],
    },
    {
        "id": "labor", "term": "临产/宫缩", "urgent": 1,
        "lay": ["要生了", "肚子一阵一阵疼", "见红了", "破水了"],
    },
]

# ======================================================================
# 二、既往史 / 危险因素词典
# ======================================================================
HISTORY: list[dict[str, Any]] = [
    {"id": "hypertension", "term": "高血压", "risk": 2,
     "lay": ["高血压", "血压高", "吃降压药", "血压一直高"]},
    {"id": "diabetes", "term": "糖尿病", "risk": 2,
     "lay": ["糖尿病", "血糖高", "打胰岛素", "吃降糖药"]},
    {"id": "cad", "term": "冠心病", "risk": 3,
     "lay": ["冠心病", "心梗过", "放过支架", "搭过桥", "心脏不好"]},
    {"id": "af", "term": "心房颤动", "risk": 3,
     "lay": ["房颤", "心律不齐", "心跳不齐", "房扑"]},
    {"id": "cva", "term": "脑血管病史", "risk": 3,
     "lay": ["中风过", "脑梗过", "脑出血", "半身不遂病史"]},
    {"id": "copd", "term": "慢阻肺/肺气肿", "risk": 2,
     "lay": ["慢阻肺", "肺气肿", "老慢支", "肺不好"]},
    {"id": "asthma", "term": "哮喘", "risk": 2,
     "lay": ["哮喘", "气喘病"]},
    {"id": "renal", "term": "肾功能不全", "risk": 2,
     "lay": ["肾功能不好", "尿毒症", "肌酐高", "肾衰"]},
    {"id": "dialysis", "term": "透析", "risk": 3,
     "lay": ["透析", "血透", "腹透"]},
    {"id": "pregnancy", "term": "妊娠状态", "risk": 3,
     "lay": ["怀孕", "有孩子了", "孕早期", "孕晚期", "怀孕两个月"]},
    {"id": "recent_surgery", "term": "近期手术/创伤", "risk": 3,
     "lay": ["刚做完手术", "上周手术", "刚做完手术三天", "术后", "刚摔了"]},
    {"id": "immobility", "term": "长期卧床/制动", "risk": 3,
     "lay": ["卧床", "一直躺着", "坐长途飞机", "坐火车二十小时", "打石膏"]},
    {"id": "cancer", "term": "恶性肿瘤", "risk": 3,
     "lay": ["癌症", "肿瘤", "化疗", "放疗", "肺癌", "胃癌", "肠癌"]},
    {"id": "anticoagulant", "term": "抗凝/抗血小板治疗", "risk": 2,
     "lay": ["华法林", "利伐沙班", "阿司匹林", "氯吡格雷", "吃抗凝药", "抗凝"]},
    {"id": "immunosuppress", "term": "免疫抑制状态", "risk": 2,
     "lay": ["激素", "免疫抑制剂", "化疗后", "免疫力低"]},
    {"id": "marfan", "term": "马凡综合征/结缔组织病", "risk": 3,
     "lay": ["马凡", "马凡氏综合征", "结缔组织病"]},
    {"id": "dvt", "term": "深静脉血栓史", "risk": 3,
     "lay": ["下肢血栓", "深静脉血栓", "血栓过"]},
    {"id": "smoking", "term": "吸烟", "risk": 1,
     "lay": ["抽烟", "吸烟", "一天一包"]},
    {"id": "alcohol", "term": "酗酒", "risk": 1,
     "lay": ["喝酒", "酗酒", "天天喝"]},
    {"id": "family_sudden_death", "term": "家族猝死/早发心血管病史", "risk": 2,
     "lay": ["家里有人猝死", "父亲心梗", "家族史"]},
    {"id": "pe_history", "term": "肺栓塞史", "risk": 3,
     "lay": ["肺栓塞过", "肺栓塞"]},
]

# ======================================================================
# 三、性状修饰词（挂到症状上，直接影响红旗判断）
# ======================================================================
QUALIFIERS: list[dict[str, Any]] = [
    {"id": "tearing", "term": "撕裂样", "urgent": 3,
     "lay": ["撕裂样", "撕裂", "撕开", "撕扯", "刀割", "刀切", "刀劈", "刀扎", "像被刀割", "像撕开"]},
    {"id": "crushing", "term": "压榨样", "urgent": 3,
     "lay": ["压榨", "压着", "挤压", "石头压", "像被石头压", "紧缩", "勒住", "箍住", "沉重"]},
    {"id": "colicky", "term": "绞痛", "urgent": 1,
     "lay": ["绞痛", "一阵一阵", "绞着", "拧着", "抽着疼", "阵发", "发作性"]},
    {"id": "burning", "term": "烧灼样", "urgent": 1,
     "lay": ["烧灼", "烧心", "火辣", "火烧一样", "灼痛"]},
    {"id": "throbbing", "term": "搏动性", "urgent": 1,
     "lay": ["搏动", "一跳一跳", "跳着疼"]},
    {"id": "boring", "term": "钻顶样", "urgent": 1,
     "lay": ["钻顶", "钻着疼", "往里钻"]},
    {"id": "stabbing", "term": "针刺样", "urgent": 1,
     "lay": ["针扎", "针刺", "扎着疼"]},
    {"id": "dull", "term": "钝痛", "urgent": 0,
     "lay": ["钝痛", "闷闷地疼", "说不上来的疼", "隐隐"]},
    {"id": "aching", "term": "胀痛", "urgent": 0,
     "lay": ["胀痛", "发胀", "胀得慌"]},
    {"id": "pleuritic", "term": "呼吸相关性", "urgent": 2,
     "lay": ["深呼吸就疼", "喘气时疼", "咳嗽时胸口疼", "翻身就疼"]},
]

# 伴随症状 / 放射
RADIATION_PATTERNS = [
    (r"(?:放射|串|牵|连|转移)(?:到|至)?\s*(后背|背部|左肩|右肩|肩膀|左臂|左手|下颌|下巴|牙齿|上腹|颈部|嗓子)", None),
]

ASSOCIATED: list[dict[str, Any]] = [
    {"id": "diaphoresis", "term": "大汗", "urgent": 2,
     "lay": ["大汗", "出汗", "冷汗", "浑身汗", "冒汗", "汗湿透"]},
    {"id": "nausea", "term": "恶心", "urgent": 1, "lay": ["恶心", "想吐", "反胃"]},
    {"id": "near_syncope", "term": "濒死感", "urgent": 3,
     "lay": ["像要死了", "快不行了", "濒死感", "感觉自己要死了"]},
    {"id": "numbness_assoc", "term": "麻木", "urgent": 2, "lay": ["发麻", "麻木", "没知觉"]},
]

# 严重程度口语
SEVERITY_HIGH = ["剧烈", "受不了", "难以忍受", "要命", "疼得打滚", "疼得直冒", "最疼", "疼得受不了",
                 "厉害得", "十级", "10分", "非常疼"]
SEVERITY_MID = ["明显", "比较疼", "挺疼", "有点厉害", "闷得慌"]
SEVERITY_LOW = ["轻微", "有点疼", "一点点", "不太疼", "隐隐作痛", "还行"]

# 时间
DURATION_RE = re.compile(
    r"(突发|突然|骤然|持续|间断|反复|进行性|阵发|已经)?\s*"
    r"(\d+|[一二三四五六七八九十两半]+)\s*个?\s*(分钟|分|小时|钟头|天|日|周|星期|个月|月|年)"
)

# 否定词：必须紧贴在术语前才算"已排除"，避免"不缓解的胸痛"被误判为无胸痛
NEGATION_RE = re.compile(r"(?:没有|没有明显|无明显|无|未见|未出现|未诉|否认|不伴|并无|没)\s*(?:明显|任何|头晕|腹痛)?\s*$")

# ======================================================================
# 三点五、部位+修饰+痛 的模糊句式
#   患者很少说标准词。像"胸口像被刀割一样疼"，中间夹了一串修饰，
#   纯词典匹配会漏掉，所以补一层"部位…疼痛词"的正则兜底。
# ======================================================================
SITE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("chest_pain", re.compile(r"(?:胸口|心口|前胸|胸前|胸骨后|左胸|右胸|胸部)[^，。；、\n]{0,10}?(?:疼|痛|闷|压|堵|不舒服|难受)")),
    ("epigastric_pain", re.compile(r"(?:胃|心窝|心口窝|剑突下|上腹)[^，。；、\n]{0,6}?(?:疼|痛|胀|烧)")),
    ("rlq_pain", re.compile(r"(?:右下腹|右下肚子|右侧小肚子)[^，。；、\n]{0,6}?(?:疼|痛)")),
    ("abdominal_pain", re.compile(r"(?:肚子|腹部|小肚子|肚脐)[^，。；、\n]{0,8}?(?:疼|痛|胀|绞|不舒服)")),
    ("back_pain", re.compile(r"(?:后背|背部|肩胛|腰背)[^，。；、\n]{0,8}?(?:疼|痛|撕|酸胀)")),
    ("headache", re.compile(r"(?:头|脑袋)[^，。；、\n]{0,10}?(?:疼|痛|胀|炸|晕)")),
    ("leg_swelling", re.compile(r"(?:小腿|大腿|一条腿|腿)[^，。；、\n]{0,6}?(?:肿|胀|发紧)")),
    ("dyspnea", re.compile(r"(?:喘|气)[^，。；、\n]{0,4}?(?:不上|不过来|急|短)")),
    ("melena", re.compile(r"(?:大便|拉屎|屎)[^，。；、\n]{0,4}?(?:黑|发黑)")),
    ("vaginal_bleeding", re.compile(r"(?:下面|下身|阴道|私处|子宫)[^，。；、\n]{0,4}?(?:出血|流血|见红)")),
    ("anuria", re.compile(r"(?:尿)[^，。；、\n]{0,3}?(?:很少|不多|解不出)")),
    ("fever", re.compile(r"(?:体温|烧)[^，。；、\n]{0,4}?(?:39|40|3[89]\.\d)")),
]

_SYMPTOM_BY_ID = {s["id"]: s for s in SYMPTOMS}

# ======================================================================
# 四、生命体征解析
# ======================================================================
VITAL_PATTERNS = {
    "bp": re.compile(r"(?:血压|BP|Bp|bp)\s*[:：]?\s*(\d{2,3})\s*[/／]\s*(\d{2,3})"),
    "p": re.compile(r"(?:心率|脉搏|HR|Hr|心率数|脉率)\s*[:：]?\s*(\d{2,3})"),
    "r": re.compile(r"(?:呼吸|RR|Rr|呼吸频率)\s*[:：]?\s*(\d{1,2})"),
    "t": re.compile(r"(?:体温|T|t)\s*[:：]?\s*(3[4-9](?:\.\d)?|4[0-2](?:\.\d)?)\s*(?:℃|度|C)?"),
    "spo2": re.compile(r"(?:血氧|SpO2|SPO2|spo2|氧饱和度|指脉氧)\s*[:：]?\s*(\d{2,3})\s*%?"),
    "nrs": re.compile(r"(?:疼痛|痛感|NRS|疼痛评分)\s*[:：]?\s*(\d{1,2})\s*(?:分|/10)?"),
}


def clean(text: str) -> str:
    """全角数字/符号归一化，去掉多余空白。"""
    if not text:
        return ""
    table = str.maketrans("０１２３４５６７８９．，：；／％", "0123456789.,:;/%")
    return text.translate(table).replace("\u3000", " ").strip()


def parse_vitals(text: str) -> dict[str, Any]:
    """从自由文本中抠生命体征。返回 {'bp_sys','bp_dia','p','r','t','spo2','nrs'}。"""
    text = clean(text)
    out: dict[str, Any] = {}
    m = VITAL_PATTERNS["bp"].search(text)
    if m:
        out["bp_sys"] = int(m.group(1))
        out["bp_dia"] = int(m.group(2))
    for key in ("p", "r", "spo2", "nrs"):
        m = VITAL_PATTERNS[key].search(text)
        if m:
            out[key] = int(m.group(1))
    m = VITAL_PATTERNS["t"].search(text)
    if m:
        out["t"] = float(m.group(1))
    return out


def vital_level(vitals: dict[str, Any]) -> list[dict[str, Any]]:
    """生命体征的危险程度分级：red 立刻处置 / yellow 需复测。"""
    hits: list[dict[str, Any]] = []
    sbp = vitals.get("bp_sys")
    dbp = vitals.get("bp_dia")
    p = vitals.get("p")
    r = vitals.get("r")
    t = vitals.get("t")
    spo2 = vitals.get("spo2")

    if sbp is not None:
        if sbp < 80 or (sbp < 90 and p and p > 110):
            hits.append({"key": "sbp", "level": "red", "text": f"收缩压 {sbp} mmHg，休克边缘"})
        elif sbp < 90:
            hits.append({"key": "sbp", "level": "yellow", "text": f"收缩压 {sbp} mmHg，偏低"})
        elif sbp >= 180 or (dbp and dbp >= 110):
            hits.append({"key": "sbp", "level": "yellow", "text": f"血压 {sbp}/{dbp} mmHg，显著升高"})
    if p is not None:
        if p >= 130 or p < 40:
            hits.append({"key": "p", "level": "red", "text": f"心率 {p} 次/分，血流动力学不稳定"})
        elif p > 100 or p < 50:
            hits.append({"key": "p", "level": "yellow", "text": f"心率 {p} 次/分，超出安全区间"})
    if r is not None:
        if r >= 30 or r <= 8:
            hits.append({"key": "r", "level": "red", "text": f"呼吸 {r} 次/分，呼吸衰竭风险"})
        elif r > 20:
            hits.append({"key": "r", "level": "yellow", "text": f"呼吸 {r} 次/分，呼吸急促"})
    if spo2 is not None:
        if spo2 < 90:
            hits.append({"key": "spo2", "level": "red", "text": f"SpO₂ {spo2}%，低氧血症"})
        elif spo2 < 94:
            hits.append({"key": "spo2", "level": "yellow", "text": f"SpO₂ {spo2}%，氧合偏低"})
    if t is not None:
        if t >= 39.5:
            hits.append({"key": "t", "level": "yellow", "text": f"体温 {t}℃，高热"})
        elif t < 35.5:
            hits.append({"key": "t", "level": "yellow", "text": f"体温 {t}℃，低体温"})
    return hits


# ======================================================================
# 五、化验单解析
# ======================================================================
#   low/high  参考区间；crit_low/crit_high 危急值；unit 展示单位
LAB_ITEMS: list[dict[str, Any]] = [
    {"id": "ctni", "name": "肌钙蛋白I", "unit": "ng/mL", "low": 0.0, "high": 0.04, "crit_high": 0.5,
     "alias": ["肌钙蛋白i", "肌钙蛋白", "ctni", "tni", "hs-tni"]},
    {"id": "ctnt", "name": "肌钙蛋白T", "unit": "ng/mL", "low": 0.0, "high": 0.014, "crit_high": 0.1,
     "alias": ["肌钙蛋白t", "ctnt", "tnt", "hs-tnt"]},
    {"id": "bnp", "name": "BNP/NT-proBNP", "unit": "pg/mL", "low": 0.0, "high": 300, "crit_high": 900,
     "alias": ["bnp", "nt-probnp", "probnp", "脑钠肽", "b型钠尿肽"]},
    {"id": "ddimer", "name": "D-二聚体", "unit": "mg/L FEU", "low": 0.0, "high": 0.5, "crit_high": 5.0,
     "alias": ["d-二聚体", "d二聚体", "ddimer", "d-dimer"]},
    {"id": "lactate", "name": "乳酸", "unit": "mmol/L", "low": 0.5, "high": 2.0, "crit_high": 4.0,
     "alias": ["乳酸", "lac", "血乳酸"]},
    {"id": "k", "name": "血钾", "unit": "mmol/L", "low": 3.5, "high": 5.3, "crit_low": 3.0, "crit_high": 6.0,
     "alias": ["血钾", "钾离子", "k+", "血清钾"]},
    {"id": "na", "name": "血钠", "unit": "mmol/L", "low": 135, "high": 145, "crit_low": 125, "crit_high": 155,
     "alias": ["血钠", "钠离子", "na+", "血清钠"]},
    {"id": "glu", "name": "血糖", "unit": "mmol/L", "low": 3.9, "high": 7.0, "crit_low": 3.0, "crit_high": 20.0,
     "alias": ["血糖", "葡萄糖", "glu", "空腹血糖", "随机血糖"]},
    {"id": "hb", "name": "血红蛋白", "unit": "g/L", "low": 115, "high": 175, "crit_low": 70,
     "alias": ["血红蛋白", "hb", "hgb", "血色素"]},
    {"id": "plt", "name": "血小板", "unit": "×10⁹/L", "low": 100, "high": 350, "crit_low": 20,
     "alias": ["血小板", "plt", "血小板计数"]},
    {"id": "wbc", "name": "白细胞", "unit": "×10⁹/L", "low": 3.5, "high": 9.5, "crit_high": 20,
     "alias": ["白细胞", "wbc", "白细胞计数"]},
    {"id": "crp", "name": "C反应蛋白", "unit": "mg/L", "low": 0.0, "high": 10, "crit_high": 100,
     "alias": ["c反应蛋白", "crp", "crp定量"]},
    {"id": "cr", "name": "肌酐", "unit": "μmol/L", "low": 44, "high": 106, "crit_high": 450,
     "alias": ["肌酐", "cr", "scr", "血清肌酐"]},
    {"id": "amy", "name": "淀粉酶", "unit": "U/L", "low": 0, "high": 110, "crit_high": 500,
     "alias": ["淀粉酶", "amy", "血淀粉酶"]},
    {"id": "lip", "name": "脂肪酶", "unit": "U/L", "low": 0, "high": 60, "crit_high": 300,
     "alias": ["脂肪酶", "lip", "血脂肪酶"]},
    {"id": "hcg", "name": "β-hCG", "unit": "mIU/mL", "low": 0, "high": 5, "crit_high": 1000,
     "alias": ["hcg", "β-hcg", "b-hcg", "人绒毛膜促性腺激素", "尿妊娠试验", "妊娠试验"]},
    {"id": "inr", "name": "INR", "unit": "", "low": 0.8, "high": 1.2, "crit_high": 3.5, "alias": ["inr", "凝血酶原时间比值"]},
    {"id": "ph", "name": "pH", "unit": "", "low": 7.35, "high": 7.45, "crit_low": 7.20, "crit_high": 7.55,
     "alias": ["ph", "酸碱度"]},
    {"id": "pao2", "name": "PaO₂", "unit": "mmHg", "low": 80, "high": 100, "crit_low": 60, "alias": ["pao2", "氧分压"]},
    {"id": "paco2", "name": "PaCO₂", "unit": "mmHg", "low": 35, "high": 45, "crit_high": 60, "alias": ["paco2", "二氧化碳分压"]},
    {"id": "bil", "name": "总胆红素", "unit": "μmol/L", "low": 3.4, "high": 20.5, "crit_high": 100,
     "alias": ["总胆红素", "胆红素", "tbil", "bil"]},
]

_LAB_LOOKUP = [(a, item) for item in LAB_ITEMS for a in item["alias"]]
_LAB_LOOKUP.sort(key=lambda x: len(x[0]), reverse=True)
_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _lab_flag(item: dict[str, Any], value: float, explicit: str) -> str:
    if value >= item.get("crit_high", 1e9) or value <= item.get("crit_low", -1e9):
        return "危急"
    if item.get("crit_low") is not None and value <= item["crit_low"]:
        return "危急"
    if "↑" in explicit or "高" in explicit or "阳性" in explicit:
        return "偏高"
    if "↓" in explicit or "低" in explicit:
        return "偏低"
    if "阴" in explicit and item["id"] == "hcg":
        return "正常"
    if value > item["high"]:
        return "偏高"
    if value < item["low"]:
        return "偏低"
    return "正常"


def parse_labs(text: str) -> list[dict[str, Any]]:
    """把旧化验单（粘贴文本）解析成结构化项目。支持一行一项或整段文字。"""
    text = clean(text)
    if not text.strip():
        return []
    lines = [ln for ln in re.split(r"[\n;；]+", text) if ln.strip()]
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in lines:
        low = line.lower()
        item = None
        alias_hit = ""
        for alias, cand in _LAB_LOOKUP:
            if alias.lower() in low:
                item, alias_hit = cand, alias
                break
        if not item or item["id"] in seen:
            continue
        # 跳过项目名里自带的数字（如 NT-proBNP 里的 2）
        tail = line[low.find(alias_hit.lower()) + len(alias_hit):]
        m = _NUM_RE.search(tail)
        if not m:
            continue
        try:
            value = float(m.group())
        except ValueError:
            continue
        unit = ""
        um = re.search(r"(ng/mL|pg/mL|mg/L|mmol/L|μmol/L|umol/L|g/L|U/L|mIU/mL|×10⁹/L|%|mmHg)", line, re.I)
        if um:
            unit = um.group(1)
        flag = _lab_flag(item, value, line)
        seen.add(item["id"])
        results.append({
            "id": item["id"], "name": item["name"], "value": value,
            "unit": unit or item["unit"], "flag": flag,
            "ref": f"{item['low']}–{item['high']}",
            "critical": flag == "危急", "abnormal": flag != "正常",
            "raw": line.strip()[:80],
        })
    return results


# ======================================================================
# 六、主入口：口语 → 结构化发现
# ======================================================================
def _covered(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(not (end <= s or start >= e) for s, e in spans)


def _negated(clause: str, start: int) -> bool:
    window = clause[max(0, start - 6):start]
    return NEGATION_RE.search(window) is not None


def _clause_quals(clause: str) -> list[dict[str, Any]]:
    return [{"id": q["id"], "term": q["term"], "urgent": q["urgent"]}
            for q in QUALIFIERS if any(qp in clause for qp in q["lay"])]


def _clause_assoc(clause: str) -> list[dict[str, Any]]:
    return [{"id": a["id"], "term": a["term"]}
            for a in ASSOCIATED if any(ap in clause for ap in a["lay"])]


def _clause_duration(clause: str) -> str | None:
    m = DURATION_RE.search(clause)
    if not m:
        return None
    return f"{m.group(1) or ''}{m.group(2)}{m.group(3)}"


def _clause_severity(clause: str) -> int | None:
    if any(k in clause for k in SEVERITY_HIGH):
        return 7
    if any(k in clause for k in SEVERITY_MID):
        return 5
    if any(k in clause for k in SEVERITY_LOW):
        return 3
    return None


def _make_finding(entry: dict[str, Any], clause: str, phrase: str) -> dict[str, Any]:
    quals = _clause_quals(clause)
    assoc = _clause_assoc(clause)
    # "撕裂样"+"胸痛" 直接拼；"绞痛"→"绞痛性腹痛"；"呼吸相关性"→"呼吸相关性胸痛"
    prefix = ""
    for q in quals:
        prefix += q["term"] if q["term"].endswith("样") else q["term"] + "性"
    std = prefix + entry["term"]
    return {
        "id": entry["id"], "term": entry["term"], "standard": std,
        "phrase": phrase, "clause": clause[:60],
        "qualifiers": quals, "associated": assoc,
        "duration": _clause_duration(clause), "severity": _clause_severity(clause),
        "urgent": max([entry["urgent"]] + [q["urgent"] for q in quals]),
        "note": entry.get("note", ""),
    }


def normalize_text(text: str) -> dict[str, Any]:
    """口语文本 → 结构化发现。

    返回：
      findings   : 命中的症状/伴随症状（含否定项）
      translation: [{"lay": 原话片段, "term": 标准术语}]，用于界面上的"翻译对照"
      history    : 命中的既往史
      vitals     : 文本里的生命体征
      radiation  : 放射部位
      unmatched  : 无法识别的片段（提示护士补问）
    """
    text = clean(text)
    findings: list[dict[str, Any]] = []
    translation: list[dict[str, str]] = []
    history: list[dict[str, Any]] = []
    radiation: list[str] = []
    if not text:
        return {"findings": findings, "translation": translation, "history": history,
                "vitals": {}, "radiation": radiation, "unmatched": []}

    clauses = [c for c in re.split(r"[，,。.;；!！?？\n\r、]+", text) if c.strip()]

    for clause in clauses:
        spans: list[tuple[int, int]] = []
        local_ids: set[str] = set()
        # --- 既往史（先匹配长词，避免"高血压"吃掉别的） ---
        for entry in sorted(HISTORY, key=lambda e: max(len(x) for x in e["lay"]), reverse=True):
            for phrase in sorted(entry["lay"], key=len, reverse=True):
                idx = clause.find(phrase)
                if idx < 0 or _covered(spans, idx, idx + len(phrase)):
                    continue
                spans.append((idx, idx + len(phrase)))
                if not _negated(clause, idx):
                    history.append({"id": entry["id"], "term": entry["term"], "phrase": phrase,
                                    "risk": entry["risk"]})
                    translation.append({"lay": phrase, "term": entry["term"]})
                break

        # --- 症状 ---
        for entry in sorted(SYMPTOMS, key=lambda e: max(len(x) for x in e["lay"]), reverse=True):
            for phrase in sorted(entry["lay"], key=len, reverse=True):
                idx = clause.find(phrase)
                if idx < 0 or _covered(spans, idx, idx + len(phrase)):
                    continue
                spans.append((idx, idx + len(phrase)))
                if _negated(clause, idx):
                    break
                # 同一句里的性状修饰 / 伴随症状 / 时间 / 程度
                f = _make_finding(entry, clause, phrase)
                findings.append(f)
                local_ids.add(entry["id"])
                translation.append({
                    "lay": clause[:28] + ("…" if len(clause) > 28 else ""),
                    "term": f["standard"],
                })
                break

        # --- 兜底：部位 + 修饰 + 疼痛词 的模糊说法 ---
        for sid, pattern in SITE_PATTERNS:
            if sid in local_ids:
                continue
            m = pattern.search(clause)
            if not m or _covered(spans, m.start(), m.end()) or _negated(clause, m.start()):
                continue
            spans.append((m.start(), m.end()))
            entry = _SYMPTOM_BY_ID[sid]
            f = _make_finding(entry, clause, m.group())
            findings.append(f)
            local_ids.add(sid)
            translation.append({"lay": m.group(), "term": f["standard"]})

        # --- 放射 ---
        for pattern, _ in RADIATION_PATTERNS:
            for m in re.finditer(pattern, clause):
                site = m.group(1)
                if site not in radiation:
                    radiation.append(site)

    # 全局性状 / 伴随症状：跨句也要能抓到（"胸口疼。出了一身大汗。"）
    text_quals = _clause_quals(text)
    text_assoc = _clause_assoc(text)

    # 时间/程度兜底：主诉和"疼了多久"常常不在同一句
    gdur = _clause_duration(text)
    gsev = _clause_severity(text)
    for f in findings:
        if not f["duration"] and gdur:
            f["duration"] = gdur
        if not f["severity"] and gsev:
            f["severity"] = gsev

    # 去重（同 id 只留最先出现的）
    uniq: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for f in findings:
        if f["id"] in seen_ids:
            continue
        seen_ids.add(f["id"])
        uniq.append(f)

    seen_h: set[str] = set()
    hist: list[dict[str, Any]] = []
    for h in history:
        if h["id"] in seen_h:
            continue
        seen_h.add(h["id"])
        hist.append(h)

    return {
        "findings": uniq,
        "translation": translation,
        "history": hist,
        "vitals": parse_vitals(text),
        "radiation": radiation,
        "qualifiers": text_quals,
        "associated": text_assoc,
        "unmatched": [],
    }


def build_context(payload: dict[str, Any]) -> dict[str, Any]:
    """把工作台提交的表单 + 口语文本，合并成一个供所有 Agent 共用的上下文。"""
    chief = clean(str(payload.get("chief") or ""))
    nurse_note = clean(str(payload.get("nurse_note") or ""))
    history_text = clean(str(payload.get("history_text") or ""))
    lab_text = str(payload.get("lab_text") or "")

    analyzed = normalize_text("。".join(x for x in (chief, nurse_note, history_text) if x))
    vitals = parse_vitals(" ".join(x for x in (chief, nurse_note) if x))

    # 表单里显式填写的生命体征优先于文本解析
    for key in ("bp_sys", "bp_dia", "p", "r", "t", "spo2", "nrs"):
        val = payload.get(key)
        if val not in (None, ""):
            try:
                vitals[key] = float(val) if key == "t" else int(val)
            except (TypeError, ValueError):
                pass
    if str(vitals.get("t", "")).endswith(".0"):
        vitals["t"] = float(vitals["t"])

    terms = {f["id"] for f in analyzed["findings"]}
    qualifiers = {q["id"] for q in analyzed.get("qualifiers", [])}
    qualifiers |= {q["id"] for f in analyzed["findings"] for q in f["qualifiers"]}
    assoc = {a["id"] for a in analyzed.get("associated", [])}
    assoc |= {a["id"] for f in analyzed["findings"] for a in f["associated"]}
    history = {h["id"] for h in analyzed["history"]}

    nrs = vitals.get("nrs")
    if nrs is None:
        sevs = [f["severity"] for f in analyzed["findings"] if f["severity"]]
        nrs = max(sevs) if sevs else None

    age = payload.get("age")
    try:
        age = int(age) if age not in (None, "") else None
    except (TypeError, ValueError):
        age = None
    sex = payload.get("sex") or "未知"

    labs = parse_labs(lab_text)
    lab_ids = {l["id"]: l for l in labs}

    return {
        "name": (payload.get("name") or "无名氏").strip()[:20],
        "age": age,
        "sex": sex,
        "chief": chief,
        "nurse_note": nurse_note,
        "terms": terms,
        "qualifiers": qualifiers,
        "associated": assoc,
        "history": history,
        "findings": analyzed["findings"],
        "translation": analyzed["translation"],
        "radiation": analyzed["radiation"],
        "vitals": vitals,
        "vital_flags": vital_level(vitals),
        "labs": labs,
        "lab_ids": lab_ids,
        "nrs": nrs,
        "text": "。".join(x for x in (chief, nurse_note, history_text) if x),
        "history_text": history_text,
        "allergy": clean(str(payload.get("allergy") or "")),
        "has_hcg": "hcg" in lab_ids,
    }
