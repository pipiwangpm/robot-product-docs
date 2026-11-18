#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
robot_api_skill.py —— 通过 Web 遥控台触发动作技能的示例
用法:
    python3 robot_api_skill.py list                 # 列出脚本内置的常用技能
    python3 robot_api_skill.py sit_toggle
    python3 robot_api_skill.py ground_pick
    python3 robot_api_skill.py kick_right
    python3 robot_api_skill.py kick_left
    python3 robot_api_skill.py roll_over
    python3 robot_api_skill.py <任意技能名>          # 技能名直接透传给 robotd
说明:
    请求走 POST /api/skill，服务端再把 {"skill": ...} 走 robot.do 推给 robotd。
    服务端响应结构：
        成功 → {"ok": true,  "message": "...", "result": {...}}
        失败 → {"ok": false, "error": "...", "result": {...}}
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
KNOWN_SKILLS = {
    "sit_toggle": "坐下 / 站立（切换）",
    "ground_pick": "从地上捡东西",
    "kick_right": "右踢腿",
    "kick_left": "左踢腿",
    "roll_over": "翻滚",
}
def post_json(url, payload, timeout=15.0):
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
def list_skills():
    print("脚本内置常用技能：")
    for name, desc in KNOWN_SKILLS.items():
        print(f"  {name:<14} {desc}")
    print()
    print("其它技能名也可以直接传：python3 robot_api_skill.py <技能名>")
def run_skill(base, skill):
    resp = post_json(f"{base}/api/skill", {"skill": skill})
    if resp.get("ok"):
        print(f"[OK] {skill}: {resp.get('message') or '已接受'}")
        result = resp.get("result") or {}
        if result:
            print(f"   result: {result}")
    else:
        print(f"[ERROR] {skill}: {resp.get('error') or resp.get('message') or '失败'}",
              file=sys.stderr)
        result = resp.get("result") or {}
        if result:
            print(f"   result: {result}", file=sys.stderr)
        sys.exit(1)
def main():
    p = argparse.ArgumentParser(description="通过 Web 遥控台触发动作技能的示例")
    p.add_argument("skill", nargs="?",
                   help="技能名；或 'list' 列出内置技能")
    p.add_argument("--host", default="127.0.0.1", help="机器人 IP（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=8000, help="Web 台端口（默认 8000）")
    args = p.parse_args()
    if not args.skill or args.skill == "list":
        list_skills()
        return
    base = f"http://{args.host}:{args.port}"
    try:
        run_skill(base, args.skill)
    except RuntimeError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
if __name__ == "__main__":
    main()
