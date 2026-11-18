import sys
import time
from vl53l5cx.vl53l5cx import VL53L5CX
from vl53l5cx.api import VL53L5CX_RESOLUTION_8X8
# ============ 可调参数 ============
I2C_BUS      = 3
GRID         = 8          # 8x8 = 64 区域
POLL_DT      = 0.05       # 轮询周期(秒)
HUE_NEAR     = 0.0        # 近 -> 红 (hue 0)
HUE_FAR      = 240.0      # 远 -> 蓝 (hue 240)
VALID_STATUS = (5, 9)     # 有效目标状态码
MIN_SPAN     = 100        # 动态范围保护(mm)
# ============ 颜色工具 ============
def hsv_to_rgb(h, s=1.0, v=1.0):
    h %= 360.0
    c = v * s
    x = c * (1.0 - abs((h / 60.0) % 2.0 - 1.0))
    m = v - c
    if   h <  60: r, g, b = c, x, 0.0
    elif h < 120: r, g, b = x, c, 0.0
    elif h < 180: r, g, b = 0.0, c, x
    elif h < 240: r, g, b = 0.0, x, c
    elif h < 300: r, g, b = x, 0.0, c
    else:         r, g, b = c, 0.0, x
    return int((r + m) * 255), int((g + m) * 255), int((b + m) * 255)
def color_of(t):
    """t=0 最近(红) -> t=1 最远(蓝)，返回 ANSI 前景色前缀。"""
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    r, g, b = hsv_to_rgb(HUE_NEAR + (HUE_FAR - HUE_NEAR) * t)
    return f"\033[38;2;{r};{g};{b}m"
RESET = "\033[0m"
DIM   = "\033[90m"
def cell(dist, ok, lo, hi):
    """生成一个固定宽度的带色单元格，保持 8x8 位置不变。"""
    if not ok:
        return f"{DIM}  ---{RESET}"
    span = hi - lo
    t = 0.0 if span <= 0 else (dist - lo) / span
    return f"{color_of(t)}{dist:5d}{RESET}"
# ============ 初始化 ============
print(f"检查 i2c-{I2C_BUS} 上的 0x29 ...")
tof = VL53L5CX(bus_id=I2C_BUS)
if not tof.is_alive():
    print(f"失败: 0x29 在 i2c-{I2C_BUS} 上没有应答")
    raise SystemExit(1)
print("初始化传感器（上传固件，约数秒）...")
try:
    tof.init()
except Exception as e:
    print(f"初始化失败: {e}")
    raise SystemExit(1)
tof.set_resolution(VL53L5CX_RESOLUTION_8X8)
tof.set_ranging_frequency_hz(15)
tof.start_ranging()
# ============ 主循环：彩色矩阵 ============
sys.stdout.write("\033[2J\033[H")   # 清屏 + 光标归位
sys.stdout.flush()
try:
    while True:
        if not tof.check_data_ready():
            time.sleep(POLL_DT)
            continue
        data   = tof.get_ranging_data()
        dist   = list(data.distance_mm)      # 长度 64，行优先
        status = list(data.target_status)
        ok    = [s in VALID_STATUS and d > 0 for d, s in zip(dist, status)]
        valid = [d for d, o in zip(dist, ok) if o]
        # 动态范围：让颜色对比度最大化
        if valid:
            lo, hi = min(valid), max(valid)
            if hi - lo < MIN_SPAN:
                mid = (lo + hi) / 2.0
                lo, hi = mid - MIN_SPAN / 2, mid + MIN_SPAN / 2
        else:
            lo, hi = 0, 1000
        # 组装一帧画面（一次性输出，避免闪烁）
        out = ["\033[H"]
        for row in range(GRID):
            line = " ".join(
                cell(dist[row * GRID + col], ok[row * GRID + col], lo, hi)
                for col in range(GRID)
            )
            out.append(line + "\033[K")     # \033[K 清到行尾
        out.append("\033[K")
        nearest = min(valid) if valid else None
        out.append(
            f"有效 {len(valid):2d}/64   最近 "
            f"{nearest if nearest is not None else '--'} mm   "
            f"色标 \033[38;2;255;0;0m红=近{RESET} → "
            f"\033[38;2;0;0;255m蓝=远{RESET}   "
            f"范围 {lo:.0f}~{hi:.0f} mm\033[K"
        )
        out.append("\033[J")                # 清掉屏幕残留
        sys.stdout.write("\n".join(out))
        sys.stdout.flush()
        time.sleep(POLL_DT)
except KeyboardInterrupt:
    print("\n停止测距...")
finally:
    tof.stop_ranging()
    sys.stdout.write("\033[0m完成\n")
    sys.stdout.flush()
