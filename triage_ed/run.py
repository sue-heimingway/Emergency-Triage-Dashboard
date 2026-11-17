"""急诊分诊决策中枢 · 启动入口

    python run.py                 # 本机演示，自动开浏览器
    python run.py --lan           # 允许手机扫码（同一 Wi-Fi）
    python run.py --port 8771     # 换端口
    python run.py --esi 4         # 界面主用简化四级制
    python run.py --no-browser
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def main() -> None:
    # 控制台编码五花八门（GBK / UTF-8 / 437），宁可显示成问号也不要崩
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass

    if sys.version_info < (3, 10):
        print(f"[需要 Python 3.10 或更高版本] 当前是 {sys.version.split()[0]}")
        print("下载地址：https://www.python.org/downloads/")
        raise SystemExit(1)

    parser = argparse.ArgumentParser(description="TriageOS 急诊分诊决策中枢")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--lan", action="store_true", help="绑定 0.0.0.0，便于手机扫码")
    parser.add_argument("--esi", default=None, choices=["4", "5"], help="界面主用四级制还是五级制")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if args.esi:
        os.environ["TRIAGE_ESI_MODE"] = args.esi
    if args.lan:
        os.environ["TRIAGE_HOST"] = "0.0.0.0"

    from app.server import serve  # noqa: E402  (导入前先落环境变量)

    serve(args.host, args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
