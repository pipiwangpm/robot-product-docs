#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
robot_api_get_data.py —— 从机器人 Web 遥控台拉数据的示例
它演示这些 GET 接口：
    /api/version             Web 台版本 + dinosaur 主程序版本
    /api/info                设备信息（名称/IP/WiFi/手柄/摄像头端口）
    /api/status              当前运动状态 + 订阅连接 + CSV 状态
    /api/health              电池电压/电量、电机最高温度、CPU 温度、IMU ready、系统负载
    /api/telemetry           最新一帧的 IMU 姿态和每关节温度/电压/在线
    /api/subscribe?since=N   驱动状态增量流（轮询 since=seq，拿 seq 之后的新帧）
用法:
    python3 robot_api_get_data.py --mode info
    python3 robot_api_get_data.py --mode subscribe --secs 10
    python3 robot_api_get_data.py --mode all --host 192.168.1.100
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
def http_get_json(url, timeout=10.0):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"连接失败: {e.reason}") from e
# ---------------------------------------------------------------------------
# 各模式
# ---------------------------------------------------------------------------
def mode_version(base):
    v = http_get_json(base + "/api/version")
    w = v.get("web") or {}
    d = v.get("dinosaur") or {}
    print("=== /api/version ===")
    print(f"Web       : {w.get('name')} v{w.get('version')}  "
          f"编译于 {w.get('build_time')}  (python {w.get('python')})")
    print(f"dinosaur  : {d.get('version')}"
          + (f" (rev {d.get('revision')})" if d.get("revision") else ""))
    if d.get("robotd"):
        print(f"  robotd  : {d.get('robotd')}")
    if d.get("current"):
        print(f"  release : {d.get('current')}")
    print(f"  来源    : {d.get('source')}")
def mode_info(base):
    info = http_get_json(base + "/api/info")
    print("=== /api/info ===")
    print(f"恐龙名    : {info.get('dinosaur_name')}")
    ip = info.get("ip") or {}
    print(f"IP        : {ip.get('primary')}  (all={ip.get('all')})")
    print(f"hostname  : {ip.get('hostname')}")
    wifi = info.get("wifi") or {}
    print(f"WiFi MAC  : {wifi.get('mac')}  ({wifi.get('iface')})")
    pad = info.get("pad") or {}
    print(f"手柄 MAC  : {pad.get('mac')}  ({pad.get('name')})")
    cam = info.get("camera") or {}
    print(f"摄像头端口: {cam.get('port')}")
def mode_status(base):
    s = http_get_json(base + "/api/status")
    print("=== /api/status ===")
    print(f"运行中    : {s.get('running')}")
    print(f"vx / vyaw : {s.get('vx')} / {s.get('vyaw')}")
    print(f"剩余      : {s.get('remaining')} s")
    f = s.get("feed") or {}
    print(f"订阅连接  : {f.get('connected')}  err={f.get('error')!r}")
    c = s.get("csv") or {}
    print(f"CSV 开启  : {c.get('enabled')}")
    cur = c.get("current")
    if cur:
        print(f"CSV 文件  : {cur.get('name')}  行数={cur.get('rows')}  大小={cur.get('size')}")
def mode_health(base):
    h = http_get_json(base + "/api/health")
    print("=== /api/health ===")
    print(f"healthy  : {h.get('healthy')}")
    b = h.get("battery") or {}
    print(f"电池     : {b.get('volts')} V  {b.get('percent')}%")
    m = h.get("motors") or {}
    print(f"电机温度 : max={m.get('max_c')} °C  mean={m.get('mean_c')}  hottest={m.get('hottest')}")
    print(f"CPU 温度 : {h.get('cpu_temp_c')} °C")
    imu = h.get("imu") or {}
    print(f"IMU      : ready={imu.get('ready')}  stale={imu.get('consecutive_stale_blocks')}")
    sys_ = h.get("system") or {}
    print(f"内存     : {sys_.get('mem_used_kb')}/{sys_.get('mem_total_kb')} kB "
          f"({sys_.get('mem_pct')}%)")
    print(f"负载     : 1m={sys_.get('load_1m')}  5m={sys_.get('load_5m')}  "
          f"15m={sys_.get('load_15m')}")
    if h.get("error"):
        print(f"error    : {h.get('error')}")
def mode_telemetry(base):
    t = http_get_json(base + "/api/telemetry")
    print("=== /api/telemetry ===")
    if not t:
        print("尚无数据（等待 robot.subscribe 首帧）")
        return
    imu = t.get("imu") or {}
    print(f"IMU      : online={imu.get('online')}  "
          f"roll={imu.get('roll_deg')}  pitch={imu.get('pitch_deg')}  "
          f"yaw={imu.get('yaw_deg')}")
    print(f"           gravity={imu.get('gravity')}  "
          f"fallen={imu.get('fallen')}  limp={imu.get('limp')}")
    print()
    print(f"{'关节':<12} {'在线':<6} {'温度°C':>8} {'角度°':>9} {'电压V':>8}")
    for s in t.get("servos") or []:
        print(f"{s['name']:<12} {str(s['online']):<6} {str(s['temp_c']):>8} "
              f"{str(s['angle_deg']):>9} {str(s['volt_v']):>8}")
    rep = t.get("reported") or {}
    print()
    print(f"上报能力 : temps={rep.get('temps')}  volts={rep.get('volts')}  "
          f"online_explicit={rep.get('online_explicit')}")
def mode_subscribe(base, secs):
    print(f"=== /api/subscribe 轮询 {secs:.1f}s ===")
    since = 0
    deadline = time.time() + secs
    total = 0
    connected_seen = None
    while time.time() < deadline:
        try:
            d = http_get_json(f"{base}/api/subscribe?since={since}")
        except RuntimeError as e:
            print(f"请求失败: {e}")
            time.sleep(1.0)
            continue
        if connected_seen is None:
            connected_seen = d.get("connected")
            print(f"订阅连接 : {connected_seen}  err={d.get('error')!r}")
        items = d.get("items") or []
        seq = d.get("seq") or since
        for it in items:
            total += 1
            print(f"[{it['t']}] policy={it['policy']}  "
                  f"applied={it['applied']}  targets5={it['targets5']}")
            since = it["seq"]
        if not items:
            since = seq
        # 与服务端 pollFeed 同样的节奏，别把 daemon 打爆
        time.sleep(0.3)
    print(f"共收到 {total} 帧，最新 seq={since}")
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(description="从机器人 Web 遥控台拉数据的示例")
    p.add_argument("--host", default="127.0.0.1", help="机器人 IP（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=8000, help="Web 台端口（默认 8000）")
    p.add_argument("--mode", default="info",
                   choices=["version", "info", "status", "health", "telemetry",
                            "subscribe", "all"],
                   help="拉哪个接口（默认 info）")
    p.add_argument("--secs", type=float, default=5.0,
                   help="subscribe 模式持续秒数（默认 5）")
    args = p.parse_args()
    base = f"http://{args.host}:{args.port}"
    try:
        if args.mode in ("version", "all"):
            mode_version(base)
            print()
        if args.mode in ("info", "all"):
            mode_info(base)
            print()
        if args.mode in ("status", "all"):
            mode_status(base)
            print()
        if args.mode in ("health", "all"):
            mode_health(base)
            print()
        if args.mode in ("telemetry", "all"):
            mode_telemetry(base)
            print()
        if args.mode in ("subscribe", "all"):
            mode_subscribe(base, args.secs)
    except RuntimeError as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(1)
if __name__ == "__main__":
    main()
