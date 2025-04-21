"""
MonitorGUI_Enhanced.py - 增强版带宽监控器（新增流量趋势图）
新增功能：
1. 可折叠趋势图
2. Canvas绘制三色曲线
3. 动态数据历史记录
"""

import socket
import time
import math
from threading import Thread, Lock
import tkinter as tk
from tkinter import ttk

# ============================== 全局配置 ==============================
VIDEO_PORT = 22222
AUDIO_PORT = 22223
REFRESH_INTERVAL = 1000
HISTORY_SIZE = 60  # 60秒历史数据

# 颜色配置
COLORS = {
    'video': '#FF4444',
    'audio': '#44AAFF',
    'total': '#44FF44'
}

# ============================== 全局变量 ==============================
video_bytes = 0
audio_bytes = 0
lock = Lock()
running = True

# 新增历史数据存储
history = {
    'video': [],
    'audio': [],
    'total': []
}

# ============================== 数据捕获线程（保持不变） ==============================
class PacketCapture(Thread):
    def __init__(self, port, counter):
        super().__init__()
        self.port = port
        self.counter = counter
        self.daemon = True  # 设置为守护线程

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind(('', self.port))
            print(f"开始监听端口 {self.port}")
        except OSError as e:
            print(f"端口 {self.port} 绑定失败: {e}")
            return

        while running:
            try:
                data, _ = sock.recvfrom(65535)
                with lock:
                    if self.counter == 'video':
                        globals()['video_bytes'] += len(data)
                    else:
                        globals()['audio_bytes'] += len(data)
            except Exception as e:
                if running:
                    print(f"端口 {self.port} 接收错误: {e}")
                break
        sock.close()
# ============================== 增强版GUI类 ==============================
class EnhancedMonitor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("带宽监控器+")
        self.geometry("420x150")
        self.resizable(False, False)
        self.graph_visible = False  # 趋势图可见状态

        # 新增组件
        self.btn_toggle = None
        self.canvas = None
        self.graph_frame = None

        # 初始化界面
        self.create_base_ui()
        self.create_graph_ui()

        # 启动数据刷新
        self.after(REFRESH_INTERVAL, self.update_data)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_base_ui(self):
        """创建基础界面组件"""
        style = ttk.Style()
        style.configure("Title.TLabel", font=('Helvetica', 12, 'bold'))
        style.configure("Data.TLabel", font=('Consolas', 14))

        # 主显示区域
        ttk.Label(self, text="实时带宽监控", style="Title.TLabel").pack(pady=5)

        data_frame = ttk.Frame(self)
        data_frame.pack(pady=10)

        self.lbl_video = ttk.Label(data_frame, style="Data.TLabel")
        self.lbl_video.grid(row=0, column=0, padx=20)

        self.lbl_audio = ttk.Label(data_frame, style="Data.TLabel")
        self.lbl_audio.grid(row=0, column=1, padx=20)

        self.lbl_total = ttk.Label(data_frame, style="Data.TLabel")
        self.lbl_total.grid(row=0, column=2, padx=20)

        # 底部状态栏
        bottom_frame = ttk.Frame(self)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.status = ttk.Label(bottom_frame, text="监控运行中...")
        self.status.pack(side=tk.LEFT, padx=5)

        # 新增切换按钮
        self.btn_toggle = ttk.Button(
            bottom_frame,
            text="显示趋势图",
            command=self.toggle_graph,
            width=12
        )
        self.btn_toggle.pack(side=tk.RIGHT, padx=5)

    def create_graph_ui(self):
        """创建趋势图组件"""
        self.graph_frame = ttk.Frame(self)

        # 创建Canvas
        self.canvas = tk.Canvas(
            self.graph_frame,
            width=420,
            height=200,
            bg='white'
        )
        self.canvas.pack(pady=5)

        # 图例
        legend_frame = ttk.Frame(self.graph_frame)
        legend_frame.pack()
        for i, (key, color) in enumerate(COLORS.items()):
            ttk.Label(
                legend_frame,
                text=key,
                foreground=color,
                font=('Arial', 9)
            ).grid(row=0, column=i, padx=15)

    def toggle_graph(self):
        """切换趋势图显示状态"""
        self.graph_visible = not self.graph_visible

        if self.graph_visible:
            self.geometry("420x380")  # 展开高度
            self.graph_frame.pack(fill=tk.X, pady=5)
            self.btn_toggle.config(text="隐藏趋势图")
            self.draw_graph()  # 立即绘制
        else:
            self.geometry("420x150")
            self.graph_frame.pack_forget()
            self.btn_toggle.config(text="显示趋势图")

    def update_data(self):
        """更新数据并记录历史"""
        global history

        if running:
            with lock:
                current_video = video_bytes
                current_audio = audio_bytes

            interval = REFRESH_INTERVAL / 1000
            video_kbps = (current_video - getattr(self, 'last_video', 0)) / 1024 / interval
            audio_kbps = (current_audio - getattr(self, 'last_audio', 0)) / 1024 / interval
            total_kbps = video_kbps + audio_kbps

            # 更新最后记录值
            self.last_video = current_video
            self.last_audio = current_audio

            # 更新显示
            self.lbl_video.config(text=f"视频:\n{video_kbps:.1f} KB/s")
            self.lbl_audio.config(text=f"音频:\n{audio_kbps:.1f} KB/s")
            self.lbl_total.config(text=f"总计:\n{total_kbps:.1f} KB/s")

            # 记录历史数据
            for key in history:
                history[key].append(locals()[f"{key}_kbps"])
                if len(history[key]) > HISTORY_SIZE:
                    history[key].pop(0)

            # 重绘趋势图
            if self.graph_visible:
                self.draw_graph()

            self.after(REFRESH_INTERVAL, self.update_data)

    def draw_graph(self):
        """绘制趋势图"""
        self.canvas.delete("all")  # 清空旧图

        # 确定纵坐标最大值
        max_value = max(
            max(history['video'] or [0]),
            max(history['audio'] or [0]),
            max(history['total'] or [0]),
            1  # 防止除零
        )
        y_scale = 180 / max_value  # 留20像素边距

        # 绘制坐标轴
        self.canvas.create_line(30, 190, 370, 190)  # X轴
        self.canvas.create_line(30, 190, 30, 10)    # Y轴

        # 绘制曲线
        for key in ['video', 'audio', 'total']:
            points = []
            for i, value in enumerate(history[key]):
                x = 30 + (i * 340 / (HISTORY_SIZE-1))  # X轴均匀分布
                y = 190 - value * y_scale
                points.extend([x, y])

            if len(points) >= 4:
                self.canvas.create_line(
                    *points,
                    fill=COLORS[key],
                    width=2,
                    tags=key
                )

    def on_close(self):
        """关闭处理"""
        global running
        running = False
        self.destroy()

# ============================== 主程序 ==============================
if __name__ == '__main__':
    # 启动数据线程
    PacketCapture(VIDEO_PORT, 'video').start()
    PacketCapture(AUDIO_PORT, 'audio').start()

    # 启动GUI
    app = EnhancedMonitor()
    app.mainloop()

    # 等待退出
    print("\n等待线程退出...")
    time.sleep(1)
