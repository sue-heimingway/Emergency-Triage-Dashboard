"""端到端测试：真起一个 HTTP 服务，把六个 Agent 的完整链路跑一遍。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import demo_cases, server  # noqa: E402
from app.agents import Pipeline  # noqa: E402
from app.store import Store  # noqa: E402


class TestHTTPFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmpdir = ROOT / ".test-tmp"
        cls.tmpdir.mkdir(exist_ok=True)
        server.STORE = Store(cls.tmpdir / "state.json")
        server.PIPELINE = Pipeline(server.STORE)
        server.STORE.reset()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.httpd.shutdown()
        cls.httpd.server_close()
        for f in cls.tmpdir.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------ 工具
    def call(self, path: str, body: dict | None = None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data,
            method="POST" if body is not None else "GET",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as res:
            raw = res.read()
            ctype = res.headers.get("Content-Type", "")
            return res.status, (json.loads(raw.decode("utf-8")) if "json" in ctype else raw.decode("utf-8"))

    def case(self, key: str) -> dict:
        return {**next(c for c in demo_cases.CASES if c["key"] == key)["patient"]}

    # ------------------------------------------------------------ 用例
    def test_01_pages_render(self):
        for path in ("/", "/doctor", "/h5?t=none", "/static/style.css", "/static/app.js"):
            status, _ = self.call(path)
            self.assertEqual(status, 200, path)

    def test_02_triage_pipeline(self):
        status, v = self.call("/api/triage", self.case("dissection"))
        self.assertEqual(status, 200)
        self.assertEqual(v["esi"], 2)
        self.assertEqual(set(v["agents"]), {"intake", "redflag", "judge", "qc", "watch", "comms"})
        self.assertEqual(len(v["tickets"]), 1)
        self.assertEqual(v["tickets"][0]["priority"], "P0")
        self.assertIn("<svg", v["qr_svg"])
        self.assertIn("/h5?t=", v["h5_url"])
        # 六个 Agent 的卡片字段齐备
        for card in v["agents"].values():
            for field in ("no", "key", "name", "role", "status", "headline", "points"):
                self.assertIn(field, card)

    def test_03_qr_endpoint(self):
        _, v = self.call("/api/triage", self.case("acs"))
        status, svg = self.call(f"/api/visit/{v['id']}/qr.svg")
        self.assertEqual(status, 200)
        self.assertIn("<svg", svg)

    def test_04_h5_view_and_report_escalation(self):
        _, v = self.call("/api/triage", self.case("minor"))
        self.assertEqual(v["esi"], 4)
        status, h5 = self.call(f"/api/h5/{v['token']}")
        self.assertEqual(status, 200)
        self.assertNotIn("screening", h5)  # 患者端不暴露诊断结论

        status, r = self.call("/api/report", {
            "token": v["token"], "kind": "pain", "nrs": 9, "label": "疼痛加重", "text": "疼得受不了",
        })
        self.assertEqual(status, 200)
        self.assertTrue(r["escalated"])
        self.assertLess(r["new_level"], r["old_level"])
        self.assertIn("升级", r["message"])

        _, after = self.call(f"/api/visit/{v['id']}")
        self.assertEqual(after["esi"], 2)
        self.assertTrue(any(t["kind"] == "deterioration" for t in after["tickets"]))

    def test_05_hitl_takeover_freezes_agent(self):
        _, v = self.call("/api/triage", self.case("sepsis"))
        _, state = self.call("/api/state")
        ticket = next(t for t in state["tickets"] if t["visit_id"] == v["id"])
        status, res = self.call("/api/hitl", {
            "ticket_id": ticket["id"], "action": "accept", "doctor": "李医生", "note": "已到床边",
        })
        self.assertEqual(status, 200)
        self.assertEqual(res["visit"]["stage"], "已接管")
        self.assertEqual(res["ticket"]["status"], "已接管")

        status, blocked = self.call("/api/report", {
            "token": v["token"], "kind": "pain", "nrs": 10, "text": "更痛",
        })
        self.assertFalse(blocked["ok"])
        self.assertIn("接管", blocked["message"])

        _, audit = self.call("/api/audit")
        self.assertTrue(any(e["action"] == "hitl_accept" for e in audit["events"]))

    def test_06_doctor_override_upgrades(self):
        _, v = self.call("/api/triage", self.case("abdomen"))
        _, state = self.call("/api/state")
        ticket = next(t for t in state["tickets"] if t["visit_id"] == v["id"])
        status, res = self.call("/api/hitl", {
            "ticket_id": ticket["id"], "action": "upgrade", "level": 1,
            "doctor": "王医生", "note": "腹部体征明显，直接进抢救室",
        })
        self.assertEqual(status, 200)
        self.assertEqual(res["visit"]["esi"], 1)
        self.assertEqual(res["visit"]["stage"], "抢救")

    def test_07_cases_endpoint(self):
        status, data = self.call("/api/demo/cases")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(data["cases"]), 6)

    def test_08_tone_rewrite(self):
        _, v = self.call("/api/triage", self.case("dissection"))
        status, r = self.call("/api/report/tone", {
            "token": v["token"], "message": "等这么久凭什么？我要投诉你们！",
        })
        self.assertEqual(status, 200)
        self.assertEqual(r["tone"], "angry")
        self.assertTrue(r["reply"])


if __name__ == "__main__":
    unittest.main()
