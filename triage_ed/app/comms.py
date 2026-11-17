"""Agent ⑥：医患沟通安抚 —— 极高情商的外交官。

急诊最危险的不是病，是情绪。家属爆炸通常只有三个原因：
  1. 不知道"为什么别人先进去"；
  2. 不知道"还要等多久"；
  3. 不知道"等的时候我该干什么"。

话术永远按这三件事组织：**给解释（分级规则）、给时间（明确节点）、给任务（红旗自查）**。
同一段话准备三种语气版本：常规版 / 焦虑版 / 暴躁版，由护士按现场情况挑。

三条铁律（写进 taboos，任何版本都不许破）：
  - 不承诺"马上就看"；
  - 不评价前面那位患者"其实没事"；
  - 不下诊断、不打保票。

注意：1 级患者已经在抢救序列里，此时再解释"前面还有谁"是反效果——
所以话术会按级别切换：高危/抢救患者讲"你已经被优先处理"，候诊患者才讲队列。
"""
from __future__ import annotations

from typing import Any

TONE_CUES = {
    "angry": ["投诉", "凭什么", "这么久", "你们医院", "什么破", "我要报警", "曝光", "找你们领导",
              "拍桌子", "骂", "滚", "死了人", "等着", "快点", "搞什么"],
    "anxious": ["会不会", "严重吗", "是不是", "要紧吗", "害怕", "担心", "能治好吗", "多久", "会不会死"],
    "skeptical": ["为什么", "凭什么", "是不是走后门", "插队", "不公平", "标准", "规定"],
}


def detect_tone(message: str) -> str:
    msg = (message or "").strip()
    if not msg:
        return "neutral"
    for tone in ("angry", "anxious", "skeptical"):
        if any(cue in msg for cue in TONE_CUES[tone]):
            return tone
    return "neutral"


# ---------------------------------------------------------------- 句子零件
def _queue_sentence(snapshot: dict[str, Any]) -> str:
    """把候诊队列翻译成一句人话：前面压着几个真正要命的。"""
    if not snapshot or not snapshot.get("total"):
        return "目前候诊区没有积压，你排在非常靠前的位置。"
    critical = snapshot.get("critical", [])
    if critical:
        names = "、".join(critical[:3])
        return (f"现在候诊区还有 {snapshot['total']} 位患者在等，其中 {len(critical)} 位是"
                f"最高级别的危重患者（{names}），他们需要立刻占用抢救室的医生和设备。")
    return f"现在候诊区还有 {snapshot['total']} 位患者在按分级顺序处理。"


def _level_sentence(level: int) -> str:
    return {
        1: "你的分级是 1 级——立即抢救级别，医生和护士已经在处理你，抢救床和设备是配给你的。",
        2: "你的分级是 2 级——高危，按规定 10 分钟内必须由医生接诊。",
        3: "你的分级是 3 级——紧急，医生会在 30 分钟内接诊，护士每 30 分钟会来看你一次。",
        4: "你的分级是 4 级——亚紧急，预计 60 分钟内接诊，可能需要先做检查再处理。",
        5: "你的分级是 5 级——非紧急，预计 2 小时内接诊，症状加重随时可以按铃。",
    }[level]


def _wait_sentence(visit: dict[str, Any], snapshot: dict[str, Any]) -> str:
    level = visit.get("esi", 3)
    if level == 1:
        return "你不需要排队，请不要自己下床走动；要上厕所或去检查，先叫护士。"
    if level == 2:
        return "请不要离开候诊区；如果 10 分钟内还没有医生来看你，请立刻按铃。"
    ahead = snapshot.get("ahead_of", 0)
    minutes = max(5, visit.get("wait_minutes", 30))
    return (f"你前面还有 {ahead} 位患者在等，按当前速度约 {minutes} 分钟轮到你。"
            f"如果中途送来 1 级患者，可能会临时延后，我们会主动告诉你。")


def _task_sentence(level: int) -> str:
    if level <= 2:
        return "期间如果出现新的不舒服（更痛、喘不上气、出血、意识不清），立刻按床头的呼叫铃，不要等。"
    return ("等待期间如果出现下面任何一种情况，请立刻做两件事：①按下候诊区的呼叫铃；"
            "②扫手腕上的二维码，点「更痛了」，系统会立即把你的顺序往前调。")


