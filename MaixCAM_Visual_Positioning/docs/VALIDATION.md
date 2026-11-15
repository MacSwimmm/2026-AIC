# 2.2.0 验证记录

本轮验证使用真实 MaixCAM Pro（GC4653、MaixPy 4.10.3、官方 1.22.0 运行库）、USB 网络、Windows EXE 与 Edge。普通模式传感器请求 60 fps，处理上限 45 fps；全画面 640 × 480、JPEG 质量 75，原 YOLO 模型与阈值保留。

## 功能与成像

| 报告 | 本轮结果与范围 |
|---|---|
| `unit-validation.json` | 64 项软件测试通过：几何/镜头、持久化、身份保护、帧有效期、进程生命周期、推理配对、通信协议、旧时序标定迁移、条件状态同步失效规则 |
| `browser-validation.json` | 模拟画面下桌面/窄屏布局、图像内信息条、点击穿透、标定/目标、冻结、恢复和断线行为通过，脚本错误 0 |
| `native-validation.json` | 真实屏幕 + Linux 触摸事件注入：尺寸输入、四点标定、选点保存、落盘/API 一致、重启持久化、退出返回单个系统 UI、菜单重新启动、YOLO 菜单双向切换通过 |
| `pc-workflow-validation.json` | 本版 EXE + Edge + 真实相机：网页标定、保存/导出/删除目标、校验点、四模式、冻结与帧率双读数、重连保存标定通过 |
| `controls-validation.json` | 电脑订阅时本机不访问相机预览，空闲界面不重绘，触屏模式菜单可用；断开恢复预览、重连关闭预览并保持模式/数据通过 |
| `live-validation.json` | 四模式完整 JPEG 解码、帧序号推进与推理执行通过；不据此声称所有类别或实体标签的检测精度通过 |
| `pi-delivery-validation.json` | 真实 MaixCAM 向电脑接收示例发送目标，验证落盘批次、请求 ID、修订号与确认；不是实体树莓派/车辆测试 |
| `pixel-conversion-validation.json` | 设备上已知颜色与独立 OpenCV 对照，转换每通道最大差 1；输入缓冲生命周期检查通过 |
| `capture-geometry-validation.json` | 固定场景中 NV21/RGB 191 个特征内点，中位位移约 0.22 px、95% 约 0.97 px；不是场地米制误差验收 |

完整像素坐标、Brown 去畸变、单应性及原三种识别输入规则保留。30/60 fps 传感器模式存在像素偏移，分别绑定成像身份；已有旧标定升级时保留原 30 fps 时序。错身份标定不能用于新成像，数据保留，禁止悄悄套用。

所有流程测试只在无正式标定/目标的设备上创建并清理自己的合成数据。注入触摸事件不代表手指体验，合成标定不代表实际 5×5 米场地误差；正式固定安装后仍需录入独立实测点。

## 性能

详见 [PERFORMANCE.md](PERFORMANCE.md) 和每份 JSON 的采样时长/源文件哈希。普通视频、连续 JPEG 接收、浏览器绘制、本机屏幕重绘分别计数，视频仅计不同 `(session_id,seq)`。

最终源码在十分钟 YOLO 热机后，正常单客户端连续接收/解码各 120 秒：普通 **44.95 fps**，YOLO **23.71 fps**，见 `performance-pc.json`。上位机与独立测速同时占两路相机订阅时为普通 40.53 / YOLO 21.08 fps，见 `performance-dual-viewer.json`，两种负载分别记录。

本机合成标定并保存目标后的连续解码：普通约 44.7 fps，YOLO 约 23.5 fps（每模式 15 秒）。本版 EXE + Edge 带标定/网格实际绘制：普通 44.76 fps，YOLO 22.39 fps（各约 5 秒，具体以 JSON 为准）。场景、光照、温度和浏览器负载影响结果，短测不能替代长时间观察。

相机普通模式上限改为 60 后，最终单通道像素路径短测约 46.5 fps，故交付上限设为 45。探索过的双通道方案因完整回归发生驱动等待已弃用；交付始终只有一个摄像头所有者和一个采集通道。

## 长时间与交付核对

`yolo-regression-validation.json`：十轮 YOLO 开/关、三次推理中重连、立即断开/重连均通过，每次核对新会话的 JPEG 与帧前进。

`stability-validation.json`：持续 YOLO 600.1 秒、2996 次状态采样、600 张抽样 JPEG 解码，断线 0、请求/解码错误 0；温度 51.44 → 63.32°C，标定与模式保持。结束后恢复普通模式；每秒抽样不作为视频帧率。

部署字节与原始数据恢复结果见 `deployment-validation.json`，以报告中的 `passed`、版本和产物哈希核对。设备上的原始空标定/目标文件及接收设置恢复为测试前的原始字节，私有令牌保留。

本版 `MaixVisionConsole.exe`：`b082f113d43e069bb06e5c18fa5abb336a733bd515356aa29d7d0d62831d41c9`。`verify_package.py` 已核对 EXE 内网页、设备文件/像素库、几何文件与电脑编译模块均等于当前源码。

本版 `MaixGround-2.2.0.zip`：`2dcdc89d8a3b53ccab89c4cf25c8d199740f03ce560229f7e47bd6409672f202`。ZIP 文件清单与每项 SHA-256 见 `native-package.json`，像素转换 C 源码及 Zig 0.13.0 重建工具随仓库提供。

设备保存位置仍为 `/root/aic_vision/data/`，更新不迁入电脑旧试验数据。现场照片只留本地 `data/qa/`，凭据、标定和目标均不提交公开仓库。原电机固件与电机上位机不参与本次视觉优化。

## 保留的历史依据

`performance-controls-before.json`、`live-browser-validation.json`、`overlay-validation.json` 是 2.1.x 历史记录；`nn-pairing-validation.json` 是原双缓冲 YOLO 返回上一输入结果的 120 次随机输入比对依据。本版没有改变模型输入与结果配对算法，软件回归保留对应检查。`performance-baseline30.json` 是本轮恢复原 2.1.1 源码的基线；`pc-workflow-before-status-cache.json` 是增加条件状态同步之前的 2.2.0 候选，不能当作最终 EXE 性能。

本轮没有配置 Wi-Fi，USB 成功不代表热点吞吐；树莓派样例接收成功不代表真实导航地图转换或车辆执行已验证。
