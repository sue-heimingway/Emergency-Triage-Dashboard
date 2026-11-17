"""Agent ③：分级裁判 —— 按 ESI（Emergency Severity Index）v4 标准打分。

ESI 的官方算法是四步，本项目严格照做，不自己发明级别：

    第 1 步  这个患者需要立即的抢救性干预吗？      是 → ESI 1
    第 2 步  这是高危情况吗？/ 剧痛、意识改变？    是 → ESI 2
    第 3 步  预计需要几项资源？                   ≥2 → ESI 3 / 1 项 → ESI 4 / 0 项 → ESI 5
    第 4 步  危险区间生命体征（在 3 级里）→ 升到 2 级

补充：需求方给的是"1–4 级"，而 ESI 标准是 1–5 级。本项目两者都给：
标准五级制（主）+ 简化四级制（5 级并入 4 级"候诊"），由 config.ESI_MODE 切换显示。

本模块只做分级，不做诊断；所有结论都写成"分级依据"。
"""
from __future__ import annotations

from typing import Any

from . import config

# 需要立即抢救的情形（ESI 第 1 步）
LIFE_THREAT_TERMS = {
    "altered_mental": "急性意识状态改变",
    "drooling": "气道梗阻风险",
    "active_bleeding": "活动性大出血",
    "hematemesis": "呕血",
    "seizure": "持续抽搐",
    "hemiparesis": "急性偏瘫",
    "speech_disturbance": "急性失语",
}

# 资源清单（ESI 第 3 步只数"项"，不数"次"）
RESOURCE_RULES: list[tuple[str, str, set[str]]] = [
    ("ecg", "12 导联心电图", {"chest_pain", "dyspnea", "palpitation", "syncope"}),
    ("labs", "实验室检查（心肌酶/血常规/生化）", {"chest_pain", "abdominal_pain", "epigastric_pain",
                                        "fever", "vomiting", "diffuse_abdominal_pain",
                                        "thirst_polyuria", "melena"}),
    ("imaging", "影像学（X 线/CT/超声）", {"abdominal_pain", "epigastric_pain", "rlq_pain",
                                     "ruq_pain", "diffuse_abdominal_pain", "back_pain",
                                     "dyspnea", "headache", "flank_pain", "fracture"}),
    ("iv", "静脉通路/补液", {"vomiting", "diarrhea", "fever", "chills", "hematemesis", "melena"}),
    ("iv_meds", "静脉给药（镇痛/抗生素/止吐）", set()),
    ("consult", "专科会诊", set()),
    ("procedure", "简单操作（缝合/夹板/导尿）", {"laceration", "fracture", "burn"}),
]


def _danger_vitals(vitals: dict[str, Any]) -> list[str]:
    """ESI 的"危险区间"生命体征：3 级患者出现即升 2 级。"""
    danger: list[str] = []
    p = vitals.get("p")
    r = vitals.get("r")
    spo2 = vitals.get("spo2")
    sbp = vitals.get("bp_sys")
    t = vitals.get("t")
    if p is not None and p > 100:
        danger.append(f"心率 {p} > 100")
    if r is not None and r > 20:
        danger.append(f"呼吸 {r} > 20")
    if spo2 is not None and spo2 < 95:
        danger.append(f"SpO₂ {spo2}% < 95%")
    if t is not None and (t > 38.5 or t < 36.0):
        danger.append(f"体温 {t}℃ 超出 36–38.5")
    if sbp is not None and sbp < 100:
        danger.append(f"收缩压 {sbp} < 100")
    return danger


def _predict_resources(ctx: dict[str, Any], flags: list[dict[str, Any]]) -> list[dict[str, str]]:
    used: list[dict[str, str]] = []
    for rid, label, onset in RESOURCE_RULES:
        hit = False
        if rid == "iv_meds":
            hit = (ctx.get("nrs") or 0) >= 6 or any(f["id"] in ("sepsis", "anaphylaxis", "gi_bleed")
                                                    for f in flags)
        elif rid == "consult":
            hit = any(f.get("tier", 1) <= 1 for f in flags)
        else:
            hit = bool(ctx["terms"] & onset)
        if hit:
            used.append({"id": rid, "label": label})
    # 已有化验结果 → 说明这一项资源已经用掉了（ESI 数的是"将要/已经使用"）
    if ctx["labs"] and not any(r["id"] == "labs" for r in used):
        used.append({"id": "labs", "label": "实验室检查（已抽血）"})
    return used