# ---------------------------------------------------------------- 主函数
def wait_explanation(visit: dict[str, Any], snapshot: dict[str, Any],
                     message: str = "", tone: str | None = None) -> dict[str, Any]:
    tone = tone or detect_tone(message)
    level = visit.get("esi", 3)
    q = _queue_sentence(snapshot)
    lv = _level_sentence(level)
    wt = _wait_sentence(visit, snapshot)
    task = _task_sentence(level)

    if level <= 2:
        variants = [
            {"tone": "neutral", "label": "常规版｜讲清规则",
             "text": f"{lv}{wt}\n\n{task}"},
            {"tone": "anxious", "label": "焦虑版｜先安抚再解释",
             "text": f"请放心，你现在就在最优先的序列里，我们一直在盯着你。{lv}{wt}\n\n{task}"},
            {"tone": "angry", "label": "暴躁版｜先降火再给事实",
             "text": (f"你的心情我完全理解。但有一件事必须先说清楚：{lv}"
                      f"{wt}\n\n{task}\n\n"
                      f"我现在能给你的承诺是：只要你按铃，我第一个到你床边。")},
        ]
    else:
        variants = [
            {"tone": "neutral", "label": "常规版｜讲清规则",
             "text": f"理解你的着急。{q}{lv}{wt}\n\n{task}"},
            {"tone": "anxious", "label": "焦虑版｜先安抚再解释",
             "text": (f"别担心，你人已经在医院了，我们每半小时会看一次你的指标。{q}{lv}{wt}\n\n"
                      f"只要有一项变差，护士会第一时间把你推进抢救室。{task}")},
            {"tone": "angry", "label": "暴躁版｜先降火再给事实",
             "text": (f"你的心情我完全理解，换成我家里人这样等，我也会急。今天我跟你说实话：{q}"
                      f"不是不给你看，是里面那两位如果现在离开抢救床，几分钟人就没了——"
                      f"你也不希望他们出事，对吗？\n\n{lv}{wt}\n\n{task}")},
        ]

    primary = next((v["text"] for v in variants if v["tone"] == tone), variants[0]["text"])

    return {
        "tone": tone,
        "primary": primary,
        "variants": variants,
        "taboos": ["不要说「马上就看」——做不到就是二次激化",
                   "不要说「前面那个人其实没事」——一旦传出去就是医疗纠纷",
                   "不要替前面的患者解释病情细节（隐私）",
                   "不要承诺「一定能治好」「不会有危险」"],
        "redlines": ["若家属出现拍桌、堵门、威胁，立即呼叫安保 + 上报医务处（HITL P0）",
                     "若家属在你面前说「我记住你了」，记录原话与时间，写进交接班"],
        "next_check": ("护士每 15 分钟主动报一次进度" if level >= 3
                       else "抢救期间由责任护士全程陪同，医生每 10 分钟向家属通报一次"),
    }


def escalation_notice(visit: dict[str, Any], old_level: int, new_level: int) -> str:
    if new_level < old_level:
        return (f"【情况升级】刚刚复评发现你的分级从 {old_level} 级升到 {new_level} 级，"
                f"已经把你调整到最前面，护士正在过来接你。请留在原地不要走动，深呼吸。")
    return (f"【情况已记录】复评完成，你的分级仍是 {new_level} 级，我们已通知当班医生。"
            f"如果症状继续加重，请再点一次按钮。")


def family_reply(message: str, visit: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    tone = detect_tone(message)
    intro = {
        "angry": "你说的我记下了。我们不解释立场，先把事实摆清楚：",
        "anxious": "别慌，我一条一条跟你说清楚：",
        "skeptical": "你问得对，我把标准直接给你看：",
        "neutral": "你好，情况我明白了，我说一下现在的安排：",
    }[tone]
    body = wait_explanation(visit, snapshot, message, tone)
    return {
        "tone": tone,
        "reply": intro + body["primary"],
        "suggest_variant": body["variants"],
        "taboos": body["taboos"],
    }
