# @time     : 2025/4/16 下午9:08
"""
屏幕广播接收端
主要功能：
1. 同时接收视频和音频流
2. 支持窗口置顶、拖动
3. 自动适应窗口尺寸
4. 右键功能菜单
"""

from zlib import decompress
from threading import Thread
from tkinter import Tk, Menu, Label
from socket import socket, AF_INET, SOCK_DGRAM
import pyaudio
from PIL.Image import frombytes
from PIL.ImageTk import PhotoImage

# 音频参数（必须与发送端一致）
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 44100
AUDIO_CHUNK = 1024


class ReceiverApp:
    """接收端主应用程序类"""
    def __init__(self):
        # 窗口初始化
        self.root = Tk()
        self.root.title('屏幕广播接收端-v1.6')
        self.root.geometry('800x600+0+0')
        self.receiving = True  # 接收状态控制

        # 初始化组件
        self.setup_ui()
        self.setup_audio()
        self.setup_network()
        self.setup_menu()
        self.setup_drag()

    def setup_ui(self):
        """初始化视频显示区域"""
        self.lbImage = Label(self.root, bg='black')
        self.lbImage.pack(fill='both', expand=True)  # 自适应窗口

    def setup_audio(self):
        """初始化音频播放设备"""
        self.p_audio = pyaudio.PyAudio()
        self.audio_stream = self.p_audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            output=True,  # 输出模式
            frames_per_buffer=AUDIO_CHUNK
        )

    def setup_network(self):
        """启动网络接收线程"""
        # 视频接收线程
        self.video_thread = Thread(target=self.recv_image, daemon=True)
        self.video_thread.start()

        # 音频接收线程
        self.audio_thread = Thread(target=self.recv_audio, daemon=True)
        self.audio_thread.start()

    def setup_menu(self):
        """创建右键上下文菜单"""
        self.menu = Menu(self.root, tearoff=0)
        self.menu.add_command(label="切换置顶", command=self.toggle_topmost)
        self.menu.add_command(label="退出", command=self.close_window)
        self.lbImage.bind("<Button-3>", self.show_menu)  # 右键绑定

    def setup_drag(self):
        """初始化窗口拖动功能"""
        self.drag_data = {"x": 0, "y": 0}
        self.lbImage.bind("<ButtonPress-1>", self.start_drag)
        self.lbImage.bind("<B1-Motion>", self.do_drag)

    def start_drag(self, event):
        """记录拖动起始坐标"""
        self.drag_data["x"] = event.x_root
        self.drag_data["y"] = event.y_root

    def do_drag(self, event):
        """执行窗口位置更新"""
        dx = event.x_root - self.drag_data["x"]
        dy = event.y_root - self.drag_data["y"]
        x = self.root.winfo_x() + dx
        y = self.root.winfo_y() + dy
        self.root.geometry(f"+{x}+{y}")  # 更新窗口位置
        self.drag_data["x"] = event.x_root
        self.drag_data["y"] = event.y_root

    def toggle_topmost(self):
        """切换窗口置顶状态"""
        current = self.root.attributes('-topmost')
        self.root.attributes('-topmost', not current)

    def show_menu(self, event):
        """显示右键菜单"""
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def recv_image(self):
        """视频接收线程函数
        协议处理流程：
        [循环架构] 双循环设计（外层控制帧切换，内层处理单帧数据）
        1. 等待start标记 -> 2. 收集数据直到_over标记 -> 3. 解析尺寸 -> 4. 启动处理线程
        特殊处理：数据收集中途收到新start标记时，立即重置流程接收新帧

        设计要点：
        - 实时性优先：允许丢弃不完整帧数据，确保最新画面及时显示
        - 网络适应性：动态处理乱序包和网络延迟
        """
        # 初始化视频专用UDP通道
        sock = socket(AF_INET, SOCK_DGRAM)
        sock.bind(('', 22222))  # 绑定固定端口实现协议分离

        # === 外层循环：帧控制循环 ===
        # 作用：持续接收新帧的起始标记，实现画面连续播放
        while self.receiving:
            # --- 阶段1：等待有效起始标记 ---
            # 功能：过滤网络残留数据，确保从新帧的起点开始接收
            # 设计：小循环持续读取直到获取start或close指令
            while self.receiving:
                # 接收协议控制包（60KB缓冲区可兼容start/close指令还有摄像头的包）
                chunk, _ = sock.recvfrom(60 * 1024)

                # 情景处理分支
                if chunk == b'start':
                    break  # 进入帧数据处理阶段
                elif chunk == b'close':
                    # 安全关闭流程（防止资源未释放）
                    self.safe_shutdown(sock)
                    return  # 直接终止线程
            else:
                # 当外层循环条件self.receiving变为False时的退出路径
                continue

            # === 阶段2：帧数据收集循环 ===
            # 功能：收集属于同一帧的所有数据包
            # 特性：允许被新start标记中断，保证实时性
            data = []  # 初始化当前帧数据容器
            while self.receiving:
                # 接收数据块（缓冲区大小与发送端BUFFER_SIZE一致）
                chunk, _ = sock.recvfrom(60 * 1024)

                # 协议标记处理分支
                if chunk.startswith(b'_over'):
                    # 正常结束标记处理
                    # 示例：b'_over(1920,1080)' -> chunk[5:]为尺寸字符串
                    self.process_image(data, chunk[5:])  # 提交完整数据
                    break  # 结束当前帧，回到外层循环

                elif chunk == b'close':
                    # 安全终止指令（跨线程协调关闭）
                    self.safe_shutdown(sock)
                    return

                elif chunk == b'start':
                    # !!! 关键异常处理 !!!
                    # 触发条件：发送端已开始新帧传输，但接收端仍在接收旧帧数据
                    # 问题：继续收集会导致新旧帧数据混杂
                    # 解决方案：
                    data.clear()  # 清空当前不完整数据
                    break  # 跳出内层循环，立即处理新帧

                else:
                    # 常规数据包处理
                    data.append(chunk)  # 累积帧数据

        # === 资源清理 ===
        # 触发条件：self.receiving被设置为False
        sock.close()  # 关闭网络连接

    def recv_audio(self):
        """音频接收线程函数
        实现特点：
        - 持续接收并播放音频
        - 自动解压缩数据
        """
        audio_sock = socket(AF_INET, SOCK_DGRAM)
        audio_sock.bind(('', 22223))  # 绑定音频端口

        while self.receiving:
            try:
                data, _ = audio_sock.recvfrom(65535)
                decompressed = decompress(data)
                self.audio_stream.write(decompressed)  # 实时播放
            except Exception as e:
                if self.receiving:
                    print("音频接收错误:", e)
        audio_sock.close()

    def process_image(self, data, size_info):
        """图像预处理
        参数：
            data: 原始字节数据列表
            size_info: 图像尺寸信息
        """
        try:
            image_size = eval(size_info)  # 解析尺寸元组
            image_data = decompress(b''.join(data))  # 解压完整数据
        except:
            data.clear()
            return

        # 使用子线程解码，避免阻塞网络线程
        Thread(target=self.decode_image,
               args=(image_data, image_size)).start()

    def decode_image(self, image_data, image_size):
        """图像解码与显示
        实现特点：
        - 在子线程中处理耗时操作
        - 使用root.after保证线程安全更新GUI
        """
        try:
            # 从字节数据创建图像
            img = frombytes('RGB', image_size, image_data)
            # 自适应窗口尺寸
            img = img.resize((self.root.winfo_width(), self.root.winfo_height()))
            photo = PhotoImage(img)
            # 通过主线程更新显示
            self.root.after(0, self.update_display, photo)
        except Exception as e:
            print("图像处理错误:", e)

    def update_display(self, photo):
        """安全更新图像显示"""
        self.lbImage.config(image=photo)
        self.lbImage.image = photo  # 保持引用避免被GC回收

    def safe_shutdown(self, sock):
        """安全关闭资源
        执行流程：
        1. 设置停止标志
        2. 关闭网络连接
        3. 释放音频资源
        4. 销毁窗口
        """
        self.receiving = False
        sock.close()
        # 释放音频资源
        self.audio_stream.stop_stream()
        self.audio_stream.close()
        self.p_audio.terminate()
        self.root.after(0, self.root.destroy)

    def close_window(self):
        """窗口关闭处理"""
        self.receiving = False
        self.root.after(100, self.root.destroy)  # 延迟销毁确保资源释放

    def run(self):
        """启动应用程序"""
        self.root.mainloop()


if __name__ == '__main__':
    app = ReceiverApp()
    app.run()
