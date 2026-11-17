"""Agent ②：红旗风险预警 —— 急诊老专家，专门排雷。

核心信条只有一条：**漏诊会死的病，宁可错杀一千，不可放过一个。**
所以这里的规则全部按"病"写，不按"症状"写——先想"这个症状组合能死人的病是什么"，
再倒过来检查患者有没有这些线索。

每一条规则都给出四样东西：
  1. 为什么致命（fatal）与时间窗（window）——用来跟家属沟通；
  2. 命中证据（hits）——让医生知道"不是机器瞎猜"；
  3. 还缺什么（missing/ask）——这是老专家的价值：不是报个警就走人，而是告诉你下一步问什么；
  4. 下一步动作（action）——给抢救室的具体指令。

注：本模块的医学逻辑按急诊通行诊疗路径整理，仅用于流程演示，不用于真实诊疗。
"""
from __future__ import annotations

from typing import Any, Callable

from . import terminology as T


# ---------------------------------------------------------------- 取数助手
def _t(ctx: dict[str, Any], *ids: str) -> bool:
    return any(i in ctx["terms"] for i in ids)


def _q(ctx: dict[str, Any], *ids: str) -> bool:
    return any(i in ctx["qualifiers"] for i in ids)


def _a(ctx: dict[str, Any], *ids: str) -> bool:
    return any(i in ctx["associated"] for i in ids)


def _h(ctx: dict[str, Any], *ids: str) -> bool:
    return any(i in ctx["history"] for i in ids)


def _v(ctx: dict[str, Any], key: str) -> Any:
    return ctx["vitals"].get(key)


def _lab(ctx: dict[str, Any], lid: str) -> float | None:
    item = ctx["lab_ids"].get(lid)
    return item["value"] if item else None


def _lab_high(ctx: dict[str, Any], lid: str, threshold: float) -> bool:
    v = _lab(ctx, lid)
    return v is not None and v >= threshold


def _age(ctx: dict[str, Any]) -> int | None:
    return ctx.get("age")


def _female(ctx: dict[str, Any]) -> bool:
    return ctx.get("sex") == "女"


def _male(ctx: dict[str, Any]) -> bool:
    return ctx.get("sex") == "男"


