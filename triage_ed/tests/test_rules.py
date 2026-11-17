"""规则层单元测试：术语库 / 红旗 / ESI / 质控。

运行：python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import esi, qc, redflags, terminology as T  # noqa: E402


def ctx(**kw):
    payload = {"name": "测试", "age": 50, "sex": "男", "chief": ""}
    payload.update(kw)
    return T.build_context(payload)


class TestTerminology(unittest.TestCase):
    def test_colloquial_to_term(self):
        r = T.normalize_text("胸口像被刀割一样疼")
        self.assertEqual([f["id"] for f in r["findings"]], ["chest_pain"])
        self.assertIn("撕裂样", r["findings"][0]["standard"])

    def test_negation(self):
        r = T.normalize_text("没有胸痛，就是有点喘不上气")
        ids = {f["id"] for f in r["findings"]}
        self.assertNotIn("chest_pain", ids)
        self.assertIn("dyspnea", ids)

    def test_unrelieved_chest_pain_is_not_negated(self):
        r = T.normalize_text("胸痛持续不缓解")
        self.assertIn("chest_pain", {f["id"] for f in r["findings"]})

    def test_vitals_extraction(self):
        v = T.parse_vitals("血压 88/54，心率 122，体温 39.2，血氧 91%")
        self.assertEqual((v["bp_sys"], v["bp_dia"], v["p"], v["t"], v["spo2"]), (88, 54, 122, 39.2, 91))

    def test_vital_level_red(self):
        flags = T.vital_level({"bp_sys": 78, "p": 130, "spo2": 88, "r": 31})
        self.assertEqual({f["level"] for f in flags}, {"red"})

    def test_lab_parse_critical(self):
        labs = T.parse_labs("肌钙蛋白I 0.82 ng/mL ↑\n血钾 6.2 mmol/L")
        by_id = {l["id"]: l for l in labs}
        self.assertEqual(by_id["ctni"]["flag"], "危急")
        self.assertEqual(by_id["k"]["flag"], "危急")

    def test_duration_and_radiation(self):
        r = T.normalize_text("胸口撕着疼，放射到后背，疼了2个小时")
        self.assertEqual(r["findings"][0]["duration"], "2小时")
        self.assertIn("后背", r["radiation"])

    def test_form_vitals_override_text(self):
        c = ctx(chief="胸口疼，血压 150/90", bp_sys=90, bp_dia=60)
        self.assertEqual(c["vitals"]["bp_sys"], 90)


class TestRedFlags(unittest.TestCase):
    def test_dissection(self):
        c = ctx(age=58, sex="男",
                chief="胸口像被刀割一样疼，撕到后背，一身大汗",
                history_text="高血压十年", bp_sys=168, bp_dia=95)
        s = redflags.screen(c)
        ids = [f["id"] for f in s["flags"]]
        self.assertIn("aortic_dissection", ids)
        self.assertEqual(s["max_tier"], 0)

    def test_stroke_detected(self):
        c = ctx(age=68, sex="女", chief="突然说话说不清楚，右边胳膊抬不起来")
        s = redflags.screen(c)
        self.assertIn("stroke", [f["id"] for f in s["flags"]])

    def test_ectopic_needs_hcg(self):
        c = ctx(age=26, sex="女", chief="停经45天，下面出血，右下腹疼，眼前发黑")
        s = redflags.screen(c)
        self.assertIn("ectopic", [f["id"] for f in s["flags"]])
        self.assertTrue(any("hCG" in m for p in s["partial"] for m in p["missing"]))

    def test_no_false_anaphylaxis(self):
        """低血压 + 晕厥 不该被判成过敏性休克。"""
        c = ctx(age=26, sex="女", chief="肚子疼，晕倒了一次", bp_sys=88, p=124)
        s = redflags.screen(c)
        self.assertNotIn("anaphylaxis", [f["id"] for f in s["flags"]])

    def test_pe_needs_respiratory_clue(self):
        c = ctx(age=58, chief="胸口刀割样疼", lab_text="D-二聚体 6.4 mg/L FEU ↑")
        s = redflags.screen(c)
        self.assertNotIn("pulmonary_embolism", [f["id"] for f in s["flags"]])

    def test_not_everything_is_red(self):
        c = ctx(age=22, chief="切菜切到手指了，流了点血，已经止住了")
        s = redflags.screen(c)
        self.assertEqual(s["flags"], [])


class TestESI(unittest.TestCase):
    def _judge(self, c):
        return esi.evaluate(c, redflags.screen(c))

    def test_shock_is_level1(self):
        c = ctx(age=76, chief="发烧三天，叫不太醒", bp_sys=86, p=126, r=26, spo2=92, t=39.6)
        self.assertEqual(self._judge(c)["level"], 1)

    def test_high_risk_is_level2(self):
        c = ctx(age=58, chief="胸口刀割样疼，撕到后背", history_text="高血压", bp_sys=168)
        self.assertEqual(self._judge(c)["level"], 2)

    def test_minor_is_level4(self):
        c = ctx(age=22, chief="切菜切到手指了，流了点血，已经止住了",
                bp_sys=118, bp_dia=74, p=78, r=16, t=36.6, spo2=99)
        j = self._judge(c)
        self.assertEqual(j["level"], 4)
        self.assertEqual(j["level_4"], 4)

    def test_no_resources_is_level5(self):
        c = ctx(age=30, chief="有点头晕，想量个血压", bp_sys=118, p=72, r=16, t=36.6, spo2=99)
        self.assertEqual(self._judge(c)["level"], 5)

    def test_danger_vitals_uprate_to_2(self):
        c = ctx(age=40, chief="肚子疼，拉肚子", p=112, bp_sys=108, r=22, spo2=95, t=37.2,
                lab_text="白细胞 12.1 ×10^9/L")
        self.assertEqual(self._judge(c)["level"], 2)

    def test_level4_mapping(self):
        c = ctx(age=30, chief="有点头晕", bp_sys=118, p=72, r=16, t=36.6, spo2=99)
        j = self._judge(c)
        self.assertEqual(j["level"], 5)
        self.assertEqual(j["level_4"], 4)


class TestQC(unittest.TestCase):
    def test_catches_missed_hcg(self):
        c = ctx(age=26, sex="女", chief="停经45天，右下腹疼", bp_sys=120, p=96, r=18, t=36.8, spo2=98)
        s = redflags.screen(c)
        j = esi.evaluate(c, s)
        verdict = qc.verify(c, s, j)
        self.assertIn("hcg_gap", [x["id"] for x in verdict["checks"]])
        self.assertFalse(verdict["conflict"])  # 数据缺口不等于级别的分歧
        self.assertEqual(verdict["verdict"], "带条件放行")

    def test_catches_under_triage(self):
        c = ctx(age=58, chief="胸口刀割样疼，撕到后背", bp_sys=160, p=98, r=20, t=36.8, spo2=96)
        s = redflags.screen(c)
        wrong = {"level": 4, "summary": "被判 4 级", "danger_vitals": []}
        verdict = qc.verify(c, s, wrong)
        self.assertTrue(verdict["conflict"])
        self.assertLessEqual(verdict["final_level"], 2)
        self.assertIn("missed_red_flag", [x["id"] for x in verdict["checks"]])

    def test_over_triage_flagged(self):
        c = ctx(age=30, chief="有点头晕", bp_sys=118, p=72, r=16, t=36.6, spo2=99)
        s = redflags.screen(c)
        inflated = {"level": 1, "summary": "被判 1 级", "danger_vitals": []}
        verdict = qc.verify(c, s, inflated)
        self.assertIn("over_triage", [x["id"] for x in verdict["checks"]])


if __name__ == "__main__":
    unittest.main()
