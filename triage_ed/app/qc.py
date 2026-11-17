"""Agent ④：交叉核验质控 —— 严苛的质控主任，专门盯裁判有没有瞎给级别。

它不重新算一遍 ESI（那是重复劳动，还会被同一个思维惯性带偏），
而是用一套**独立的"安全底线"**去卡：

    安全底线 = 这类患者无论如何都不该高于几级

    例子：任何一条致命红旗 → 底线就是 2 级；
         休克/低氧/气道梗阻 → 底线就是 1 级。

然后拿"裁判给的级"跟"安全底线"比：
    裁判级别 > 底线  →  低判，必须升级（这是漏诊的源头）
    裁判级别 < 底线  →  不可能发生，说明裁判比底线更激进，通过
    裁判 1/2 级但没有任何硬证据 → 过度分级，浪费抢救资源，建议复核

除此之外还查 5 类"看不见的漏诊"：生命体征缺口、人口学缺口、
育龄女性未查 hCG、过敏史缺失、以及"剧痛却给了低级别"的逻辑矛盾。
"""
from __future__ import annotations

from typing import Any

CRITICAL = "critical"
MAJOR = "major"
MINOR = "minor"


def _has(ctx: dict[str, Any], key: str) -> bool:
    return key in ctx["terms"]


def safety_floor(ctx: dict[str, Any], screening: dict[str, Any]) -> tuple[int, list[str]]:
    """独立于分级裁判的安全底线：这类患者最宽松也只能到这个级别。"""
    vitals = ctx["vitals"]
    reasons: list[str] = []
    floor = 5

    sbp, p, r, spo2 = vitals.get("bp_sys"), vitals.get("p"), vitals.get("r"), vitals.get("spo2")
    if (spo2 is not None and spo2 < 90) or (r is not None and (r >= 30 or r <= 8)) or \
       (sbp is not None and sbp < 90 and p is not None and p > 110):
        return 1, ["存在休克/呼吸衰竭级生命体征"]

    hard = screening["hard_flags"]
    if any(f["tier"] == 0 for f in hard):
        floor = min(floor, 2)
        reasons.append(f"触发致命病预警：{'、'.join(f['name'] for f in hard if f['tier'] == 0)}")
    if _has(ctx, "altered_mental") or _has(ctx, "drooling"):
        floor = min(floor, 2)
        reasons.append("意识改变/气道风险")
    if (ctx.get("nrs") or 0) >= 8:
        floor = min(floor, 2)
        reasons.append("NRS ≥ 8 剧痛")
    return floor, reasons