# ---------------------------------------------------------------- 规则表
RULES: list[dict[str, Any]] = [
    {
        "id": "acs", "name": "急性冠脉综合征（心梗）", "tier": 0, "weight": 96,
        "dept": "胸痛中心 / 抢救室",
        "fatal": "左主干或前降支近端闭塞可在数分钟内发生室颤、心源性休克",
        "window": "再灌注时间窗：进门到球囊扩张 ≤ 90 分钟",
        "test": lambda c: _acs(c),
        "ask": ["胸痛是否持续超过 20 分钟不缓解？", "有没有向左侧手臂、下颌放射？",
                "有无大汗、恶心、濒死感？", "既往是否放过支架、做过搭桥？"],
        "action": "10 分钟内完成 18 导联心电图，抽心肌酶（超敏肌钙蛋白），开通静脉通路，心电监护，启动胸痛中心流程",
    },
    {
        "id": "aortic_dissection", "name": "主动脉夹层", "tier": 0, "weight": 99,
        "dept": "抢救室 / 血管外科",
        "fatal": "一旦破裂，数分钟内失血性休克死亡；发病 48 小时内每小时死亡率约 1%",
        "window": "确诊到手术的时间窗以小时计",
        "test": lambda c: _dissection(c),
        "ask": ["疼痛是不是一开始就是最剧烈的撕裂样/刀割样？", "疼痛有没有从胸口往背后、腰部移动？",
                "双上臂血压差多少？", "有没有马凡综合征或高血压病史？"],
        "action": "立即双侧上臂血压 + 床旁心脏超声，控制心率血压（目标收缩压 100–120 mmHg），急查主动脉 CTA，血管外科急会诊",
    },
    {
        "id": "pulmonary_embolism", "name": "肺栓塞", "tier": 0, "weight": 94,
        "dept": "抢救室 / 呼吸科",
        "fatal": "大面积栓塞可致急性右心衰、梗阻性休克，猝死风险高",
        "window": "高危患者溶栓窗以小时计",
        "test": lambda c: _pe(c),
        "ask": ["近期有没有长途乘车、卧床、手术或骨折制动？", "有没有单侧小腿肿痛？",
                "有没有肿瘤、口服避孕药、既往血栓史？"],
        "action": "查 D-二聚体、动脉血气、心电图（S1Q3T3），评估右心负荷，高危者直接床旁超声 / CTPA",
    },
    {
        "id": "stroke", "name": "急性脑卒中", "tier": 0, "weight": 97,
        "dept": "卒中中心",
        "fatal": "大血管闭塞每分钟死亡约 190 万个神经元，延误即永久残疾或死亡",
        "window": "静脉溶栓 4.5 小时，取栓 6–24 小时（越早越好）",
        "test": lambda c: _stroke(c),
        "ask": ["最后一个完全正常的时间点是什么时候？", "症状是突然出现还是逐渐加重？",
                "有没有房颤、高血压、糖尿病？", "有没有 recent 头部外伤或近期手术（溶栓禁忌）？"],
        "action": "启动卒中绿色通道：急查血糖、头颅 CT 排除出血，NIHSS 评分，通知神经内科评估溶栓/取栓",
    },
    {
        "id": "sah", "name": "蛛网膜下腔出血", "tier": 0, "weight": 95,
        "dept": "神经外科",
        "fatal": "再出血死亡率可达 50%–80%，且可在数小时内发生",
        "window": "首诊 6 小时内 CT 阴性需腰穿",
        "test": lambda c: _sah(c),
        "ask": ["头痛是不是「一辈子最痛」、几秒钟内到达顶峰？", "有没有呕吐、脖子发硬、怕光？",
                "有没有短暂意识丧失？", "有没有动脉瘤家族史？"],
        "action": "急查头颅 CT（平扫），必要时腰穿查红细胞/黄变，绝对卧床、镇痛、控制血压，神经外科会诊",
    },
    {
        "id": "ectopic", "name": "异位妊娠破裂", "tier": 0, "weight": 93,
        "dept": "妇产科 / 抢救室",
        "fatal": "腹腔内大出血可在数小时内致失血性休克死亡，是育龄女性腹痛第一大死因",
        "window": "确诊后需急诊手术，休克者边复苏边手术",
        "test": lambda c: _ectopic(c),
        "ask": ["末次月经什么时候？月经是否推迟？", "有没有阴道流血、肛门坠胀感？",
                "有没有晕倒或眼前发黑？", "是否做过 β-hCG 或妇科超声？"],
        "action": "立即查血 β-hCG + 经阴道超声，开放两条静脉通路，备血，妇产科急会诊；血压不稳直接进抢救室",
    },
    {
        "id": "sepsis", "name": "脓毒症 / 感染性休克", "tier": 0, "weight": 92,
        "dept": "抢救室",
        "fatal": "感染性休克病死率约 30%–50%，每小时延迟抗生素都在增加死亡",
        "window": "1 小时集束化治疗（血培养、乳酸、广谱抗生素、液体复苏）",
        "test": lambda c: _sepsis(c),
        "ask": ["发热几天了？最高多少度？", "有没有尿路、胆道、肺部感染灶？",
                "有没有意识变差、尿量减少？", "有没有免疫抑制、糖尿病、近期住院史？"],
        "action": "测乳酸、血培养（抗生素前）、血气，30 ml/kg 晶体液复苏，1 小时内广谱抗生素，寻找感染灶",
    },
    {
        "id": "anaphylaxis", "name": "过敏性休克", "tier": 0, "weight": 98,
        "dept": "抢救室",
        "fatal": "喉头水肿 + 循环崩溃可在数分钟内致死",
        "window": "肾上腺素越早越好，分钟级",
        "test": lambda c: _anaphylaxis(c),
        "ask": ["发病前有没有接触药物、食物、蜂蜇、造影剂？", "有没有声音嘶哑、喉咙发紧、喘鸣？",
                "有没有全身风团、瘙痒？"],
        "action": "立即肌注肾上腺素（大腿外侧），高流量吸氧，开放静脉通路快速补液，抗组胺药 + 糖皮质激素，准备气道管理",
    },
    {
        "id": "gi_bleed", "name": "上消化道大出血", "tier": 0, "weight": 90,
        "dept": "消化科 / 内镜中心",
        "fatal": "短时间大量失血致失血性休克；呕血误吸可致窒息",
        "window": "血流动力学稳定后 24 小时内急诊内镜",
        "test": lambda c: _gi_bleed(c),
        "ask": ["吐的是鲜血还是咖啡色？量有多少？", "有没有黑便、血便？",
                "有没有吃抗凝药/阿司匹林？", "有没有肝硬化、溃疡病史？"],
        "action": "开放大号静脉通路、快速补液备血，查血常规+凝血+肝肾功，抑酸，禁食，联系内镜与介入",
    },
    {
        "id": "airway", "name": "急性气道梗阻 / 急性会厌炎", "tier": 0, "weight": 98,
        "dept": "抢救室 / 耳鼻喉科",
        "fatal": "喉头水肿进展为完全梗阻只需数分钟，属「来不及抢救」类型",
        "window": "分钟级",
        "test": lambda c: _airway(c),
        "ask": ["有没有流口水、吞咽困难、说话含糊？", "有没有喘鸣、吸气困难？",
                "咽痛多久了，是否快速加重？"],
        "action": "禁止强行压舌查看咽喉（可诱发窒息），保持坐位，高流量吸氧，床旁备好气管插管/环甲膜穿刺，耳鼻喉急会诊",
    },
    {
        "id": "meningitis", "name": "颅内感染（脑膜炎）", "tier": 0, "weight": 89,
        "dept": "神经内科 / 感染科",
        "fatal": "暴发型流脑可在 24 小时内进展为休克、DIC",
        "window": "1 小时内经验性抗生素，不因腰穿延迟用药",
        "test": lambda c: _meningitis(c),
        "ask": ["发热多少天？头痛是否剧烈？", "有没有喷射性呕吐、怕光、脖子发硬？",
                "有没有皮肤瘀点瘀斑？", "有没有意识改变？"],
        "action": "识别脑膜刺激征与皮肤瘀点，尽早血培养 + 经验性抗生素，必要时腰穿（先排除颅内高压）",
    },
    {
        "id": "hypoglycemia", "name": "低血糖 / 代谢性脑病", "tier": 0, "weight": 88,
        "dept": "抢救室",
        "fatal": "长时间低血糖造成不可逆脑损伤；也可伪装成「醉酒」「中风」",
        "window": "分钟级补糖",
        "test": lambda c: _hypoglycemia(c),
        "ask": ["有没有糖尿病、打胰岛素或吃降糖药？", "最后进食是什么时候？",
                "有没有出汗、手抖、心悸？"],
        "action": "床旁血糖（所有意识改变患者必查），低血糖立即静脉推注葡萄糖，复测并查找病因",
    },
    {
        "id": "arrhythmia", "name": "恶性心律失常 / 心源性晕厥", "tier": 1, "weight": 86,
        "dept": "急诊监护",
        "fatal": "室速、三度房室传导阻滞可随时恶化为室颤、心脏骤停",
        "window": "持续心电监护，分钟级",
        "test": lambda c: _arrhythmia(c),
        "ask": ["晕倒前有没有心慌、眼前发黑？", "有没有心脏病、心衰、房颤史？",
                "家族里有没有人年轻时猝死？", "晕倒时有没有抽搐、咬舌？"],
        "action": "立即 12 导联心电图 + 持续心电监护，查电解质（钾、镁）、心肌酶，必要时心内科会诊",
    },
    {
        "id": "mesenteric", "name": "肠系膜缺血 / 绞窄性肠梗阻", "tier": 0, "weight": 87,
        "dept": "普外科",
        "fatal": "肠管坏死穿孔后弥漫性腹膜炎，死亡率可达 60%",
        "window": "黄金 6 小时",
        "test": lambda c: _mesenteric(c),
        "ask": ["疼痛是不是剧烈但按压肚子反而不太痛（症状体征分离）？", "有没有房颤史？",
                "有没有呕吐、停止排气排便、腹胀？"],
        "action": "查乳酸、D-二聚体、腹部增强 CT（血管重建），禁食胃肠减压，普外科急会诊",
    },
    {
        "id": "testicular_torsion", "name": "睾丸扭转", "tier": 0, "weight": 91,
        "dept": "泌尿外科",
        "fatal": "超过 6 小时睾丸存活率骤降，误诊即睾丸切除",
        "window": "黄金 6 小时",
        "test": lambda c: _torsion(c),
        "ask": ["疼痛是不是突然发生、逐渐加重？", "有没有恶心呕吐？",
                "睾丸是不是上抬、触痛明显？", "有没有剧烈运动或外伤？"],
        "action": "立即泌尿外科急会诊 + 阴囊彩超（血流信号），不要先按「附睾炎」开药让患者回家",
    },
    {
        "id": "dka", "name": "糖尿病酮症酸中毒", "tier": 1, "weight": 84,
        "dept": "急诊 / 内分泌",
        "fatal": "严重脱水、酸中毒、低钾可致心跳骤停；常被当成「胃肠炎」",
        "window": "数小时内纠正容量与酸中毒",
        "test": lambda c: _dka(c),
        "ask": ["有没有多饮多尿、体重下降？", "有没有恶心呕吐、腹痛？",
                "呼吸是不是又深又快、有烂苹果味？"],
        "action": "查血糖、血酮/尿酮、血气、电解质，补液 + 小剂量胰岛素，监测血钾",
    },
    {
        "id": "geriatric_abdomen", "name": "老年急腹症（症状体征分离）", "tier": 1, "weight": 60, "soft": True,
        "dept": "普外科",
        "fatal": "老年人腹膜刺激征常不明显，穿孔/绞窄发现时已晚，死亡率成倍上升",
        "window": "黄金 6 小时",
        "test": lambda c: _geriatric(c),
        "ask": ["腹痛起始时间？有没有「疼痛突然减轻」？", "有没有腹胀、停止排气排便？",
                "有没有心脑血管病史？"],
        "action": "放宽影像指征（腹部 CT），不要以「肚子软、无压痛」排除外科急腹症",
    },
    {
        "id": "infant_fever", "name": "小婴儿发热（脓毒症高危）", "tier": 0, "weight": 90,
        "dept": "儿科急诊",
        "fatal": "3 个月以下婴儿发热可能是严重细菌感染唯一表现，可迅速进展为脓毒症",
        "window": "分钟级评估",
        "test": lambda c: _infant(c),
        "ask": ["宝宝多大月龄？", "体温多少？精神反应、吃奶怎么样？",
                "有没有皮疹、拒奶、哭闹不止？"],
        "action": "按儿科危重症处理：完整查体、血培养、必要时腰穿，经验性抗生素",
    },
]


