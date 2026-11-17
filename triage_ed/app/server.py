"""零依赖 HTTP 服务（Python 标准库）。

页面：
  /           分诊工作台（护士用）
  /h5?t=xxx   患者扫码页（手机）
  /doctor     医生接管台（Human-in-the-loop）

接口：
  GET  /api/state            队列 + 工单 + 统计（前端每 2 秒轮询）
  GET  /api/visit/<id>       单个患者全景
  GET  /api/visit/<id>/qr.svg  离线二维码
  GET  /api/h5/<token>       患者端视图（不含诊断结论）
  POST /api/triage           接入并跑完 ①~⑥
  POST /api/report           扫码上报 → 触发 ⑤ 重新评估
  POST /api/hitl             医生接管 / 裁决
  POST /api/demo/seed        填充演示队列
  POST /api/reset            清空
"""
from __future__ import annotations

import json
import math
import mimetypes
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import comms, config, demo_cases, qr, redflags
from .agents import Pipeline
from .store import Store

STORE = Store()
PIPELINE = Pipeline(STORE)
_STARTED = time.time()


def lan_ips() -> list[str]:
    ips: set[str] = set()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127."):
                ips.add(ip)
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def _public_base(host_header: str) -> str:
    """算出二维码里应该写的地址：优先用访问者看到的 Host，其次退回局域网 IP。"""
    override = __import__("os").environ.get("TRIAGE_PUBLIC_URL")
    if override:
        return override.rstrip("/")
    host = host_header or f"127.0.0.1:{config.PORT}"
    if host.startswith("127.0.0.1") or host.startswith("localhost"):
        ips = lan_ips()
        if ips:
            port = host.rsplit(":", 1)[-1] if ":" in host else str(config.PORT)
            host = f"{ips[0]}:{port}"
    return f"http://{host}"


def _queue_payload(base: str) -> list[dict[str, Any]]:
    out = []
    now = time.time()
    for pos, v in enumerate(STORE.window(), start=1):
        flags = (v.get("screening") or {}).get("flags") or []
        out.append({
            "id": v["id"], "name": v["name"], "age": v.get("age"), "sex": v.get("sex"),
            "chief": (v.get("chief") or "")[:40],
            "esi": v.get("esi"), "stage": v["stage"], "position": pos,
            "flag": flags[0]["name"] if flags else "",
            "nrs": v.get("nrs"),
            "wait_minutes": max(0, int((now - v["created_ts"]) / 60)),
            "created_at": v["created_at"],
            "h5": f"{base}/h5?t={v['token']}",
            "qr": f"/api/visit/{v['id']}/qr.svg",
        })
    return out


