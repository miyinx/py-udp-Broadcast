# @time     : 2025/4/16 下午9:07
"""
屏幕广播发送端-v1.6（支持视频源热切换版本）
改进点：
1. 支持屏幕/摄像头热切换
2. 优化摄像头资源管理
3. 增强线程安全性
"""

# ============================== 系统配置 ==============================
from time import sleep
from os import startfile
from zlib import compress  # 使用zlib进行数据压缩（DEFLATE算法）
from threading import Thread, Lock  # 线程模块，Lock用于资源同步
import cv2  # OpenCV库，用于摄像头操作
import pyaudio  # 音频处理库
from socket import socket, AF_INET, SOCK_DGRAM, SOL_SOCKET, SO_BROADCAST  # UDP广播相关
from tkinter import Tk, BooleanVar, Button, Label, StringVar, Radiobutton, Checkbutton  # GUI组件
from PIL.ImageGrab import grab  # 屏幕截图库（比mss更快）

# 音频配置
FORMAT = pyaudio.paInt16  # 16位整型音频格式（兼容性最好）
CHANNELS = 1  # 单声道（降低带宽消耗）
RATE = 44100  # 采样率（Hz）
AUDIO_CHUNK = 1024  # 每次读取的音频数据块大小（经验值，平衡延迟和性能）

# ============================== GUI初始化 ==============================
root = Tk()
root.title('屏幕广播发送端-v1.6')
root.geometry('330x150+500+200')  # 窗口宽度x高度+水平偏移+垂直偏移
root.resizable(False, False)  # 禁止调整窗口大小（保持界面布局）

# ============================== 全局变量 ==============================
BUFFER_SIZE = 60 * 1024  # UDP数据分块大小（60KB，经验值）
sending = BooleanVar(root, value=False)  # 广播状态原子变量（线程安全）
source_type = StringVar(value='screen')  # 当前视频源类型（'screen'/'camera'）
audio_enabled = BooleanVar(value=False)  # 音频传输开关状态

# 资源对象
cap = None  # 摄像头设备对象（OpenCV VideoCapture实例）
audio_stream = None  # 音频输入流（PyAudio Stream对象）
audio_socket = None  # 音频传输专用socket（与视频分开端口）
p_audio = None  # PyAudio实例（音频设备接口）
audio_thread = None  # 音频传输线程对象

# 资源锁（防止多线程资源竞争）
camera_lock = Lock()  # 摄像头操作锁（保证open/release原子性）
audio_lock = Lock()  # 音频设备操作锁（防止同时操作设备）


# ============================== 摄像头管理模块 ==============================
def init_camera():
    """安全初始化摄像头设备
    线程安全地执行摄像头初始化，防止多线程同时操作导致设备冲突
     功能说明：
    - 检查摄像头是否已打开
    - 未打开时尝试初始化摄像头
    - 返回初始化状态（True/False）
    """
    global cap
    with camera_lock:  # 获取锁后执行临界区代码
        if cap is None or not cap.isOpened():
            cap = cv2.VideoCapture(0)  # 尝试打开默认的摄像头，参数0表示默认摄像头
            return cap.isOpened()  # 返回摄像头是否成功打开
        return True


def release_camera():
    """安全释放摄像头资源
    确保在释放摄像头资源时不会被其他线程中断
    功能说明：
    - 如果摄像头处于打开状态则释放资源
    - 将cap置为None防止重复释放
    """
    global cap
    with camera_lock:
        if cap and cap.isOpened():
            cap.release()  # 释放摄像头设备
            cap = None  # 置空防止野指针


# ============================== 视频处理模块 ==============================
def get_frame():
    """获取当前帧数据
    根据当前选择的视频源动态获取图像数据，自动处理颜色空间转换
    功能说明：
    - 根据当前模式获取视频源数据
    - 屏幕模式使用PIL的grab()获取屏幕截图
    - 摄像头模式使用OpenCV读取视频帧
    - 自动进行颜色空间转换（BGR转RGB）
    """
    current_source = source_type.get()

    if current_source == 'screen':
        # 使用PIL获取屏幕截图（全屏），转换为RGB格式（兼容网络传输）
        return grab().convert('RGB')  # convert确保统一格式
    else:
        with camera_lock:  # 保证摄像头读取的原子性
            if cap and cap.isOpened():
                ret, frame = cap.read()  # 读取摄像头帧（BGR格式）
                # OpenCV默认使用BGR格式，转换为RGB用于后续处理
                return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if ret else None
            return None


