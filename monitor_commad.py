# @time     : 2025/4/19 下午8:18
"""
Monitor.py - 屏幕广播带宽监控工具
功能：
1. 实时监测视频流和音频流带宽
2. 独立线程抓取网络数据包
3. 控制台动态刷新显示统计信息
"""

import socket
import time
from threading import Thread, Lock
import sys

# ============================== 全局配置 ==============================
VIDEO_PORT = 22222  # 视频传输端口（与发送端一致）
AUDIO_PORT = 22223  # 音频传输端口（与发送端一致）
REFRESH_INTERVAL = 1  # 统计刷新间隔（秒）

# ============================== 全局变量 ==============================
video_bytes = 0  # 视频流量计数器
audio_bytes = 0  # 音频流量计数器
lock = Lock()  # 线程安全锁
running = True  # 程序运行状态标志


# ============================== 数据包捕获线程 ==============================
def capture_packets(port, counter):
    """通用数据包捕获线程
    port: 监听的端口号
    counter: 要更新的计数器变量（'video'或'audio'）
    """
    global running
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        sock.bind(('', port))  # 绑定所有接口
    except OSError as e:
        print(f"端口 {port} 绑定失败: {e}")
        return

    while running:
        try:
            data, _ = sock.recvfrom(65535)  # 接收最大UDP数据包
            with lock:
                if counter == 'video':
                    globals()['video_bytes'] += len(data)
                else:
                    globals()['audio_bytes'] += len(data)
        except Exception as e:
            if running:
                print(f"端口 {port} 接收错误: {e}")
            break
    sock.close()


# ============================== 控制台显示模块 ==============================
def print_stats():
    """控制台动态刷新显示统计信息"""
    last_video = 0
    last_audio = 0

    while running:
        time.sleep(REFRESH_INTERVAL)

        # 计算差值
        with lock:
            delta_video = video_bytes - last_video
            delta_audio = audio_bytes - last_audio
            last_video = video_bytes
            last_audio = audio_bytes

        # 计算带宽速率
        video_kbps = delta_video / 1024 / REFRESH_INTERVAL
        audio_kbps = delta_audio / 1024 / REFRESH_INTERVAL
        total_kbps = video_kbps + audio_kbps

        # 构建输出字符串
        sys.stdout.write("\r" + " " * 80 + "\r")  # 清空当前行
        stats = [
            f"视频带宽: {video_kbps:6.1f} KB/s",
            f"音频带宽: {audio_kbps:6.1f} KB/s",
            f"总带宽: {total_kbps:6.1f} KB/s"
        ]
        sys.stdout.write(" | ".join(stats))
        sys.stdout.flush()


# ============================== 主控制逻辑 ==============================
def main():
    global running

    # 启动捕获线程
    Thread(target=capture_packets, args=(VIDEO_PORT, 'video')).start()
    Thread(target=capture_packets, args=(AUDIO_PORT, 'audio')).start()

    # 启动显示线程
    Thread(target=print_stats).start()

    # 等待退出指令
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        running = False
        print("\n监控已停止")


if __name__ == '__main__':
    main()