def verify(ctx: dict[str, Any], screening: dict[str, Any], judgment: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    judge = judgment["level"]
    floor, floor_reasons = safety_floor(ctx, screening)
    hard = screening["hard_flags"]
    danger = judgment.get("danger_vitals") or []

    # ---- 1. 红旗漏判 ----
    if hard and judge > 2:
        checks.append({
            "id": "missed_red_flag", "level": CRITICAL, "name": "红旗漏判",
            "detail": f"已触发「{hard[0]['name']}」等 {len(hard)} 条致命预警，裁判却给了 ESI {judge}",
        })
    # ---- 2. 休克/低氧漏判 ----
    if floor == 1 and judge > 1:
        checks.append({
            "id": "missed_critical_vitals", "level": CRITICAL, "name": "危重生命体征漏判",
            "detail": "；".join(floor_reasons) + f"，裁判却给了 ESI {judge}",
        })
    # ---- 3. 剧痛与级别矛盾 ----
    nrs = ctx.get("nrs")
    if nrs is not None and nrs >= 8 and judge >= 4:
        checks.append({
            "id": "pain_mismatch", "level": MAJOR, "name": "剧痛与级别矛盾",
            "detail": f"NRS {nrs}/10 却定为 ESI {judge}，按 ESI 规则剧痛属第 2 步（≥2 级）",
        })
    # ---- 4. 生命体征缺口 ----
    missing_vitals = [k for k in ("bp_sys", "p", "r", "t", "spo2") if ctx["vitals"].get(k) is None]
    if missing_vitals:
        label = {"bp_sys": "血压", "p": "心率", "r": "呼吸", "t": "体温", "spo2": "血氧"}
        checks.append({
            "id": "vitals_gap", "level": CRITICAL if len(missing_vitals) >= 3 else MINOR,
            "name": "生命体征不完整",
            "detail": "缺：" + "、".join(label[k] for k in missing_vitals) +
                      ("——三项以上缺失，任何分级都不可靠" if len(missing_vitals) >= 3 else ""),
        })
    # ---- 5. 人口学缺口 ----
    demo_gap = []
    if ctx.get("age") is None:
        demo_gap.append("年龄")
    if ctx.get("sex") in (None, "未知"):
        demo_gap.append("性别")
    if demo_gap:
        checks.append({"id": "demo_gap", "level": MINOR, "name": "人口学信息缺失",
                       "detail": "缺：" + "、".join(demo_gap) + "（年龄/性别直接决定风险阈值）"})
    # ---- 6. 育龄女性腹痛未查 hCG ----
    age, female = ctx.get("age"), ctx.get("sex") == "女"
    abd = bool(ctx["terms"] & {"abdominal_pain", "diffuse_abdominal_pain", "flank_pain", "rlq_pain"})
    if female and age and 12 <= age <= 55 and abd and not ctx.get("has_hcg"):
        checks.append({
            "id": "hcg_gap", "level": CRITICAL, "name": "育龄女性腹痛未查 β-hCG",
            "detail": "宫外孕是育龄女性腹痛第一大死因，未查 hCG 不能安全放行",
        })
    # ---- 7. 过敏史 ----
    if not ctx.get("allergy"):
        checks.append({"id": "allergy_gap", "level": MINOR, "name": "过敏史未记录",
                       "detail": "静脉用药前必须确认过敏史，否则无法启动静脉给药"})
    # ---- 8. 过度分级 ----
    if judge <= 2 and not hard and not danger and floor >= 4:
        checks.append({
            "id": "over_triage", "level": MAJOR, "name": "疑似过度分级",
            "detail": f"没有任何致命红旗或危险生命体征，却给了 ESI {judge}，会挤占抢救资源",
        })

    # ---- 汇总 ----
    critical = [c for c in checks if c["level"] == CRITICAL]
    major = [c for c in checks if c["level"] == MAJOR]
    minor = [c for c in checks if c["level"] == MINOR]
    # 会改变级别的检查 vs 只是"数据不全"的检查，两者的处置完全不同
    level_checks = {"missed_red_flag", "missed_critical_vitals", "over_triage", "pain_mismatch"}
    level_conflicts = [c for c in critical if c["id"] in level_checks] + major
    data_gaps = [c for c in critical if c["id"] not in level_checks]
    final = min(judge, floor) if floor < judge else judge

    if level_conflicts:
        raise_level = any(c["id"] in ("missed_red_flag", "missed_critical_vitals") for c in critical) \
            or judge > floor
        verdict = "建议升级" if raise_level else "建议复核"
        conflict = True
    elif judge > floor:
        verdict, conflict = "建议升级", True
    elif data_gaps:
        verdict, conflict = "带条件放行", False
    else:
        verdict, conflict = "通过", False

    if conflict:
        headline = f"质控不通过：{verdict}（裁判 ESI {judge} → 建议 ESI {final}）"
    elif data_gaps:
        headline = f"质控有条件通过：{verdict}（{'；'.join(c['name'] for c in data_gaps)}）"
    else:
        headline = f"质控通过：裁判 ESI {judge} 未被推翻（安全底线 {floor} 级）"

    return {
        "verdict": verdict,
        "conflict": conflict,
        "judge_level": judge,
        "floor": floor,
        "floor_reasons": floor_reasons,
        "final_level": min(final, 5),
        "checks": checks,
        "critical": critical,
        "major": major,
        "minor": minor,
        "data_critical": data_gaps,
        "headline": headline,
        "jury": [
            {"role": "分级裁判 Agent ③", "level": judge, "color": "#334155",
             "argument": judgment.get("summary", "")},
            {"role": "交叉核验 Agent ④", "level": final, "color": "#b91c1c",
             "argument": verdict + "：" + ("；".join(c["detail"] for c in (level_conflicts or data_gaps)) or "未发现冲突")},
        ],
    }