def evaluate(ctx: dict[str, Any], screening: dict[str, Any]) -> dict[str, Any]:
    vitals = ctx["vitals"]
    terms = ctx["terms"]
    flags = screening["hard_flags"]
    reasons: list[dict[str, str]] = []

    # ---------------- 第 1 步：是否需要立即抢救 ----------------
    sbp = vitals.get("bp_sys")
    p = vitals.get("p")
    r = vitals.get("r")
    spo2 = vitals.get("spo2")
    gcs_like = terms & {"altered_mental"}

    step1: list[str] = []
    if spo2 is not None and spo2 < 90:
        step1.append(f"SpO₂ {spo2}% < 90%，存在低氧血症")
    if r is not None and (r >= 30 or r <= 8):
        step1.append(f"呼吸 {r} 次/分，呼吸衰竭")
    if sbp is not None and sbp < 90 and p is not None and p > 110:
        step1.append(f"血压 {sbp}/{vitals.get('bp_dia')} 伴心率 {p}，休克状态")
    if gcs_like:
        step1.append("急性意识状态改变（无法维持气道/保护反射）")
    if terms & {"active_bleeding"}:
        step1.append("活动性大出血未控制")
    if terms & {"drooling"}:
        step1.append("流涎/吞咽困难，气道随时梗阻")
    if any(f["id"] in ("anaphylaxis", "airway") and f["confidence"] >= 0.75 for f in flags):
        step1.append(f"致命性急症：{next(f['name'] for f in flags if f['id'] in ('anaphylaxis', 'airway'))}")
    if _lab_critical(ctx, ("k",), 6.0, "high"):
        step1.append("血钾 > 6.0 mmol/L，随时恶性心律失常")
    if _lab_critical(ctx, ("glu",), 3.0, "low") and gcs_like:
        step1.append("低血糖伴意识改变")

    if step1:
        reasons = [{"k": "第 1 步：需要立即抢救", "v": s} for s in step1]
        return _result(1, "step1", reasons, [], [], ctx)

    # ---------------- 第 2 步：高危 / 剧痛 / 意识改变 ----------------
    step2: list[str] = []
    for f in flags:
        step2.append(f"高危诊断链：{f['name']}（置信度 {f['confidence']}）")
    nrs = ctx.get("nrs")
    if nrs is not None and nrs >= 7:
        step2.append(f"疼痛评分 NRS {nrs}/10，属剧痛")
    if gcs_like:
        step2.append("意识状态改变（嗜睡/答非所问）")
    danger = _danger_vitals(vitals)
    if danger and not step2:
        # 只有危险生命体征时仍按第 3 步先算资源，再在第 4 步升级
        pass
    if step2:
        reasons = [{"k": "第 2 步：高危情况", "v": s} for s in step2]
        return _result(2, "step2", reasons, [], danger, ctx)

    # ---------------- 第 3 步：数资源 ----------------
    resources = _predict_resources(ctx, flags)
    n = len(resources)
    level = 3 if n >= 2 else (4 if n == 1 else 5)
    reasons.append({"k": "第 3 步：预计资源项", "v": f"{n} 项 → ESI {level}"})
    for res in resources:
        reasons.append({"k": "资源", "v": res["label"]})

    # ---------------- 第 4 步：危险生命体征升级 ----------------
    if level == 3 and danger:
        reasons.append({"k": "第 4 步：危险区间生命体征", "v": "；".join(danger) + " → 升为 ESI 2"})
        return _result(2, "step4", reasons, resources, danger, ctx)

    if danger:
        reasons.append({"k": "危险区间生命体征", "v": "；".join(danger) + "（已纳入复测项）"})
    return _result(level, "step3", reasons, resources, danger, ctx)


def _lab_critical(ctx: dict[str, Any], ids: tuple[str, ...], threshold: float, side: str) -> bool:
    for lid in ids:
        item = ctx["lab_ids"].get(lid)
        if not item:
            continue
        if side == "high" and item["value"] >= threshold:
            return True
        if side == "low" and item["value"] <= threshold:
            return True
    return False


def _result(level: int, path: str, reasons: list[dict[str, str]],
            resources: list[dict[str, str]], danger: list[str], ctx: dict[str, Any]) -> dict[str, Any]:
    level_4 = 4 if level == 5 else level
    wait = config.ESI_WAIT_TARGET[level]
    confidence = 0.9 if path in ("step1", "step2") else (0.78 if path == "step4" else 0.72)
    target_text = "立即进入抢救室" if level == 1 else f"目标候诊 ≤ {wait} 分钟"
    return {
        "level": level,
        "level_label": config.ESI_LABEL[level],
        "level_color": config.ESI_COLOR[level],
        "level_4": level_4,
        "mode": config.ESI_MODE,
        "path": path,
        "reasons": reasons,
        "resources": resources,
        "danger_vitals": danger,
        "wait_target": wait,
        "confidence": confidence,
        "summary": f"ESI {level} 级（{config.ESI_LABEL[level]}），{target_text}",
    }