def send_image():
    """视频流发送线程函数
    实现UDP广播传输的核心逻辑，包含分块传输、错误恢复和动态切换机制
    实现逻辑：
    1. 创建UDP广播socket（SO_BROADCAST选项启用广播）
    2. 循环获取帧数据并根据当前模式处理：
       - 屏幕模式：使用PIL截图，压缩字节数据
       - 摄像头模式：使用OpenCV获取帧，压缩字节数据
    3. 数据传输协议：
       - 发送b'start'标记表示传输开始
       - 将压缩数据分块发送（每块60KB）
       - 发送b'_over+分辨率信息'标记表示传输结束
    4. 异常处理：
       - 摄像头不可用时自动切换回屏幕模式
       - 发送错误时自动释放资源
    """
    # 创建UDP socket并设置广播选项
    sock = socket(AF_INET, SOCK_DGRAM)  # IPv4 UDP socket
    sock.setsockopt(SOL_SOCKET, SO_BROADCAST, 1)  # 启用广播（关键选项）
    IP = '192.168.31.255'  # 192.168.31.255受限广播地址（局域网所有主机）255.255.255.255

    while sending.get():  # 主循环（根据广播状态控制）
        try:
            current_source = source_type.get()  # 动态获取当前视频源

            # 模式切换保护：摄像头模式需要确保初始化成功
            if current_source == 'camera' and not init_camera():
                source_type.set('screen')  # 自动回退到屏幕模式
                continue  # 跳过本次循环

            img = get_frame()  # 获取当前帧数据
            if img is None:  # 获取失败时短暂休眠
                sleep(0.1)
                continue

            # 统一数据处理流程（根据源类型调整参数）
            if current_source == 'screen':
                w, h = img.size  # PIL图像尺寸（宽, 高）
                # 将PIL图像转换为字节数据并压缩（使用zlib）
                im_bytes = compress(img.tobytes())  # tobytes()获取原始像素数据
            else:
                h, w = img.shape[:2]  # OpenCV图像尺寸（高, 宽）
                im_bytes = compress(img.tobytes())  # numpy数组直接获取字节

            # 分段传输协议（解决UDP包大小限制问题）
            # 发送开始标记（接收端用于重置缓冲区）
            sock.sendto(b'start', (IP, 22222))
            # 计算分块数量（ceil除法确保发送完整数据）
            for i in range(len(im_bytes) // BUFFER_SIZE + 1):
                # 分块发送压缩后的数据（每块最大60KB）
                chunk = im_bytes[i * BUFFER_SIZE:(i + 1) * BUFFER_SIZE]
                sock.sendto(chunk, (IP, 22222))
            # 发送结束标记和分辨率信息（用于接收端重建图像）
            # 使用_over前缀避免与数据内容冲突
            end_marker = b'_over' + str((w, h)).encode()  # 示例：b'_over(1920,1080)'
            sock.sendto(end_marker, (IP, 22222))

            # 动态帧率控制（摄像头通常帧率低于屏幕）
            sleep(0.05 if current_source == 'camera' else 0.04)  # 约20-25FPS
        except Exception as e:
            print("视频发送异常:", e)
            if current_source == 'camera':
                source_type.set('screen')  # 异常时自动切换安全模式

    # 资源清理阶段（循环结束后执行）
    release_camera()  # 确保释放摄像头资源
    sock.sendto(b'close', (IP, 22222))  # 通知接收端结束传输，关闭接收端程序
    sock.close()  # 关闭socket（释放系统资源）


# ============================== 音频处理模块 ==============================
def send_audio():
    """音频发送线程函数
    实现音频采集、压缩和广播的完整流程，独立于视频传输
    实现逻辑：
    1. 初始化音频设备（PyAudio）
    2. 创建音频传输socket
    3. 循环读取音频数据块：
       - 使用zlib压缩音频数据
       - 通过UDP广播发送到22223端口
    4. 资源安全释放：
       - 停止音频流
       - 关闭PyAudio实例
       - 关闭socket连接
    """
    global audio_stream, audio_socket
    IP = '255.255.255.255'  # 使用独立端口(22223)避免数据混叠

    with audio_lock:  # 确保音频设备初始化原子性
        try:
            p = pyaudio.PyAudio()  # 创建PyAudio实例（音频设备接口）
            # 打开音频输入流（麦克风）
            stream = p.open(
                format=FORMAT,
                channels=CHANNELS,
                rate=RATE,
                input=True,  # 输入模式
                frames_per_buffer=AUDIO_CHUNK  # 每次读取的块大小
            )
            # 创建专用音频传输socket（与视频分开端口）
            audio_socket = socket(AF_INET, SOCK_DGRAM)
            audio_socket.setsockopt(SOL_SOCKET, SO_BROADCAST, 1)

            # 更新全局变量（在锁保护下进行）
            global p_audio, audio_stream
            p_audio = p
            audio_stream = stream
        except Exception as e:
            print("音频初始化失败:", e)
            return  # 初始化失败直接返回

    print("音频传输已启动")
    # 主采集循环（同时检测广播状态和音频开关）
    while sending.get() and audio_enabled.get():
        try:
            # 读取音频数据块（阻塞式读取，长度=AUDIO_CHUNK）
            data = stream.read(AUDIO_CHUNK)
            compressed = compress(data)  # 压缩音频数据（减少带宽）
            # 发送到独立端口22223（接收端需分开处理）
            audio_socket.sendto(compressed, (IP, 22223))
        except Exception as e:
            print("音频发送错误:", e)
            break  # 发生错误退出循环

    # 资源释放（在锁保护下进行）
    with audio_lock:
        if stream.is_active():
            stream.stop_stream()  # 停止音频流（如果仍在运行）
        stream.close()  # 关闭流
        p.terminate()  # 释放PyAudio资源
        audio_socket.close()  # 关闭socket


# ============================== GUI回调函数 ==============================
def toggle_audio():
    """音频开关切换回调
    处理音频传输的实时启停，注意不直接操作线程而是通过状态控制
    功能说明：
    - 当音频开关状态改变时触发
    - 如果正在广播且开启音频，则启动新音频线程
    - 关闭音频时等待线程自然退出
    """
    if sending.get():  # 仅在广播运行时生效
        if audio_enabled.get():
            # 启动新线程避免阻塞GUI
            Thread(target=send_audio).start()
        else:
            # 仅输出日志，实际停止由主循环检测状态变化
            print("等待音频线程退出")


def on_source_change():
    """视频源切换回调
    处理屏幕/摄像头切换时的资源管理，保证切换原子性
    功能说明：
    - 当用户切换视频源时触发
    - 切换到屏幕模式时立即释放摄像头资源
    - 切换到摄像头模式时尝试初始化摄像头
    - 摄像头初始化失败自动切换回屏幕模式
    """
    if source_type.get() == 'screen':
        release_camera()  # 立即释放摄像头资源
    else:
        if not init_camera():  # 尝试初始化摄像头
            source_type.set('screen')  # 失败则重置选项


# ============================== 主控制逻辑 ==============================
def btnStartClick():
    """开始广播按钮回调
    处理广播启动流程，启动视频/音频线程，更新界面状态
    实现流程：
    1. 检查摄像头模式是否可用
    2. 设置广播状态为True
    3. 启动视频发送线程
    4. 如果开启音频则启动音频线程
    5. 更新GUI控件状态
    """
    # 前置检查：摄像头模式需要成功初始化
    if source_type.get() == 'camera' and not init_camera():
        source_type.set('screen')  # 自动切换回屏幕模式
        return

    sending.set(True)  # 设置广播状态标志
    # 启动视频发送线程（无参数传递）
    Thread(target=send_image).start()
    # 如果开启音频则启动音频线程
    if audio_enabled.get():
        Thread(target=send_audio).start()

    # 更新按钮状态
    btnStart['state'] = 'disabled'
    btnStop['state'] = 'normal'
    status_label.config(text="状态：广播中...", fg='green')


def btnStopClick():
    """停止广播按钮回调
    安全停止所有传输，释放系统资源，恢复界面状态
    实现流程：
    1. 设置广播状态为False
    2. 等待1秒确保线程退出
    3. 安全释放所有音频资源
    4. 释放摄像头资源
    5. 更新GUI状态
    """
    sending.set(False)  # 通知所有线程停止
    sleep(1)  # 等待线程退出（经验值，确保资源释放）

    # 安全释放音频资源（在锁保护下进行）
    with audio_lock:
        if audio_stream:
            audio_stream.stop_stream()
            audio_stream.close()
        if p_audio:
            p_audio.terminate()  # 必须终止PyAudio实例
        if audio_socket:
            audio_socket.close()

    release_camera()  # 确保摄像头资源释放

    # 恢复界面元素状态
    btnStart['state'] = 'normal'
    btnStop['state'] = 'disabled'
    status_label.config(text="状态：已停止广播", fg='red')


# ============================== GUI布局 ==============================
# 音频控制组件
Checkbutton(root, text="传输麦克风音频", variable=audio_enabled,
            command=toggle_audio).place(x=220, y=30)

# 标题标签（居中显示）
Label(root, text='UDP局域网屏幕广播系统', fg='red').place(x=5, y=5, width=330, height=20)

# 视频源选择组件（单选按钮）
Radiobutton(root, text="屏幕共享", variable=source_type, value='screen',
            command=on_source_change).place(x=20, y=30)
Radiobutton(root, text="摄像头直播", variable=source_type, value='camera',
            command=on_source_change).place(x=120, y=30)

# 控制按钮（开始/停止）
btnStart = Button(root, text='开始广播', command=btnStartClick)
btnStart.place(x=20, y=60, width=125, height=25)
btnStop = Button(root, text='停止广播', command=btnStopClick, state='disabled')
btnStop.place(x=185, y=60, width=125, height=25)

# 状态显示标签
status_label = Label(root, text="状态：就绪，未广播", fg='gray')
status_label.place(x=5, y=95)

# 作者信息（带超链接）
url = r'https://github.com/miyinx/py-udp-Broadcast'
lb = Label(root, text="计算机学院-miyinx", fg="blue", cursor="hand2")
lb.place(x=110, y=120)
# 绑定点击事件（使用默认浏览器打开链接）
lb.bind("<Button-1>", lambda e: startfile(url))

# 启动GUI主事件循环
root.mainloop()
