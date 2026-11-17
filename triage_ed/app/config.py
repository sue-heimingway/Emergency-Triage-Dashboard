"""全局配置：路径、端口、版本号。

刻意保持零依赖：只使用 Python 标准库。
如果以后要接大模型，只需在 comms/agents 里读下面两个环境变量，
不配置也能完整跑通（本项目默认走确定性规则引擎）。
"""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = BASE_DIR / "data"
DATA_FILE = DATA_DIR / "state.json"

VERSION = "1.0.0"
SYSTEM_NAME = "TriageOS"
SYSTEM_SUB = "急诊分诊决策中枢"
DISCLAIMER = "演示原型：仅用于流程与规则演练，不构成临床决策依据，不用于真实诊疗。"

HOST = os.environ.get("TRIAGE_HOST", "127.0.0.1")
PORT = int(os.environ.get("TRIAGE_PORT", "8770"))

# ESI 显示制式：5 = 标准 ESI v4（1~5 级）；4 = 简化四级制（5 级并入 4 级候诊）
ESI_MODE = os.environ.get("TRIAGE_ESI_MODE", "5")

# 可选大模型（不配置也不影响任何功能）
LLM_API_KEY = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")

# 候诊监测的巡检间隔（秒）。演示用可以调小，真实场景建议 30~60。
MONITOR_TICK = float(os.environ.get("TRIAGE_TICK", "3"))

ESI_WAIT_TARGET = {1: 0, 2: 10, 3: 30, 4: 60, 5: 120}
ESI_LABEL = {
    1: "立即抢救",
    2: "高危候诊·10 分钟内",
    3: "紧急候诊·30 分钟内",
    4: "亚紧急候诊",
    5: "非紧急候诊",
}
ESI_COLOR = {1: "#b91c1c", 2: "#ea580c", 3: "#ca8a04", 4: "#2563eb", 5: "#059669"}
