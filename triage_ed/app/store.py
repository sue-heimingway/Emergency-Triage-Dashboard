"""状态存储：患者、工单、审计日志。JSON 落盘，进程重启不丢演示数据。

小体量演示不需要数据库，但必须做到三件事：
  1. 线程安全（HTTP 是多线程的）；
  2. 原子写（先写 .tmp 再 replace，避免半截文件）；
  3. 所有人工操作都进审计（HITL 的合规底线）。
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config

# 工单优先级与 SLA（分钟）
PRIORITY = {
    "red_flag": ("P0", 5),
    "conflict": ("P0", 5),
    "deterioration": ("P0", 5),
    "missing_info": ("P1", 15),
    "nurse_call": ("P1", 10),
    "recheck_overdue": ("P2", 30),
    "comms_risk": ("P1", 10),
}


def now_iso() -> str:
    return datetime.now().strftime("%m-%d %H:%M:%S")


def now_ts() -> float:
    return time.time()


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.DATA_FILE
        self.lock = threading.RLock()
        self.visits: dict[str, dict[str, Any]] = {}
        self.tickets: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []
        self.seq = 0
        self._load()

    # ------------------------------------------------------------ 持久化
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        self.visits = data.get("visits", {})
        self.tickets = data.get("tickets", {})
        self.audit = data.get("audit", [])[-300:]
        self.seq = data.get("seq", 0)

    def save(self) -> None:
        with self.lock:
            payload = {
                "visits": self.visits,
                "tickets": self.tickets,
                "audit": self.audit[-300:],
                "seq": self.seq,
                "saved_at": now_iso(),
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    # ------------------------------------------------------------ 患者
    def next_seq(self) -> int:
        with self.lock:
            self.seq += 1
            return self.seq

    def create_visit(self, ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            n = self.next_seq()
            visit = {
                "id": f"P-{n:04d}",
                "token": secrets.token_urlsafe(8),
                "name": ctx["name"],
                "age": ctx["age"],
                "sex": ctx["sex"],
                "chief": ctx["chief"],
                "stage": "分诊中",
                "esi": None,
                "esi_prev": None,
                "final_level": None,
                "created_at": now_iso(),
                "created_ts": now_ts(),
                "updated_at": now_iso(),
                "reports": [],
                "timeline": [{"at": now_iso(), "text": "患者到达，开始分诊"}],
                "hitl": [],
                "agents": {},
                "screening": {},
                "judgment": {},
                "qc": {},
                "comms": {},
                "watch": {},
                "ctx": self._slim_ctx(ctx),
                "source": payload.get("source", "manual"),
            }
            self.visits[visit["id"]] = visit
            self.save()
            return visit

    @staticmethod
    def _slim_ctx(ctx: dict[str, Any]) -> dict[str, Any]:
        """保存一份瘦身后的上下文（不存 set，方便 JSON）。"""
        return {
            "terms": sorted(ctx["terms"]),
            "qualifiers": sorted(ctx["qualifiers"]),
            "associated": sorted(ctx["associated"]),
            "history": sorted(ctx["history"]),
            "vitals": ctx["vitals"],
            "vital_flags": ctx.get("vital_flags") or [],
            "labs": ctx["labs"],
            "findings": ctx["findings"],
            "translation": ctx["translation"],
            "radiation": ctx["radiation"],
            "nrs": ctx["nrs"],
            "allergy": ctx["allergy"],
            "has_hcg": ctx["has_hcg"],
        }

    def get_visit(self, vid: str) -> dict[str, Any] | None:
        return self.visits.get(vid)

    def get_by_token(self, token: str) -> dict[str, Any] | None:
        for v in self.visits.values():
            if v["token"] == token:
                return v
        return None

    def update_visit(self, vid: str, **fields: Any) -> dict[str, Any] | None:
        with self.lock:
            visit = self.visits.get(vid)
            if not visit:
                return None
            visit.update(fields)
            visit["updated_at"] = now_iso()
            self.save()
            return visit

    def add_timeline(self, vid: str, text: str) -> None:
        with self.lock:
            visit = self.visits.get(vid)
            if not visit:
                return
            visit.setdefault("timeline", []).append({"at": now_iso(), "text": text})
            visit["updated_at"] = now_iso()
            self.save()

    # ------------------------------------------------------------ 队列
    @staticmethod
    def _sort_key(v: dict[str, Any]) -> tuple:
        return (v.get("esi") or 9, -(v.get("nrs") or 0), v.get("created_ts") or 0)

    def waiting(self) -> list[dict[str, Any]]:
        return sorted([v for v in self.visits.values() if v["stage"] == "候诊"], key=self._sort_key)

    def window(self) -> list[dict[str, Any]]:
        """候诊 + 抢救中，用于计算队列位置。"""
        return sorted([v for v in self.visits.values() if v["stage"] in ("候诊", "抢救")], key=self._sort_key)

    def position(self, visit: dict[str, Any]) -> int:
        for i, v in enumerate(self.window(), start=1):
            if v["id"] == visit["id"]:
                return i
        return 0

    def queue_snapshot(self, for_visit: dict[str, Any] | None = None) -> dict[str, Any]:
        window = self.window()
        critical = [v for v in window if (v.get("esi") or 9) <= 2]
        ahead = 0
        if for_visit:
            for v in window:
                if v["id"] == for_visit["id"]:
                    break
                ahead += 1
        names = []
        for v in critical:
            if for_visit and v["id"] == for_visit["id"]:
                continue
            flags = (v.get("screening") or {}).get("flags") or []
            names.append(flags[0]["name"] if flags else (v.get("chief") or "危重患者")[:12])
        return {
            "total": max(0, len(window) - (1 if for_visit else 0)),
            "critical": names[:5],
            "ahead_of": ahead,
            "waiting": len([v for v in window if v["stage"] == "候诊"]),
        }

    # ------------------------------------------------------------ 工单（HITL）
    def raise_ticket(self, visit: dict[str, Any], kind: str, reason: str,
                     evidence: list[str], options: list[str] | None = None,
                     agent: str = "") -> dict[str, Any]:
        priority, sla = PRIORITY.get(kind, ("P1", 15))
        with self.lock:
            n = self.next_seq()
            ticket = {
                "id": f"H-{n:04d}",
                "visit_id": visit["id"],
                "patient": visit["name"],
                "age": visit.get("age"),
                "sex": visit.get("sex"),
                "chief": visit.get("chief"),
                "esi": visit.get("esi"),
                "kind": kind,
                "priority": priority,
                "sla_minutes": sla,
                "reason": reason,
                "evidence": evidence,
                "options": options or ["同意分级", "升级为 1 级", "降级", "申请补充信息", "医生接管"],
                "agent": agent,
                "status": "待接管",
                "assignee": "",
                "decision": "",
                "note": "",
                "created_at": now_iso(),
                "created_ts": now_ts(),
                "due_ts": now_ts() + sla * 60,
                "closed_at": "",
                "timeline": [{"at": now_iso(), "text": f"Agent 发起工单（{priority}，SLA {sla} 分钟）：{reason}"}],
            }
            self.tickets[ticket["id"]] = ticket
            visit.setdefault("hitl", []).append(ticket["id"])
            self.save()
            self.log("hitl_raise", ticket["id"], visit["id"], reason)
            return ticket

    def get_ticket(self, tid: str) -> dict[str, Any] | None:
        return self.tickets.get(tid)

    def pending_tickets(self) -> list[dict[str, Any]]:
        order = {"P0": 0, "P1": 1, "P2": 2}
        return sorted([t for t in self.tickets.values() if t["status"] == "待接管"],
                      key=lambda t: (order.get(t["priority"], 9), t["created_ts"]))

    def ticket_action(self, tid: str, action: str, doctor: str = "值班医生",
                      note: str = "", level: int | None = None) -> dict[str, Any] | None:
        with self.lock:
            t = self.tickets.get(tid)
            if not t:
                return None
            t["status"] = "已接管" if action == "accept" else "已关闭"
            t["assignee"] = doctor
            t["decision"] = action
            t["note"] = note
            t["closed_at"] = now_iso()
            t["timeline"].append({"at": now_iso(), "text": f"{doctor} 执行：{action} {note}".strip()})
            visit = self.visits.get(t["visit_id"])
            if visit:
                if action == "accept":
                    visit["stage"] = "已接管"
                    visit["timeline"].append({"at": now_iso(), "text": f"{doctor} 已接管该患者，Agent 决策冻结"})
                elif level is not None:
                    visit["esi_prev"] = visit.get("esi")
                    visit["esi"] = level
                    visit["final_level"] = level
                    visit["stage"] = "抢救" if level == 1 else visit.get("stage", "候诊")
                    visit["timeline"].append({"at": now_iso(), "text": f"{doctor} 人工改判为 ESI {level} 级"})

            # 医生已处理该患者 → 顺带关闭他的"提醒类"低优先级工单，避免列表里挂着过时待办
            subsumed = {"recheck_overdue", "missing_info", "nurse_call", "comms_risk"}
            for other in self.tickets.values():
                if other["id"] == tid or other["status"] != "待接管" or other["visit_id"] != t["visit_id"]:
                    continue
                if other["kind"] in subsumed or action == "accept":
                    other["status"] = "已关闭"
                    other["assignee"] = doctor
                    other["decision"] = other["decision"] or "auto_close"
                    other["note"] = ((other["note"] + " ") if other["note"] else "") + "（医生处理该患者，自动关闭）"
                    other["closed_at"] = now_iso()
                    other["timeline"].append({"at": now_iso(), "text": f"{doctor} 处理该患者，本工单自动关闭"})

            self.save()
            self.log(f"hitl_{action}", tid, t["visit_id"], note or action)
            return t

    def overdue_tickets(self) -> list[dict[str, Any]]:
        ts = now_ts()
        return [t for t in self.tickets.values() if t["status"] == "待接管" and ts > t["due_ts"]]

    # ------------------------------------------------------------ 审计
    def log(self, action: str, ticket: str, visit_id: str, detail: str = "") -> None:
        with self.lock:
            self.audit.append({
                "at": now_iso(), "ts": now_ts(), "action": action,
                "ticket": ticket, "visit": visit_id, "detail": detail[:200],
            })
            self.audit = self.audit[-300:]

    def stats(self) -> dict[str, Any]:
        visits = list(self.visits.values())
        return {
            "total": len(visits),
            "waiting": len([v for v in visits if v["stage"] == "候诊"]),
            "critical": len([v for v in visits if (v.get("esi") or 9) == 1]),
            "pending": len(self.pending_tickets()),
            "taken_over": len([v for v in visits if v["stage"] == "已接管"]),
        }

    def reset(self) -> None:
        with self.lock:
            self.visits.clear()
            self.tickets.clear()
            self.audit.clear()
            self.seq = 0
            self.save()