# ---------------------------------------------------------------- 各条判断
def _acs(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    hits: list[str] = []
    if _t(c, "chest_pain"):
        hits.append("胸痛")
    if _t(c, "epigastric_pain"):
        hits.append("上腹痛（下壁心梗的不典型表现）")
    if not hits:
        return False, [], [], 0.0
    score = 0
    if _q(c, "crushing"):
        hits.append("压榨样胸痛"); score += 3
    if _q(c, "tearing"):
        hits.append("撕裂样疼痛"); score += 1
    if _a(c, "diaphoresis"):
        hits.append("大汗"); score += 3
    if c["radiation"]:
        hits.append(f"放射至{c['radiation'][0]}"); score += 3
    if _a(c, "nausea"):
        hits.append("恶心"); score += 1
    if _a(c, "near_syncope"):
        hits.append("濒死感"); score += 2
    if _h(c, "cad"):
        hits.append("冠心病史"); score += 3
    if _h(c, "diabetes"):
        hits.append("糖尿病（痛觉不典型）"); score += 1
    if _h(c, "hypertension"):
        hits.append("高血压"); score += 1
    if _h(c, "smoking"):
        hits.append("吸烟"); score += 1
    if _lab_high(c, "ctni", 0.04):
        hits.append("肌钙蛋白升高"); score += 4
    age = _age(c) or 0
    if age >= 55:
        score += 1
    if _t(c, "epigastric_pain") and not _t(c, "chest_pain"):
        score -= 1
    return score >= 3, hits, [], min(0.98, 0.55 + score * 0.05)


def _dissection(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    if not _t(c, "chest_pain", "back_pain", "epigastric_pain", "abdominal_pain"):
        return False, [], [], 0.0
    hits: list[str] = []
    score = 0
    if _q(c, "tearing"):
        hits.append("撕裂样/刀割样疼痛"); score += 5
    if _t(c, "chest_pain") and _t(c, "back_pain"):
        hits.append("胸背痛并存（夹层典型走向）"); score += 4
    if c["radiation"] and any(s in ("后背", "背部", "肩胛", "腰部") for s in c["radiation"]):
        hits.append("疼痛向背部/腰部放射"); score += 3
    if _h(c, "hypertension"):
        hits.append("高血压史"); score += 2
    if _h(c, "marfan"):
        hits.append("马凡综合征/结缔组织病"); score += 4
    if _lab_high(c, "ddimer", 1.0):
        hits.append("D-二聚体显著升高"); score += 2
    if _age(c) and _age(c) >= 50:
        score += 1
    return score >= 4, hits, ["双侧上臂血压差", "主动脉 CTA"], min(0.99, 0.6 + score * 0.05)


def _pe(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    hits: list[str] = []
    score = 0
    respiratory = _t(c, "dyspnea") or _t(c, "hemoptysis") or _q(c, "pleuritic")
    risk_labels: list[str] = []
    for hid, label in (("immobility", "长期卧床/长途制动"), ("recent_surgery", "近期手术/创伤"),
                       ("cancer", "恶性肿瘤"), ("dvt", "下肢深静脉血栓史"), ("pregnancy", "妊娠/产褥期")):
        if _h(c, hid):
            risk_labels.append(label); score += 2
    ddimer_high = _lab_high(c, "ddimer", 2.0)
    # 必须先有呼吸系统线索，或"胸痛 + 血栓危险因素"才谈得上肺栓塞
    if not respiratory and not (_t(c, "chest_pain") and (risk_labels or ddimer_high)):
        return False, [], [], 0.0
    if _t(c, "dyspnea"):
        hits.append("呼吸困难"); score += 2
    if _t(c, "chest_pain"):
        hits.append("胸痛"); score += 1
    if _q(c, "pleuritic"):
        hits.append("呼吸相关性胸痛"); score += 2
    if _t(c, "syncope"):
        hits.append("晕厥（大面积栓塞）"); score += 2
    if _t(c, "hemoptysis"):
        hits.append("咯血"); score += 2
    if _t(c, "leg_swelling"):
        hits.append("单侧下肢肿胀"); score += 3
    if risk_labels:
        hits.append("危险因素：" + "、".join(risk_labels))
    if ddimer_high:
        hits.append("D-二聚体升高"); score += 2
    return score >= 4, hits, ["D-二聚体", "右心负荷评估（床旁超声）"], min(0.95, 0.5 + score * 0.06)


def _stroke(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    hits: list[str] = []
    for tid, label in (("hemiparesis", "单侧肢体无力"), ("speech_disturbance", "言语不清/失语"),
                       ("facial_droop", "口角歪斜"), ("vision_loss", "突发视力障碍")):
        if _t(c, tid):
            hits.append(label)
    if not hits:
        return False, [], [], 0.0
    if _h(c, "af"):
        hits.append("房颤（心源性栓塞）")
    if _h(c, "hypertension"):
        hits.append("高血压")
    score = 3 + len(hits)
    return True, hits, ["最后一次正常时间（last known well）", "血糖", "头颅 CT"], min(0.97, 0.55 + score * 0.05)


def _sah(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    hits: list[str] = []
    if _t(c, "headache_thunderclap"):
        hits.append("雷击样/爆炸样剧烈头痛")
    if _t(c, "neck_stiffness"):
        hits.append("颈项强直")
    if _t(c, "vomiting"):
        hits.append("呕吐")
    if _t(c, "syncope"):
        hits.append("短暂意识丧失")
    score = len(hits) * 2
    trigger = _t(c, "headache_thunderclap") or (_t(c, "headache") and _t(c, "neck_stiffness"))
    return trigger, hits, ["头颅 CT 平扫", "必要时腰穿"], min(0.95, 0.5 + score * 0.1)


def _ectopic(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    age = _age(c)
    if not _female(c) or age is None or not (12 <= age <= 55):
        return False, [], [], 0.0
    hits: list[str] = []
    score = 0
    if _t(c, "amenorrhea"):
        hits.append("停经"); score += 3
    if _t(c, "abdominal_pain", "flank_pain"):
        hits.append("腹痛"); score += 2
    if _t(c, "vaginal_bleeding"):
        hits.append("阴道流血"); score += 2
    if _t(c, "syncope"):
        hits.append("晕厥（腹腔内出血）"); score += 3
    missing = []
    if not c.get("has_hcg"):
        missing.append("β-hCG 未查")
    if _h(c, "pregnancy"):
        hits.append("已知妊娠"); score += 2
    trigger = score >= 4 and (_t(c, "amenorrhea") or _h(c, "pregnancy") or _t(c, "vaginal_bleeding"))
    return trigger, hits, missing + ["经阴道超声"], min(0.97, 0.5 + score * 0.08)


def _sepsis(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    infection = _t(c, "fever") or _t(c, "chills")
    if not infection:
        return False, [], [], 0.0
    hits = ["发热/寒战"]
    score = 1
    sbp = _v(c, "bp_sys"); rr = _v(c, "r"); p = _v(c, "p")
    if sbp is not None and sbp < 100:
        hits.append(f"收缩压 {sbp} mmHg"); score += 3
    if rr is not None and rr >= 22:
        hits.append(f"呼吸 {rr} 次/分"); score += 2
    if p is not None and p > 110:
        hits.append(f"心率 {p} 次/分"); score += 2
    if _t(c, "altered_mental"):
        hits.append("意识改变"); score += 2
    if _lab_high(c, "lactate", 2.0):
        hits.append("乳酸升高"); score += 3
    if _h(c, "immunosuppress", "cancer", "renal", "diabetes"):
        hits.append("免疫抑制/基础病"); score += 1
    if _t(c, "drooling", "sore_throat"):
        hits.append("可疑上气道/深部感染灶"); score += 1
    return score >= 4, hits, ["乳酸", "血培养"], min(0.96, 0.45 + score * 0.07)


def _anaphylaxis(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    # 先过闸门：没有皮肤/气道线索，单纯低血压 + 晕厥不是过敏性休克
    skin_airway = (_t(c, "rash") and _t(c, "dyspnea")) or _t(c, "drooling") or \
                  (_t(c, "rash") and (_v(c, "bp_sys") or 999) < 90)
    if not skin_airway:
        return False, [], [], 0.0
    hits: list[str] = []
    score = 0
    if _t(c, "rash"):
        hits.append("荨麻疹/皮疹"); score += 2
    if _t(c, "dyspnea"):
        hits.append("呼吸困难/喉头水肿"); score += 3
    if _t(c, "drooling"):
        hits.append("流涎/吞咽困难"); score += 2
    sbp = _v(c, "bp_sys")
    if sbp is not None and sbp < 90:
        hits.append("低血压"); score += 3
    if _t(c, "syncope"):
        hits.append("晕厥"); score += 2
    if _t(c, "vomiting"):
        hits.append("呕吐"); score += 1
    return score >= 5, hits, ["接触史（药物/食物/造影剂/蜂蜇）"], min(0.98, 0.5 + score * 0.07)


def _gi_bleed(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    hits: list[str] = []
    if _t(c, "hematemesis"):
        hits.append("呕血")
    if _t(c, "melena"):
        hits.append("黑便")
    if not hits:
        return False, [], [], 0.0
    score = 3
    p = _v(c, "p"); sbp = _v(c, "bp_sys")
    if p is not None and p > 100:
        hits.append(f"心率 {p} 次/分"); score += 2
    if sbp is not None and sbp < 100:
        hits.append(f"收缩压 {sbp} mmHg"); score += 3
    hb = _lab(c, "hb")
    if hb is not None and hb < 90:
        hits.append(f"血红蛋白 {hb} g/L"); score += 3
    if _h(c, "anticoagulant"):
        hits.append("抗凝/抗血小板药物"); score += 1
    return True, hits, ["血常规", "凝血功能"], min(0.96, 0.5 + score * 0.06)


def _airway(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    hits: list[str] = []
    score = 0
    if _t(c, "drooling"):
        hits.append("流涎/吞咽困难（会厌炎典型）"); score += 4
    if _t(c, "sore_throat") and _t(c, "dyspnea"):
        hits.append("咽痛 + 呼吸困难"); score += 3
    if _t(c, "dyspnea") and any(k in c["text"] for k in ("喘鸣", "喉咙发紧", "声音嘶哑", "吸气困难")):
        hits.append("喘鸣/喉鸣"); score += 3
    if _t(c, "self_harm"):
        return False, [], [], 0.0
    return score >= 3, hits, ["间接喉镜/纤维喉镜（需有气道保障条件下）"], min(0.97, 0.5 + score * 0.08)


def _meningitis(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    if not _t(c, "fever"):
        return False, [], [], 0.0
    hits = ["发热"]
    score = 1
    if _t(c, "neck_stiffness"):
        hits.append("颈项强直"); score += 3
    if _t(c, "headache"):
        hits.append("头痛"); score += 1
    if _t(c, "altered_mental"):
        hits.append("意识改变"); score += 2
    if _t(c, "rash"):
        hits.append("皮疹/瘀点（暴发型流脑）"); score += 3
    if _t(c, "vomiting"):
        hits.append("呕吐"); score += 1
    trigger = score >= 4 and (_t(c, "neck_stiffness") or _t(c, "altered_mental"))
    return trigger, hits, ["血培养", "脑脊液检查"], min(0.94, 0.45 + score * 0.07)


def _hypoglycemia(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    glu = _lab(c, "glu")
    hits: list[str] = []
    score = 0
    if glu is not None and glu < 3.0:
        hits.append(f"血糖 {glu} mmol/L"); score += 5
    if _t(c, "altered_mental"):
        hits.append("意识改变"); score += 2
    if _h(c, "diabetes"):
        hits.append("糖尿病史"); score += 1
    if _t(c, "syncope"):
        hits.append("晕厥"); score += 1
    return score >= 4, hits, ["床旁血糖（所有意识改变患者必查）"], min(0.97, 0.5 + score * 0.07)


def _arrhythmia(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    if not _t(c, "syncope"):
        return False, [], [], 0.0
    hits = ["晕厥"]
    score = 1
    if _t(c, "palpitation"):
        hits.append("心悸"); score += 2
    if _h(c, "af", "cad"):
        hits.append("房颤/冠心病史"); score += 2
    if _h(c, "family_sudden_death"):
        hits.append("家族猝死史"); score += 2
    p = _v(c, "p")
    if p is not None and (p > 120 or p < 45):
        hits.append(f"心率 {p} 次/分"); score += 2
    return score >= 4, hits, ["12 导联心电图", "电解质（钾、镁）"], min(0.93, 0.45 + score * 0.07)


def _mesenteric(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    if not _t(c, "abdominal_pain", "diffuse_abdominal_pain", "epigastric_pain"):
        return False, [], [], 0.0
    hits: list[str] = []
    score = 0
    if _h(c, "af"):
        hits.append("房颤（栓塞来源）"); score += 3
    if _t(c, "obstipation"):
        hits.append("停止排气排便"); score += 2
    if _t(c, "distension"):
        hits.append("腹胀"); score += 1
    if _t(c, "vomiting"):
        hits.append("呕吐"); score += 1
    if _q(c, "colicky"):
        hits.append("阵发性绞痛"); score += 1
    if _lab_high(c, "lactate", 2.0):
        hits.append("乳酸升高"); score += 3
    return score >= 4, hits, ["乳酸", "腹部增强 CT（血管重建）"], min(0.9, 0.4 + score * 0.07)


def _torsion(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    if not _t(c, "testicular_pain"):
        return False, [], [], 0.0
    hits = ["睾丸疼痛/肿胀"]
    score = 3
    if _t(c, "vomiting") or _a(c, "nausea"):
        hits.append("恶心呕吐"); score += 1
    if _t(c, "abdominal_pain"):
        hits.append("腹痛（牵涉痛）"); score += 1
    return True, hits, ["阴囊彩超（睾丸血流）"], min(0.95, 0.4 + score * 0.1)


def _dka(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    if not _t(c, "thirst_polyuria"):
        return False, [], [], 0.0
    hits = ["多饮多尿"]
    score = 1
    if _t(c, "vomiting", "abdominal_pain"):
        hits.append("呕吐/腹痛（酮症表现）"); score += 2
    if _t(c, "dyspnea"):
        hits.append("深大呼吸"); score += 2
    glu = _lab(c, "glu")
    if glu is not None and glu > 13.9:
        hits.append(f"血糖 {glu} mmol/L"); score += 3
    if _h(c, "diabetes"):
        hits.append("糖尿病史"); score += 1
    return score >= 4, hits, ["血酮/尿酮", "血气分析", "电解质"], min(0.92, 0.4 + score * 0.07)


def _geriatric(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    age = _age(c)
    if age is None or age < 65:
        return False, [], [], 0.0
    if not _t(c, "abdominal_pain", "epigastric_pain", "rlq_pain", "ruq_pain", "diffuse_abdominal_pain"):
        return False, [], [], 0.0
    hits = [f"{age} 岁老年患者腹痛"]
    if _t(c, "vomiting"):
        hits.append("呕吐")
    if _h(c, "af", "cad", "cva"):
        hits.append("心脑血管基础病")
    return True, hits, ["腹部 CT（放宽指征）"], 0.6


def _infant(c: dict[str, Any]) -> tuple[bool, list[str], list[str], float]:
    age = _age(c)
    if age is None:
        return False, [], [], 0.0
    months = age * 12 if age <= 3 else None
    if months is None or months > 3 or not _t(c, "fever"):
        return False, [], [], 0.0
    return True, [f"月龄 {months:.0f} 个月 + 发热"], ["完整查体", "血培养"], 0.9


# ---------------------------------------------------------------- 对外主函数
def screen(ctx: dict[str, Any]) -> dict[str, Any]:
    """逐条排雷。返回触发的红旗、可疑待证据项、以及已排查清单。"""
    flags: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    cleared: list[str] = []

    for rule in RULES:
        try:
            hit, hits, missing, confidence = rule["test"](ctx)
        except Exception:  # 单条规则异常不能拖垮整条流水线
            hit, hits, missing, confidence = False, [], [], 0.0
        if hit:
            flags.append({
                "id": rule["id"], "name": rule["name"], "tier": rule["tier"],
                "weight": rule["weight"], "dept": rule["dept"],
                "fatal": rule["fatal"], "window": rule["window"],
                "hits": hits, "ask": rule["ask"], "action": rule["action"],
                "confidence": round(confidence, 2), "soft": rule.get("soft", False),
                "missing": missing,
            })
            # 关键证据缺失 → 同时生成"待补充"提醒（这才是防漏诊的关键）
            if missing:
                partial.append({
                    "id": rule["id"], "name": rule["name"],
                    "missing": missing, "confidence": round(confidence, 2),
                })
        else:
            cleared.append(rule["name"])

    # 硬红旗在前（tier 0 最危），软提示垫底；同级按权重降序
    flags.sort(key=lambda f: (1 if f["soft"] else 0, f["tier"], -f["weight"]))
    hard = [f for f in flags if not f["soft"]]

    if hard and any(f["weight"] >= 95 for f in hard):
        headline = f"红色警报：触发 {len(hard)} 条致命病预警，最高危「{hard[0]['name']}」，必须立即处置"
    elif hard:
        headline = f"橙色预警：触发 {len(hard)} 条高危预警（{hard[0]['name']}），按高危流程处理"
    else:
        soft = [f for f in flags if f["soft"]]
        if soft:
            headline = (f"未见致命性红旗：{len(soft)} 条软提示待关注（{soft[0]['name']}）")
        else:
            headline = f"未见致命性红旗：已排查 {len(RULES)} 类致命急症"

    return {
        "flags": flags,
        "hard_flags": hard,
        "partial": partial,
        "screened": len(RULES),
        "cleared": cleared,
        "max_tier": min((f["tier"] for f in hard), default=9),
        "headline": headline,
    }
