"""六个 Agent 的编排层。

流水线（每个 Agent 的产出都会被记录、可回溯）：

  ① 信息采集      → 口语变术语、化验单结构化、缺口清单
  ② 红旗排雷      → 致命病预警 + 追问清单 + 已排查清单
  ③ 分级裁判      → ESI 级别 + 四步依据 + 资源预测
  ④ 交叉核验      → 安全底线 vs 裁判级别 + 冲突判定（绝不自动降级）
  ⑤ 动态候诊      → 复评计划、队列位次、扫码上报后的重评估
  ⑥ 沟通安抚      → 三版话术（常规/焦虑/暴躁）+ 禁忌

两个底座：
  术语库      → terminology.py
  人在环 HITL → store.raise_ticket + server 的 /api/hitl/*，以及本文件里的升级策略

安全原则：Agent 可以自动**升级**，但绝不自动**降级**；降级必须由人决定。
"""
from __future__ import annotations

import time
from typing import Any

from . import comms, config, esi, qc, redflags, terminology as T
from .store import Store

RECENT_WINDOW = 30  # 队列里最多展示多少位


def _card(no: int, key: str, name: str, role: str, status: str,
          headline: str, points: list[str], extra: dict[str, Any] | None = None,
          confidence: float = 0.9, elapsed_ms: float = 0.0) -> dict[str, Any]:
    return {
        "no": no, "key": key, "name": name, "role": role, "status": status,
        "headline": headline, "points": points, "extra": extra or {},
        "confidence": round(confidence, 2), "elapsed_ms": round(elapsed_ms, 2),
    }


def ctx_from_visit(visit: dict[str, Any]) -> dict[str, Any]:
    """从持久化的瘦上下文重建工作上下文（供重评估使用）。"""
    sc = visit["ctx"]
    labs = list(sc.get("labs") or [])
    return {
        "name": visit["name"], "age": visit.get("age"), "sex": visit.get("sex"),
        "chief": visit.get("chief", ""), "nurse_note": "",
        "terms": set(sc.get("terms") or []),
        "qualifiers": set(sc.get("qualifiers") or []),
        "associated": set(sc.get("associated") or []),
        "history": set(sc.get("history") or []),
        "findings": list(sc.get("findings") or []),
        "translation": list(sc.get("translation") or []),
        "radiation": list(sc.get("radiation") or []),
        "vitals": dict(sc.get("vitals") or {}),
        "vital_flags": T.vital_level(dict(sc.get("vitals") or {})),
        "labs": labs,
        "lab_ids": {l["id"]: l for l in labs},
        "nrs": sc.get("nrs"),
        "text": visit.get("chief", ""),
        "history_text": "",
        "allergy": sc.get("allergy") or "",
        "has_hcg": bool(sc.get("has_hcg")),
    }