def _state() -> dict[str, Any]:
    base = STORE.__dict__.setdefault("_base", f"http://127.0.0.1:{config.PORT}")
    tickets = []
    for t in STORE.pending_tickets():
        overdue = time.time() > t["due_ts"]
        tickets.append({**t, "overdue": overdue})
    return {
        "stats": STORE.stats(),
        "queue": _queue_payload(base),
        "tickets": tickets,
        "audit": STORE.audit[-24:][::-1],
        "cases": [{"key": c["key"], "title": c["title"], "tag": c["tag"]} for c in demo_cases.CASES],
        "meta": {
            "version": config.VERSION, "system": config.SYSTEM_NAME,
            "agents": 6, "screened": len(redflags.RULES), "esi_mode": config.ESI_MODE,
            "uptime": int(time.time() - _STARTED), "qr": qr.AVAILABLE,
            "disclaimer": config.DISCLAIMER,
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = f"TriageOS/{config.VERSION}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------ 基础
    def log_message(self, fmt: str, *args: Any) -> None:  # 演示时别刷屏
        pass

    def _send(self, body: bytes, ctype: str, status: int = 200, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict[str, Any] | list[Any], status: int = 200) -> None:
        self._send(json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", status)

    def _error(self, status: int, message: str) -> None:
        self._json({"ok": False, "error": message}, status)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("请求体不是合法 JSON")

    def _file(self, path: Path, ctype: str | None = None) -> None:
        if not path.exists():
            self._error(404, "not found")
            return
        ctype = ctype or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif path.suffix in (".css", ".js", ".svg"):
            ctype += "; charset=utf-8"
        self._send(path.read_bytes(), ctype, cache="no-cache")

    def _base(self) -> str:
        return _public_base(self.headers.get("Host", ""))

    # ------------------------------------------------ GET
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        STORE.__dict__["_base"] = self._base()

        if path in ("/", "/index.html"):
            return self._file(config.TEMPLATE_DIR / "index.html")
        if path in ("/h5", "/h5.html"):
            return self._file(config.TEMPLATE_DIR / "h5.html")
        if path in ("/doctor", "/doctor.html"):
            return self._file(config.TEMPLATE_DIR / "doctor.html")
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            target = (config.STATIC_DIR / name).resolve()
            if config.STATIC_DIR.resolve() not in target.parents:
                return self._error(403, "forbidden")
            return self._file(target)

        if path == "/api/state":
            return self._json(_state())
        if path == "/api/demo/cases":
            return self._json({"cases": demo_cases.CASES})
        if path == "/health":
            return self._json({"ok": True, "version": config.VERSION, "qr": qr.AVAILABLE,
                               "esi_mode": config.ESI_MODE, "store": len(STORE.visits)})
        if path.startswith("/api/visit/"):
            parts = path.strip("/").split("/")
            vid = parts[2]
            visit = STORE.get_visit(vid)
            if not visit:
                return self._error(404, "患者不存在")
            if len(parts) == 3:
                return self._json(self._full_visit(visit))
            if len(parts) == 4 and parts[3] == "qr.svg":
                url = f"{self._base()}/h5?t={visit['token']}"
                svg = qr.svg(url)
                if not svg:
                    return self._error(503, "二维码组件不可用")
                return self._send(svg.encode("utf-8"), "image/svg+xml; charset=utf-8")
        if path.startswith("/api/h5/"):
            token = path.rsplit("/", 1)[-1]
            visit = STORE.get_by_token(token)
            if not visit:
                return self._error(404, "链接无效或已过期")
            return self._json(self._h5_view(visit))
        if path == "/api/audit":
            return self._json({"events": STORE.audit[-60:][::-1]})
        return self._error(404, "not found")

    def _full_visit(self, visit: dict[str, Any]) -> dict[str, Any]:
        base = self._base()
        return {
            **visit,
            "position": STORE.position(visit),
            "h5_url": f"{base}/h5?t={visit['token']}",
            "qr_svg": qr.svg(f"{base}/h5?t={visit['token']}"),
            "tickets": [STORE.tickets[t] for t in visit.get("hitl", []) if t in STORE.tickets],
        }

    def _h5_view(self, visit: dict[str, Any]) -> dict[str, Any]:
        """患者端只给"我该知道"的信息：位次、预计时间、注意事项。不给诊断结论。"""
        snapshot = STORE.queue_snapshot(visit)
        pos = STORE.position(visit)
        esi_level = visit.get("esi") or 3
        comms_view = comms.wait_explanation(visit, snapshot)
        return {
            "id": visit["id"], "name": visit["name"], "stage": visit["stage"],
            "esi": esi_level, "position": pos, "ahead": snapshot["ahead_of"],
            "wait_minutes": config.ESI_WAIT_TARGET.get(esi_level, 30),
            "total_waiting": snapshot["total"],
            "queue": snapshot,
            "notice": comms_view["primary"],
            "taboos": comms_view["taboos"],
            "last_report": (visit.get("reports") or [None])[-1],
            "updated_at": visit["updated_at"],
            "taken_over": visit["stage"] == "已接管",
        }

    # ------------------------------------------------ POST
    def do_POST(self) -> None:
        path = urlparse(self.path).path
        STORE.__dict__["_base"] = self._base()
        try:
            body = self._body()
        except ValueError as exc:
            return self._error(400, str(exc))

        try:
            if path == "/api/triage":
                chief = (body.get("chief") or "").strip()
                if not chief:
                    return self._error(400, "主诉不能为空")
                visit = PIPELINE.intake(body)
                return self._json(self._full_visit(visit))

            if path == "/api/report":
                token = body.get("token") or ""
                visit = STORE.get_by_token(token)
                if not visit:
                    return self._error(404, "链接无效或已过期")
                result = PIPELINE.recheck(visit, body)
                return self._json(result)

            if path == "/api/hitl":
                tid = body.get("ticket_id") or ""
                action = body.get("action") or ""
                if action not in ("accept", "agree", "upgrade", "downgrade", "missing", "takeover"):
                    return self._error(400, "未知的处置动作")
                level = None
                if action == "upgrade":
                    level = int(body.get("level") or 1)
                elif action == "downgrade":
                    level = int(body.get("level") or 3)
                    if level <= 1:
                        return self._error(400, "降级不能降到 1 级，请直接用「接管」")
                mapped = "accept" if action in ("accept", "takeover") else action
                ticket = STORE.ticket_action(tid, mapped, body.get("doctor") or "值班医生",
                                             (body.get("note") or "").strip(), level)
                if not ticket:
                    return self._error(404, "工单不存在")
                visit = STORE.get_visit(ticket["visit_id"])
                return self._json({"ok": True, "ticket": ticket,
                                   "visit": self._full_visit(visit) if visit else None})

            if path == "/api/call-doctor":
                visit = STORE.get_visit(body.get("visit_id") or "")
                if not visit:
                    return self._error(404, "患者不存在")
                note = (body.get("note") or "").strip() or "需人工复核"
                ticket = STORE.raise_ticket(
                    visit, "nurse_call", f"护士站呼叫医生接管：{note}",
                    [f"当前分级 ESI {visit.get('esi')}", f"主诉：{(visit.get('chief') or '')[:40]}"],
                    agent="护士站",
                )
                return self._json({"ok": True, "ticket": ticket})

            if path == "/api/demo/seed":
                created = []
                for case in demo_cases.CASES:
                    if case["key"] in ("minor",):
                        continue
                    visit = PIPELINE.intake({**case["patient"], "source": "seed"})
                    created.append({"id": visit["id"], "name": visit["name"], "esi": visit["esi"]})
                return self._json({"ok": True, "created": created})

            if path == "/api/reset":
                STORE.reset()
                return self._json({"ok": True})

            if path == "/api/report/tone":
                # 家属原话 → 情绪识别 + 话术改写（不算业务主线，给沟通 Agent 单独用）
                token = body.get("token") or ""
                visit = STORE.get_by_token(token)
                if not visit:
                    return self._error(404, "患者不存在")
                snapshot = STORE.queue_snapshot(visit)
                result = comms.family_reply(body.get("message") or "", visit, snapshot)
                return self._json(result)

            return self._error(404, "not found")
        except KeyError as exc:
            self._error(404, f"对象不存在: {exc}")
        except Exception as exc:  # 演示环境：打日志、返回可读错误
            import traceback
            traceback.print_exc()
            self._error(500, f"服务异常: {exc}")


class _Server(ThreadingHTTPServer):
    """演示用的 HTTP 服务：安静、能复用端口、不因浏览器提前断开而刷堆栈。"""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):  # noqa: D401
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return  # 浏览器刷新/关闭连接，属正常现象
        super().handle_error(request, client_address)


def _monitor_loop() -> None:
    while True:
        try:
            PIPELINE.tick()
        except Exception:  # 巡检线程不能死
            import traceback
            traceback.print_exc()
        time.sleep(config.MONITOR_TICK)


def _service_alive(port: int) -> bool:
    """端口上是不是已经有一个"我们自己的"服务在跑（防止双击两次起两个实例）。"""
    import json as _json
    import urllib.request as _req

    for host in ("127.0.0.1", "localhost"):
        try:
            with _req.urlopen(f"http://{host}:{port}/health", timeout=0.8) as res:
                data = _json.loads(res.read().decode("utf-8"))
                if data.get("ok") and "version" in data:
                    return True
        except Exception:
            continue
    return False


def serve(host: str | None = None, port: int | None = None, open_browser: bool = True) -> None:
    host = host or config.HOST
    port = port or config.PORT

    if _service_alive(port):
        print()
        print("=" * 62)
        print(f" {port} 端口上已经有一个 TriageOS 在运行了，不用再启动一次。")
        print(f" 直接打开：http://127.0.0.1:{port}/")
        print(" 想重启的话：先关掉之前那个黑色窗口，再双击 启动.bat")
        print("=" * 62)
        if open_browser:
            try:
                import webbrowser
                webbrowser.open(f"http://127.0.0.1:{port}/")
            except Exception:
                pass
        return

    try:
        httpd = _Server((host, port), Handler)
    except OSError as exc:
        print()
        print("=" * 62)
        print(f" 启动失败：端口 {port} 被别的程序占用了（{exc}）")
        print(f" 换个端口启动 ——  py -3 run.py --port {port + 10}")
        print("=" * 62)
        raise SystemExit(1)
    threading.Thread(target=_monitor_loop, daemon=True).start()

    print("=" * 62)
    print(f" {config.SYSTEM_NAME} · {config.SYSTEM_SUB}  v{config.VERSION}")
    print("=" * 62)
    print(f" 分诊工作台  http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/")
    print(f" 医生接管台  http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/doctor")
    print(f" 患者扫码页  http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/h5")
    if host in ("0.0.0.0", "::", ""):
        print(" 手机访问（需同一 Wi-Fi）：")
        for ip in lan_ips():
            print(f"   http://{ip}:{port}/h5")
    else:
        print(" 手机扫码提示：用 --lan 启动，二维码会自动指向局域网 IP")
    print(f" 二维码组件   {'OK（离线生成）' if qr.AVAILABLE else '不可用（只显示链接）'}")
    print(f" ESI 制式     {'标准五级制' if config.ESI_MODE == '5' else '简化四级制'}")
    print(" 浏览器没自动打开？把上面的地址复制到浏览器地址栏即可。")
    print(f" {config.DISCLAIMER}")
    print(" 按 Ctrl+C 停止")
    print("=" * 62)

    if open_browser:
        try:
            import webbrowser
            threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()
