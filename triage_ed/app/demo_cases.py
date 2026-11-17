"""演示病例：点一下就把整套流水线跑给你看。

每个病例都对应一个"教学点"，别随便改数字——
它们是用来证明系统能抓住某种漏诊的（见 expect 字段）。
"""
from __future__ import annotations

CASES: list[dict] = [
    {
        "key": "dissection",
        "title": "① 撕裂样胸背痛 · 58 岁男",
        "tag": "主动脉夹层",
        "expect": "Agent ② 必须抓出夹层；ESI 应为 1–2 级；Agent ④ 检查医生是否漏了双侧血压/D-二聚体",
        "patient": {
            "name": "王建国", "age": 58, "sex": "男",
            "chief": "胸口像被刀割一样疼，一下子就到最疼了，从胸口一直撕到后背，出了一身大汗",
            "history_text": "高血压十年，一直吃降压药；抽烟一天一包",
            "bp_sys": 168, "bp_dia": 95, "p": 102, "r": 22, "t": 36.8, "spo2": 96,
            "allergy": "否认药物过敏",
            "lab_text": "D-二聚体 6.4 mg/L FEU ↑\n肌钙蛋白I 0.02 ng/mL",
        },
    },
    {
        "key": "acs",
        "title": "② 压榨性胸痛 · 61 岁男",
        "tag": "急性心梗",
        "expect": "压榨样 + 左臂放射 + 大汗 → 心梗预警；危险生命体征 → ESI 2；化验单里肌钙蛋白危急值",
        "patient": {
            "name": "李国强", "age": 61, "sex": "男",
            "chief": "胸口像被石头压着，闷得喘不上气，左边胳膊发麻，一直在出汗",
            "history_text": "高血压、糖尿病，五年前心梗放了一个支架；抽烟",
            "bp_sys": 100, "bp_dia": 65, "p": 118, "r": 24, "t": 36.5, "spo2": 93,
            "allergy": "青霉素过敏",
            "lab_text": "肌钙蛋白I 0.82 ng/mL ↑ 参考 0-0.04\n白细胞 15.6 ×10^9/L ↑",
        },
    },
    {
        "key": "ectopic",
        "title": "③ 停经 45 天 · 右下腹痛 · 26 岁女",
        "tag": "宫外孕破裂",
        "expect": "育龄女性 + 停经 + 腹痛 + 晕厥 → 宫外孕预警；未查 hCG → Agent ④ 判 critical（这是最经典的漏诊）",
        "patient": {
            "name": "张晓雨", "age": 26, "sex": "女",
            "chief": "月经两个月没来了，下面有点出血，右下腹疼得厉害，刚才眼前一黑差点晕过去",
            "history_text": "",
            "bp_sys": 88, "bp_dia": 54, "p": 124, "r": 24, "t": 36.9, "spo2": 97,
            "allergy": "",
            "lab_text": "",
        },
    },
    {
        "key": "sepsis",
        "title": "④ 高热 + 叫不醒 · 76 岁男",
        "tag": "脓毒症休克",
        "expect": "发热 + 低血压 + 意识改变 + 乳酸 5.2 → 脓毒症预警；ESI 1 级；直接 P0 呼叫医生",
        "patient": {
            "name": "陈德海", "age": 76, "sex": "男",
            "chief": "发烧三天了，今天叫不太醒，跟他说话没反应，一天没怎么尿",
            "history_text": "糖尿病、高血压，卧床两年，长期留置导尿",
            "bp_sys": 86, "bp_dia": 50, "p": 126, "r": 26, "t": 39.6, "spo2": 92,
            "allergy": "磺胺过敏",
            "lab_text": "乳酸 5.2 mmol/L ↑\n白细胞 18.6 ×10^9/L ↑\n肌酐 245 μmol/L ↑",
        },
    },
    {
        "key": "stroke",
        "title": "⑤ 说话不清 + 半身无力 · 68 岁女",
        "tag": "急性脑卒中",
        "expect": "识别为卒中通道；Agent ② 会追问「最后一次正常时间」；ESI 2 级",
        "patient": {
            "name": "刘桂芳", "age": 68, "sex": "女",
            "chief": "一个小时前还好好的，突然说话说不清楚，右边胳膊抬不起来，走路往一边倒",
            "history_text": "房颤，高血压",
            "bp_sys": 176, "bp_dia": 98, "p": 96, "r": 20, "t": 36.6, "spo2": 96,
            "allergy": "否认药物过敏",
            "lab_text": "血糖 8.2 mmol/L",
        },
    },
    {
        "key": "abdomen",
        "title": "⑥ 绞痛 + 停止排气 · 34 岁女",
        "tag": "肠梗阻",
        "expect": "腹痛+呕吐+停止排气排便 → 肠梗阻/绞窄预警；家属暴躁时点「沟通安抚」生成话术",
        "patient": {
            "name": "赵敏", "age": 34, "sex": "女",
            "chief": "肚子一阵一阵绞着疼，疼起来直不起腰，还吐了两次，两天没放屁了",
            "history_text": "三年前剖宫产",
            "bp_sys": 118, "bp_dia": 76, "p": 96, "r": 20, "t": 37.6, "spo2": 98,
            "allergy": "否认药物过敏",
            "lab_text": "白细胞 15.6 ×10^9/L ↑\n血钾 3.4 mmol/L ↓",
        },
    },
    {
        "key": "minor",
        "title": "⑦ 切菜划伤 · 22 岁男",
        "tag": "非紧急",
        "expect": "无红旗、需 1 项资源（缝合）→ ESI 4 级；用来演示「不给高分」也是一种能力",
        "patient": {
            "name": "周子豪", "age": 22, "sex": "男",
            "chief": "切菜切到手指了，流了点血，自己按住了，现在不太流了",
            "history_text": "",
            "bp_sys": 118, "bp_dia": 74, "p": 78, "r": 16, "t": 36.6, "spo2": 99,
            "allergy": "否认药物过敏",
            "lab_text": "",
        },
    },
]
