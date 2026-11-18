#!/usr/bin/env python3
"""
从 duck robot 的 8443 端口（webrtcsink 信令服务器）获取视频流。
用法:
    # 预览视频窗口
    python3 scripts/receive_video.py <robot_host>
    # 保存到 MP4 文件
    python3 scripts/receive_video.py <robot_host> --output output.mp4
    # 保存为 JPEG 帧序列
    python3 scripts/receive_video.py <robot_host> --save-frames ./frames/
依赖:
    pip install aiortc av opencv-python websockets
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import signal
import sys
from pathlib import Path
from typing import Optional
import av
import cv2
import websockets
from aiortc import (
    RTCIceCandidate,
    RTCIceServer,
    RTCConfiguration,
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
)
# ─── 状态 ───────────────────────────────────────────────────────────────
class State:
    def __init__(self) -> None:
        self.pc: Optional[RTCPeerConnection] = None
        self.session_id: Optional[str] = None
        self._frame_queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        self._ice_outbox: asyncio.Queue[str] = asyncio.Queue()
    def put_frame(self, frame) -> None:
        # 满了就丢最旧的，保持最新
        try:
            self._frame_queue.put_nowait(frame)
        except asyncio.QueueFull:
            try:
                self._frame_queue.get_nowait()
                self._frame_queue.put_nowait(frame)
            except asyncio.QueueEmpty:
                pass
    async def get_frame(self):
        return await self._frame_queue.get()
    def enqueue_ice(self, candidate_str: str) -> None:
        """本地 ICE 候选入出箱，信令循环会把它发给服务器。"""
        self._ice_outbox.put_nowait(candidate_str)
    async def close(self) -> None:
        if self.pc is not None:
            try:
                await self.pc.close()
            except Exception:
                pass
            self.pc = None
state = State()
shutdown = asyncio.Event()
# ─── 信令 ───────────────────────────────────────────────────────────────
def _ice_candidate_to_sdp_str(cand: RTCIceCandidate) -> str:
    """把 aiortc 的 RTCIceCandidate 还原成 `candidate:...` 字符串。"""
    parts = [
        "candidate:", str(cand.foundation), str(cand.component),
        cand.protocol, str(cand.priority), cand.ip, str(cand.port),
        "typ", cand.type,
    ]
    if cand.relatedAddress is not None and cand.relatedPort is not None:
        parts += ["raddr", cand.relatedAddress, "rport", str(cand.relatedPort)]
    if cand.tcpType is not None:
        parts += ["tcptype", cand.tcpType]
    return " ".join(parts)
def _parse_ice_candidate(sdp_str: str, sdp_mid: str | None, sdp_mline_index: int | None) -> RTCIceCandidate | None:
    """把 RFC 8445 的 candidate: 行解析成 aiortc 1.10.1 的 RTCIceCandidate。
    格式: candidate:<foundation> <component> <protocol> <priority> <ip> <port>
          typ <cand-type> [raddr <rel-ip> rport <rel-port>] [tcptype <tcp-type>] ...
    返回 None 代表这条不能解析（比如空 candidate 或 end-of-candidates marker）。
    """
    body = sdp_str.strip()
    if body.startswith("candidate:"):
        body = body[len("candidate:"):]
    if not body:
        # 空 candidate 行 — webrtcsink 偶尔会发
        return None
    parts = body.split()
    if len(parts) < 7:
        print(f"[ice] !! candidate 格式太短，跳过: {sdp_str[:120]}")
        return None
    # 关键字段（按顺序）
    foundation = parts[0]
    component = int(parts[1])
    protocol = parts[2]
    priority = int(parts[3])
    ip = parts[4]
    port = int(parts[5])
    # 找 "typ" 位置，type 在其后
    try:
        typ_idx = parts.index("typ")
    except ValueError:
        print(f"[ice] !! 找不到 typ 关键字，跳过: {sdp_str[:120]}")
        return None
    cand_type = parts[typ_idx + 1]
    # 扩展字段（raddr/rport/tcptype）
    related_address = None
    related_port = None
    tcp_type = None
    i = typ_idx + 2
    while i < len(parts):
        if parts[i] == "raddr" and i + 1 < len(parts):
            related_address = parts[i + 1]
        elif parts[i] == "rport" and i + 1 < len(parts):
            related_port = int(parts[i + 1])
        elif parts[i] == "tcptype" and i + 1 < len(parts):
            tcp_type = parts[i + 1]
        i += 1
    return RTCIceCandidate(
        component=component,
        foundation=foundation,
        ip=ip,
        port=port,
        priority=priority,
        protocol=protocol,
        type=cand_type,
        relatedAddress=related_address,
        relatedPort=related_port,
        sdpMid=sdp_mid,
        sdpMLineIndex=sdp_mline_index,
        tcpType=tcp_type,
    )
async def connect_signalling(host: str, port: int):
    """连接到 webrtcsink 信令服务器并处理整个握手流程。"""
    url = f"ws://{host}:{port}"
    print(f"[signalling] 连接 {url}")
    async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
        print("[signalling] 已连接，等待 welcome…")
        async for raw in ws:
            msg = json.loads(raw)
            mtype = msg.get("type")
            print(f"[signalling] ← {mtype}",
                  msg.get("producers") and f"({len(msg['producers'])} producers)" or "",
                  msg.get("sessionId") and f"sid={msg['sessionId'][:8]}…" or "")
            if mtype == "welcome":
                # 请求 producers 列表
                await ws.send(json.dumps({"type": "list"}))
            elif mtype == "list":
                producers = msg.get("producers") or []
                if not producers:
                    print("[signalling] !! 没有 producer — mediad 可能未在运行或 pipeline 未到 PLAYING")
                    return
                producer = producers[0]
                meta = producer.get("meta") or {}
                print(f"[signalling] 选择 producer id={producer['id'][:12]}… name={meta.get('name', '?')}")
                await ws.send(json.dumps({
                    "type": "startSession",
                    "peerId": producer["id"],
                }))
            elif mtype == "sessionStarted":
                state.session_id = msg["sessionId"]
                # 配 Google 免费 STUN，才能 gather srflx（NAT 穿透）和 host candidate
                config = RTCConfiguration(iceServers=[
                    RTCIceServer(urls="stun:stun.l.google.com:19302"),
                    RTCIceServer(urls="stun:stun1.l.google.com:19302"),
                ])
                state.pc = RTCPeerConnection(config)
                print(f"[signalling] session 建立: {state.session_id[:12]}…")
                @state.pc.on("track")
                def on_track(track):
                    print(f"[webrtc] track 到达: {track.kind} id={track.id[:12]}…")
                    if track.kind == "video":
                        asyncio.create_task(_pull_video_frames(track))
                @state.pc.on("connectionstatechange")
                def on_state():
                    print(f"[webrtc] connection state: {state.pc.connectionState}")
            elif mtype == "peer":
                if state.pc is None:
                    print("[signalling] !! peer 消息在 sessionStarted 之前到达")
                    continue
                try:
                    if msg.get("sdp"):
                        sdp = msg["sdp"]
                        print(f"[signalling] → setRemoteDescription({sdp['type']}), "
                              f"length={len(sdp['sdp'])}")
                        await state.pc.setRemoteDescription(
                            RTCSessionDescription(sdp["sdp"], sdp["type"])
                        )
                        print("[signalling] → createAnswer…")
                        answer = await state.pc.createAnswer()
                        # answer SDP 默认发 setup:active（想当 DTLS client），
                        # 但我们是 ICE controlled（远端 offer 是 controlling），
                        # 所以必须是 DTLS server，改成 passive。
                        answer_sdp = answer.sdp.replace("a=setup:active", "a=setup:passive")
                        # H.264 profile-level-id 统一用远端 offer 里的值（42c028），
                        # aiortc 默认填 42e01f，虽然兼容但远端可能发的 SPS/PPS 按
                        # 自己的参数编码，导致 aiortc 解码报 "non-existing PPS 0"。
                        answer_sdp = answer_sdp.replace(
                            "profile-level-id=42e01f",
                            "profile-level-id=42c028",
                        )
                        # 去掉 RTX（retransmission）codec line —— 它只是用来重传丢包的，
                        # aiortc 会尝试给 "video/rtx" 建解码器然后崩溃
                        lines = [l for l in answer_sdp.split("\n")
                                 if "rtx/" not in l and "apt=96" not in l and "apt=97" not in l]
                        answer_sdp = "\n".join(lines)
                        answer = RTCSessionDescription(answer_sdp, answer.type)
                        print("[signalling] → setLocalDescription…")
                        await state.pc.setLocalDescription(answer)
                        # aiortc 1.10.1 的 icecandidate 事件是坏的，但
                        # setLocalDescription 内部已经同步跑完 gather()，
                        # 所以 transceivers 上的 iceGatherer 已经有 candidates。
                        # 先发送 answer SDP，让远端知道 DTLS role 协商结果，
                        # 然后再发所有本地 ICE candidates。
                        await ws.send(json.dumps({
                            "type": "peer",
                            "sessionId": state.session_id,
                            "sdp": {"type": "answer", "sdp": answer.sdp},
                        }))
                        local_candidates = []
                        for tr in state.pc.getTransceivers():
                            ice_gatherer = tr._transport._transport.iceGatherer
                            for cand in ice_gatherer.getLocalCandidates():
                                local_candidates.append(cand)
                        print(f"[ice] → 本地 {len(local_candidates)} 条（手动 gather）")
                        for cand in local_candidates:
                            sdp_str = _ice_candidate_to_sdp_str(cand)
                            await ws.send(json.dumps({
                                "type": "peer",
                                "sessionId": state.session_id,
                                "ice": {"candidate": sdp_str},
                            }))
                        print("[signalling] → answer + ICE 已发送")
                    elif msg.get("ice"):
                        ice = msg["ice"]
                        raw = ice["candidate"]
                        if not raw or "typ" not in raw:
                            print("[ice] 跳过空 candidate")
                            continue
                        print(f"[ice] ← {raw[:80]}")
                        cand = _parse_ice_candidate(
                            raw,
                            sdp_mid=ice.get("sdpMid"),
                            sdp_mline_index=ice.get("sdpMLineIndex"),
                        )
                        if cand is None:
                            print("[ice] 跳过无效 candidate")
                            continue
                        try:
                            master_transport = state.pc.getTransceivers()[0]._transport._transport
                            await master_transport.addRemoteCandidate(cand)
                            print(f"[ice] +{cand.type} {cand.ip}:{cand.port} "
                                  f"→ state={master_transport.state}")
                        except Exception as exc:
                            print(f"[ice] !! addRemoteCandidate 失败: {exc}")
                        # 远端 webrtcsink 不发 end-of-candidates marker，
                        # 远端 ICE 是快速连续到达的，所以收到第一条就立刻 trigger
                        # connect() —— 后续到达的远端 ICE 会被 aioice 动态配对进
                        # check_list。远端 2s 后会 endSession，不能等。
                        if not hasattr(state, "_connect_pending"):
                            state._connect_pending = True
                            asyncio.create_task(_trigger_connect_now())
                except Exception as exc:
                    import traceback
                    print(f"[signalling] !! peer 处理失败: {exc}")
                    traceback.print_exc()
            elif mtype == "sessionEnded":
                reason = msg.get("reason", "")
                print(f"[signalling] session 结束 {reason}")
                break
            elif mtype == "endSession":
                reason = msg.get("reason", "")
                print(f"[signalling] endSession 收到 {reason}")
                break
            elif mtype == "error":
                print(f"[signalling] !! error: {msg.get('details')}")
            elif mtype == "sessionRejected":
                print(f"[signalling] !! session 被拒绝: {msg.get('reason')}")
                break
    await state.close()
# ─── 视频帧提取 ─────────────────────────────────────────────────────────
async def _trigger_connect_now():
    """绕开 aiortc 1.10.1 的时序 bug：手动让 ICE + DTLS + RTP 都启动。"""
    await asyncio.sleep(0.1)
    if state.pc is None or shutdown.is_set():
        return
    try:
        master_transport = state.pc.getTransceivers()[0]._transport._transport
        dtls_transport = state.pc.getTransceivers()[0]._transport
        transceiver = state.pc.getTransceivers()[0]
        remote_params = state.pc._RTCPeerConnection__remoteIce.get(transceiver)
        remote_dtls_params = state.pc._RTCPeerConnection__remoteDtls.get(transceiver)
        conn = master_transport._connection
        # 1. 设置 remote ICE params（ufrag/pwd）
        if remote_params and remote_params.usernameFragment:
            conn.remote_username = remote_params.usernameFragment
            conn.remote_password = remote_params.password
            conn.remote_is_lite = remote_params.iceLite
        # 2. 手动触发 ICE connectivity check（aiortc 的 start() 被幂等保护挡了）
        print(f"[ice] connect() remote={len(conn.remote_candidates)} local={len(conn.local_candidates)}")
        await conn.connect()
        print(f"[ice] !! ICE OK → state={master_transport.state}")
        # 3. 启动 DTLS（我们已经改了 SDP setup:active→passive，所以我们是 DTLS server）
        if dtls_transport.state == "new" and remote_dtls_params:
            print(f"[dtls] start → ", end="", flush=True)
            await dtls_transport.start(remote_dtls_params)
            print(f"state={dtls_transport.state}")
        # 4. 启动 RTP receiver
        if transceiver.receiver.track is not None and transceiver.currentDirection in ("recvonly", "sendrecv"):
            remote_rtp = state.pc._RTCPeerConnection__remoteRtp(transceiver)
            print(f"[rtp] receiver.receive() 启动")
            asyncio.create_task(transceiver.receiver.receive(remote_rtp))
        print(f"[webrtc] all started, CS={state.pc.connectionState} ICEgath={state.pc.iceGatheringState}")
    except Exception as exc:
        print(f"[!!] connect 链失败: {exc}")
        import traceback
        traceback.print_exc()
async def _pull_video_frames(track: VideoStreamTrack):
    """持续从 WebRTC video track 拉帧，送入队列。"""
    try:
        while not shutdown.is_set():
            frame = await track.recv()
            state.put_frame(frame)
    except Exception as exc:
        print(f"[video] track 结束: {exc}")
# ─── 输出模式 ──────────────────────────────────────────────────────────
async def output_display():
    """OpenCV 窗口预览。按 q 或 Esc 退出。"""
    print("[output] OpenCV 预览窗口已打开，按 q 关闭")
    while not shutdown.is_set():
        frame = await state.get_frame()
        # aiortc VideoFrame → numpy (BGR)
        img = frame.to_ndarray(format="bgr24")
        cv2.imshow("duck video", img)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
    cv2.destroyAllWindows()
async def output_save_mp4(path: str):
    """保存为 H.264 MP4。"""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 等待第一帧拿到尺寸
    frame = await state.get_frame()
    w, h = frame.width, frame.height
    print(f"[output] 保存到 {out_path}  ({w}x{h})")
    container = av.open(str(out_path), mode="w")
    stream = container.add_stream("libx264", rate=30)
    stream.width = w
    stream.height = h
    stream.pix_fmt = "yuv420p"
    stream.options = {"preset": "veryfast", "crf": "23"}
    n = 0
    try:
        while not shutdown.is_set():
            try:
                frame = await asyncio.wait_for(state.get_frame(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            img = frame.to_ndarray(format="yuv420p")
            pkt = stream.encode(av.VideoFrame.from_ndarray(img, format="yuv420p"))
            if pkt:
                container.mux(pkt)
            n += 1
            if n % 30 == 0:
                print(f"[output] 已写入 {n} 帧", end="\r")
    finally:
        try:
            container.close()
        except Exception:
            pass
        print(f"\n[output] 完成，共 {n} 帧")
async def output_save_frames(dir_path: str):
    """保存为 JPEG 帧序列。"""
    out_dir = Path(dir_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[output] 保存帧到 {out_dir}/")
    n = 0
    try:
        while not shutdown.is_set():
            frame = await state.get_frame()
            img = frame.to_ndarray(format="bgr24")
            cv2.imwrite(str(out_dir / f"frame_{n:06d}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            n += 1
            if n % 30 == 0:
                print(f"[output] 已保存 {n} 帧", end="\r")
    finally:
        print(f"\n[output] 完成，共 {n} 帧")
# ─── 入口 ─────────────────────────────────────────────────────────────
def _install_signal_handlers() -> None:
    def _sig(signum, _frame):
        print(f"\n[shutdown] 收到信号 {signum}")
        shutdown.set()
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)
async def main() -> int:
    ap = argparse.ArgumentParser(description="从 duck robot 的 8443 端口获取 WebRTC 视频流")
    ap.add_argument("host", help="机器人 IP 或 hostname (如 duck.local 或 192.168.x.x)")
    ap.add_argument("--port", type=int, default=8443, help="信令服务器端口 (默认 8443)")
    ap.add_argument("--output", "-o", help="输出 MP4 路径 (如 video.mp4)")
    ap.add_argument("--save-frames", "-f", help="保存 JPEG 帧序列到该目录")
    ap.add_argument("--duration", "-t", type=float, help="录制/预览时长 (秒)，不指定则直到手动停止")
    args = ap.parse_args()
    _install_signal_handlers()
    tasks = []
    # 信令任务
    sig_task = asyncio.create_task(connect_signalling(args.host, args.port))
    tasks.append(sig_task)
    # 输出任务
    if args.output:
        out_task = asyncio.create_task(output_save_mp4(args.output))
    elif args.save_frames:
        out_task = asyncio.create_task(output_save_frames(args.save_frames))
    else:
        out_task = asyncio.create_task(output_display())
    tasks.append(out_task)
    # 时长限制
    if args.duration:
        def _time_up():
            print(f"\n[shutdown] 时长 {args.duration}s 到期")
            shutdown.set()
        loop = asyncio.get_running_loop()
        loop.call_later(args.duration, _time_up)
    try:
        # 等 shutdown 或第一个任务完成
        await shutdown.wait()
        # 给正在进行的帧处理一点时间收尾
        await asyncio.sleep(0.2)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await state.close()
    print("[done]")
    return 0
if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
