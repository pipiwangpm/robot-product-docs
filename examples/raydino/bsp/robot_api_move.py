#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
robot_api_move.py —— 通过 Web 遥控台发运动命令的示例
用法:
    python3 robot_api_move.py fwd   --speed 0.2 --secs 1.0
    python3 robot_api_move.py back  --speed 0.1 --secs 0.5
    python3 robot_api_move.py left  --turn  0.5 --secs 0.5   # 正值 = 左转
    python3 robot_api_move.py right --turn  0.5 --secs 0.5
    python3 robot_api_move.py stop
    python3 robot_api_move.py interactive                    # 键盘遥控
说明:
    Web 台的 Mover 会以 HZ=10Hz 反复发 robot.move，secs 到点后自动补一帧
    全零停止；所以一次点击不用再额外 stop。
    interactive 模式退出时会显式 POST /api/stop，避免留下残余线程。
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
def post_json(url, payload, timeout=10.0):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"连接失败: {e.reason}") from e
def api_move(base, vx, vyaw, secs):
    return post_json(f"{base}/api/move", {"vx": vx, "vyaw": vyaw, "secs": secs})
def api_stop(base):
    return post_json(f"{base}/api/stop", {})
# ---------------------------------------------------------------------------
# 交互模式：termios cbreak + select，一按键立刻发送
# ---------------------------------------------------------------------------
def interactive(base, speed, turn, secs):
    import os
    import select
    import termios
    import tty
    mapping = {
        "w": ( speed,  0.0, "前进"),
        "W": ( speed,  0.0, "前进"),
        "s": (-speed,  0.0, "后退"),
        "S": (-speed,  0.0, "后退"),
        "a": ( 0.0,   turn, "左转"),
        "A": ( 0.0,   turn, "左转"),
        "d": ( 0.0,  -turn, "右转"),
        "D": ( 0.0,  -turn, "右转"),
    }
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        print("交互遥控（q 退出）:")
        print(f"  w 前进   s 后退   a 左转   d 右转   空格 停止")
        print(f"  参数: vx={speed} m/s  vyaw={turn} rad/s  secs={secs}")
        while True:
            r, _, _ = select.select([sys.stdin], [], [], 0.1)
            if not r:
                continue
            ch = sys.stdin.read(1)
            if ch in ("q", "Q", "\x03"):        # q 或 Ctrl+C
                break
            if ch == " ":
                try:
                    api_stop(base)
                    print("\n■ 已发送停止")
                except RuntimeError as e:
                    print(f"\n停止失败: {e}")
                continue
            if ch == "\x1b":                       # 方向键转义序列，忽略剩余两字节
                try:
                    sys.stdin.read(2)
                except Exception:
                    pass
                continue
            if ch in mapping:
                vx, vyaw, label = mapping[ch]
                try:
                    res = api_move(base, vx, vyaw, secs)
                    print(f"\n{label}: vx={res.get('vx')}  "
                          f"vyaw={res.get('vyaw')}  {res.get('secs')}s")
                except RuntimeError as e:
                    print(f"\n{label} 失败: {e}")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print("\n退出交互，发送停止")
        try:
            api_stop(base)
        except RuntimeError as e:
            print(f"停止失败: {e}")
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="通过 Web 遥控台发运动命令的示例")
    p.add_argument("direction",
                   choices=["fwd", "back", "left", "right", "stop", "interactive"])
    p.add_argument("--host", default="127.0.0.1", help="机器人 IP（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=8000, help="Web 台端口（默认 8000）")
    p.add_argument("--speed", type=float, default=0.2,
                   help="前进/后退速度 m/s（默认 0.2）")
    p.add_argument("--turn", type=float, default=0.5,
                   help="左/右转 vyaw rad/s（默认 0.5）")
    p.add_argument("--secs", type=float, default=1.0,
                   help="单次运动持续秒数（默认 1.0）")
    args = p.parse_args()
    base = f"http://{args.host}:{args.port}"
    try:
        if args.direction == "stop":
            print(api_stop(base))
        elif args.direction == "interactive":
            interactive(base, args.speed, args.turn, args.secs)
        else:
            vx = vyaw = 0.0
            if args.direction == "fwd":
                vx = args.speed
            elif args.direction == "back":
                vx = -args.speed
            elif args.direction == "left":
                vyaw = args.turn
            elif args.direction == "right":
                vyaw = -args.turn
            print(api_move(base, vx, vyaw, args.secs))
    except RuntimeError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
if __name__ == "__main__":
    main()