# ======================================================================
# Agent ① 信息采集与多模态
# ======================================================================
def agent_intake(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    findings = ctx["findings"]
    translation = ctx["translation"]
    labs = ctx["labs"]
    abnormal = [l for l in labs if l["abnormal"]]
    critical = [l for l in labs if l["critical"]]

    missing: list[str] = []
    if ctx.get("age") is None:
        missing.append("年龄")
    if ctx.get("sex") in (None, "未知"):
        missing.append("性别")
    vital_names = {"bp_sys": "血压", "p": "心率", "r": "呼吸", "t": "体温", "spo2": "血氧"}
    for key, label in vital_names.items():
        if ctx["vitals"].get(key) is None:
            missing.append(label)
    if not ctx.get("allergy"):
        missing.append("过敏史")

    points: list[str] = []
    if translation:
        terms = list(dict.fromkeys(t["term"] for t in translation))
        more = f" 等 {len(terms)} 个" if len(terms) > 5 else ""
        points.append(f"识别标准术语：{'、'.join(terms[:5])}{more}")
    if labs:
        points.append(f"化验解析 {len(labs)} 项，异常 {len(abnormal)} 项" +
                      (f"，其中危急值 {len(critical)} 项" if critical else ""))
    if missing:
        points.append("待补：" + "、".join(missing[:6]))
    if not points:
        points.append("主诉信息不足，已生成补问清单")

    if critical:
        status, head = "critical", f"识别到 {len(critical)} 项危急值，已置顶提醒医生"
    elif translation or labs:
        status = "done"
        parts = []
        if translation:
            distinct = len({t["term"] for t in translation})
            parts.append(f"把口语规范为 {distinct} 个标准术语")
        if labs:
            parts.append(f"解析 {len(labs)} 项化验（异常 {len(abnormal)} 项）")
        head = "，".join(parts)
    else:
        status, head = "warn", "主诉信息不足，已生成补问清单"

    return _card(
        1, "intake", "信息采集与多模态", "极其敏锐的护士", status, head, points,
        {"translation": translation, "labs": labs, "vitals": ctx["vitals"],
         "missing": missing, "radiation": ctx["radiation"],
         "images": payload.get("images") or []},
        confidence=0.94 if translation else 0.6,
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )


# ======================================================================
# Agent ② 红旗风险预警
# ======================================================================
def agent_redflag(ctx: dict[str, Any], screening: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    flags = screening["flags"]
    points: list[str] = []
    for f in flags[:4]:
        points.append(f"{f['name']}（置信度 {f['confidence']}）：" + "、".join(f["hits"][:4]))
    for p in screening["partial"][:2]:
        points.append(f"证据缺口：{p['name']} 还缺 " + "、".join(p["missing"]))
    if not flags:
        points.append(f"已排查 {screening['screened']} 类致命急症，均未触发")

    if screening["hard_flags"]:
        status = "critical" if screening["max_tier"] == 0 else "warn"
    else:
        status = "done"

    return _card(
        2, "redflag", "红旗风险预警", "极其博学的急诊老专家", status, screening["headline"], points,
        {"flags": flags, "partial": screening["partial"], "cleared": screening["cleared"],
         "screened": screening["screened"], "ask": [a for f in flags[:2] for a in f["ask"]][:6]},
        confidence=max((f["confidence"] for f in flags), default=0.9),
        elapsed_ms=(time.perf_counter() - started) * 1000,
    )


# ======================================================================
# Agent ③ 分级裁判
# ======================================================================
def agent_judge(judgment: dict[str, Any]) -> dict[str, Any]:
    points = [f"{r['k']}｜{r['v']}" for r in judgment["reasons"][:6]]
    return _card(
        3, "judge", "分级裁判", "极其冷酷的台长",
        "critical" if judgment["level"] == 1 else "done",
        judgment["summary"], points,
        {"judgment": judgment},
        confidence=judgment["confidence"],
    )


# ======================================================================
# Agent ④ 交叉核验质控
# ======================================================================
def agent_qc(result: dict[str, Any]) -> dict[str, Any]:
    points = [f"[{c['level']}] {c['name']}：{c['detail']}" for c in result["checks"][:5]]
    if not points:
        points = ["未发现分级错误、生命体征缺口或逻辑矛盾"]
    points.append(f"安全底线：ESI {result['floor']} 级" +
                  (f"（{'；'.join(result['floor_reasons'])}）" if result["floor_reasons"] else ""))
    return _card(
        4, "qc", "交叉核验质控", "极其严苛的质控主任",
        "critical" if result["critical"] else ("warn" if result["conflict"] else "done"),
        result["headline"], points,
        {"qc": result},
        confidence=0.88,
    )


# ======================================================================
# Agent ⑤ 动态候诊监测
# ======================================================================
RECHECK_MINUTES = {1: 5, 2: 10, 3: 30, 4: 60, 5: 120}


def agent_watch(visit: dict[str, Any], store: Store, level: int) -> dict[str, Any]:
    interval = RECHECK_MINUTES.get(level, 30)
    interval_text = "持续监护（不设复评间隔）" if level == 1 else f"每 {interval} 分钟复评"
    snapshot = store.queue_snapshot(visit)
    pos = store.position(visit)
    points = [
        f"复评节奏：{interval_text}，到点自动提醒护士",
        f"当前队列位次：第 {pos} 位（前面 {snapshot['ahead_of']} 位）",
        "触发条件：扫码上报「更痛/喘不上气/出血/意识变化」→ 立即重新跑一遍 ②③④",
        "升级规则：新级别高于原级别时自动前移，并同步呼叫当班医生",
    ]
    return _card(
        5, "watch", "动态候诊监测", "极其尽责的巡视员", "watching",
        f"已纳入动态监测：{interval_text}，队列第 {pos} 位", points,
        {"interval": interval, "interval_text": interval_text, "queue": snapshot, "position": pos,
         "next_recheck_ts": time.time() + interval * 60},
        confidence=0.92,
    )


# ======================================================================
# Agent ⑥ 医患沟通安抚
# ======================================================================
def agent_comms(visit: dict[str, Any], store: Store, tone: str | None = None) -> dict[str, Any]:
    snapshot = store.queue_snapshot(visit)
    result = comms.wait_explanation(visit, snapshot, tone=tone)
    points = [
        f"当前推荐语气：{'暴躁版' if result['tone'] == 'angry' else '焦虑版' if result['tone'] == 'anxious' else '常规版'}",
        "已生成三版话术：常规版 / 焦虑版 / 暴躁版",
        f"沟通禁忌 {len(result['taboos'])} 条已内嵌，避免二次激化",
        "家属原话可现场输入，Agent 会识别情绪并改写话术",
    ]
    return _card(
        6, "comms", "医患沟通安抚", "极高情商的外交官", "done",
        "已生成排队解释话术（含 3 种语气 + 禁忌清单）", points,
        {"comms": result}, confidence=0.9,
    )


# ======================================================================
# 编排器
# ======================================================================
class Pipeline:
    def __init__(self, store: Store) -> None:
        self.store = store

    # ---------------------------------------------------------- 首次分诊
    def intake(self, payload: dict[str, Any]) -> dict[str, Any]:
        ctx = T.build_context(payload)
        visit = self.store.create_visit(ctx, payload)

        a1 = agent_intake(ctx, payload)
        screening = redflags.screen(ctx)
        a2 = agent_redflag(ctx, screening)
        judgment = esi.evaluate(ctx, screening)
        a3 = agent_judge(judgment)
        qc_result = qc.verify(ctx, screening, judgment)
        a4 = agent_qc(qc_result)

        # 安全原则：只自动升级，不自动降级
        final_level = min(judgment["level"], qc_result["final_level"]) if qc_result["conflict"] \
            else judgment["level"]

        self.store.update_visit(
            visit["id"],
            esi=final_level, final_level=final_level,
            stage="抢救" if final_level == 1 else "候诊",
            nrs=ctx.get("nrs"),
            screening={"flags": screening["flags"], "partial": screening["partial"],
                       "cleared": screening["cleared"], "screened": screening["screened"],
                       "headline": screening["headline"]},
            judgment=judgment, qc=qc_result,
        )
        visit = self.store.get_visit(visit["id"])  # 取回带 esi 的最新对象

        a5 = agent_watch(visit, self.store, final_level)
        a6 = agent_comms(visit, self.store)
        self.store.update_visit(visit["id"], watch=a5["extra"], comms=a6["extra"],
                                agents={"intake": a1, "redflag": a2, "judge": a3,
                                        "qc": a4, "watch": a5, "comms": a6})
        visit = self.store.get_visit(visit["id"])

        self._maybe_raise_tickets(visit, screening, qc_result, final_level)
        self.store.add_timeline(
            visit["id"],
            f"分诊完成：ESI {final_level} 级 · {'；'.join(f['name'] for f in screening['hard_flags'][:3]) or '未触发红旗'}",
        )
        return self.store.get_visit(visit["id"]) or visit

    def _maybe_raise_tickets(self, visit: dict[str, Any], screening: dict[str, Any],
                             qc_result: dict[str, Any], level: int) -> None:
        hard = screening["hard_flags"]
        raised_p0 = False
        # ① ESI 1 级 / 致命红旗 → P0 呼叫医生
        if level == 1:
            raised_p0 = True
            self.store.raise_ticket(
                visit, "red_flag",
                f"ESI 1 级立即抢救：{hard[0]['name'] if hard else '危重生命体征'}",
                [f"{f['name']}（{f['confidence']}）：{'、'.join(f['hits'][:3])}" for f in hard[:3]] +
                [f"生命体征：{visit['ctx']['vitals']}"],
                agent="Agent ②③",
            )
        elif hard and screening["max_tier"] == 0:
            raised_p0 = True
            self.store.raise_ticket(
                visit, "red_flag",
                f"致命病预警：{hard[0]['name']}（需医生 5 分钟内确认排雷）",
                [f"{f['name']}：{'、'.join(f['hits'][:3])}" for f in hard[:3]] +
                [f"下一步：{hard[0]['action']}"],
                agent="Agent ②",
            )
        # ② Agent 吵架 → P0 争议裁决
        if qc_result["conflict"] and level == qc_result["final_level"] and level < qc_result["judge_level"]:
            raised_p0 = True
            self.store.raise_ticket(
                visit, "conflict",
                f"Agent ③ 判 {qc_result['judge_level']} 级，Agent ④ 认为应为 {qc_result['final_level']} 级",
                [c["detail"] for c in (qc_result["critical"] + qc_result["major"])][:4],
                agent="Agent ③④",
            )
        # ③ 质控发现的数据缺口（如育龄女性未查 hCG）→ 必须由人补
        data_gaps = qc_result.get("data_critical", [])
        for gap in data_gaps:
            self.store.raise_ticket(
                visit, "missing_info", f"质控卡住：{gap['name']}",
                [gap["detail"], "该缺口不补齐不能安全放行"],
                agent="Agent ④", options=["已补查，重新评估", "医生接管并承担责任", "暂按现级别处理"],
            )
        # ④ 排雷缺关键证据 → P1 补数据（已经有 P0 或质控工单时不再重复弹窗）
        if screening["partial"] and not raised_p0 and not data_gaps:
            first = screening["partial"][0]
            self.store.raise_ticket(
                visit, "missing_info",
                f"排雷缺关键证据：{first['name']}",
                [f"缺：{'、'.join(first['missing'])}", "补齐后 Agent 会自动重评估"],
                agent="Agent ②", options=["已补齐，重评估", "暂不补齐，先按现级别处理", "医生接管"],
            )

    # ---------------------------------------------------------- 扫码上报 / 复评
    def recheck(self, visit: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
        if visit["stage"] == "已接管":
            return {"ok": False, "message": "该患者已由医生接管，Agent 停止自动干预"}

        kind = report.get("kind") or "other"
        nrs = report.get("nrs")
        text = (report.get("text") or "").strip()
        ctx = ctx_from_visit(visit)

        # 把这次上报合并进上下文
        if kind == "pain":
            if nrs is not None:
                ctx["nrs"] = max(ctx.get("nrs") or 0, int(nrs))
            ctx["associated"].add("near_syncope" if nrs and int(nrs) >= 9 else "nausea")
            ctx["text"] += " 疼痛加剧"
        elif kind == "dyspnea":
            ctx["terms"].add("dyspnea")
            if ctx["vitals"].get("spo2") is None:
                ctx["vitals"]["spo2"] = 93
        elif kind == "bleeding":
            ctx["terms"].add("active_bleeding")
        elif kind == "mental":
            ctx["terms"].add("altered_mental")
        elif kind == "fever":
            ctx["terms"].add("fever")
            ctx["vitals"]["t"] = max(ctx["vitals"].get("t") or 0, 39.0)
        if text:
            extra = T.normalize_text(text)
            ctx["terms"] |= {f["id"] for f in extra["findings"]}
            ctx["qualifiers"] |= {q["id"] for f in extra["findings"] for q in f["qualifiers"]}
            ctx["associated"] |= {a["id"] for a in extra["associated"]}
            ctx["findings"] = ctx["findings"] + extra["findings"]

        screening = redflags.screen(ctx)
        judgment = esi.evaluate(ctx, screening)
        qc_result = qc.verify(ctx, screening, judgment)
        new_level = min(judgment["level"], qc_result["final_level"]) if qc_result["conflict"] \
            else judgment["level"]
        old_level = visit.get("esi") or 5

        self.store.update_visit(
            visit["id"], nrs=ctx.get("nrs"), screening={"flags": screening["flags"],
                                                       "partial": screening["partial"],
                                                       "cleared": screening["cleared"],
                                                       "screened": screening["screened"],
                                                       "headline": screening["headline"]},
            judgment=judgment, qc=qc_result, ctx=self.store._slim_ctx(ctx),
        )
        visit = self.store.get_visit(visit["id"])

        visit.setdefault("reports", []).append({
            "at": store_now(), "kind": kind, "nrs": nrs, "text": text,
        })

        escalated = new_level < old_level
        if escalated:
            self.store.update_visit(visit["id"], esi=new_level, esi_prev=old_level,
                                    final_level=new_level,
                                    stage="抢救" if new_level == 1 else "候诊")
            visit = self.store.get_visit(visit["id"])
            a2 = agent_redflag(ctx, screening)
            a3 = agent_judge(judgment)
            a4 = agent_qc(qc_result)
            self.store.update_visit(visit["id"], agents={
                **(visit.get("agents") or {}), "redflag": a2, "judge": a3, "qc": a4,
            })
            self.store.raise_ticket(
                visit, "deterioration",
                f"候诊期间病情升级：ESI {old_level} → {new_level}",
                [f"患者上报：{report.get('label') or kind} {text}",
                 f"复评后触发：{screening['headline']}"],
                agent="Agent ⑤",
            )
            self.store.add_timeline(visit["id"], f"扫码上报触发升级：ESI {old_level} → {new_level}")
            message = comms.escalation_notice(visit, old_level, new_level)
        else:
            self.store.add_timeline(visit["id"], f"扫码上报（{kind}）复评完成，维持 ESI {old_level} 级")
            message = comms.escalation_notice(visit, old_level, new_level)

        # 重新计算复评时间
        watch = (visit.get("watch") or {})
        watch["next_recheck_ts"] = time.time() + RECHECK_MINUTES.get(new_level, 30) * 60
        watch["position"] = self.store.position(visit)
        self.store.update_visit(visit["id"], watch=watch)

        return {
            "ok": True, "escalated": escalated, "old_level": old_level, "new_level": new_level,
            "message": message, "screening": screening["headline"],
            "ticket": (visit.get("hitl") or [None])[-1],
        }

    # ---------------------------------------------------------- 巡视巡检
    def tick(self) -> list[str]:
        """后台巡检：复评超时 → 提醒护士 + 生成 P2 工单。"""
        events: list[str] = []
        ts = time.time()
        for v in list(self.store.visits.values()):
            if v["stage"] != "候诊":
                continue
            watch = v.get("watch") or {}
            due = watch.get("next_recheck_ts")
            if not due or ts < due:
                continue
            watch["next_recheck_ts"] = ts + RECHECK_MINUTES.get(v.get("esi") or 3, 30) * 60
            self.store.update_visit(v["id"], watch=watch)
            # 同一患者已经挂着待接管的"复评超时"工单时，不再重复写时间线、重复开单
            open_tickets = [t for t in self.store.tickets.values()
                            if t["visit_id"] == v["id"] and t["kind"] == "recheck_overdue"
                            and t["status"] == "待接管"]
            if open_tickets:
                continue
            overdue = int((ts - due) / 60)
            self.store.add_timeline(v["id"], f"Agent ⑤ 巡视：距上次复评已超时 {overdue} 分钟，提醒护士")
            self.store.raise_ticket(
                v, "recheck_overdue",
                f"候诊 {v.get('esi')} 级患者超过 {(v.get('watch') or {}).get('interval', 30)} 分钟未复评",
                [f"最后更新：{v['updated_at']}", "请护士到候诊区复测生命体征并重新评估疼痛"],
                agent="Agent ⑤",
            )
            events.append(f"{v['id']} {v['name']} 复评超时")
        return events


def store_now() -> str:
    from .store import now_iso
    return now_iso()
