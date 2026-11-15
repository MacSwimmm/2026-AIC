# 2.2.0 本机地面坐标与目标点接口

MaixCAM 是标定、目标点及坐标计算的唯一数据来源。电脑服务绑定 `127.0.0.1:29860`，负责网页和代理请求，不再读取电脑旧标定。相机端监听 `18765`，请求必须带 `Authorization: Bearer <设备私有令牌>`；令牌在设备 `/root/aic_vision/agent_config.json`。设备 API 不直接对普通浏览器开放跨域权限。

## 坐标、身份和持久化

- A=(0,0)，AB=+X，AD=+Y，单位 m，坐标系名 `ground`。
- 完整画面 640 × 480，左上角为像素原点；屏幕预览和识别缩放均还原到这套像素坐标。
- 设备运行 `host/geometry.py` 的同一套算法：像素 → 可选 Brown 去畸变 → 单应性 → 地面坐标。
- 标定和目标点保存在设备 `/root/aic_vision/data/ground.json`（schema 2）。校验点、矩阵、相机 UID 摘要、成像设置和批次修订号一起保存。每次修改更新 `revision`。
- 保存先同步临时文件，再原子替换并同步目录；成功回复表示设备保存完成。重新标定/清除标定同时清除原目标点。
- 数据不在程序安装目录内；安装更新保留已有设备端数据，电脑的 1.x 临时试验数据不自动导入。

## 设备 API

以下接口位于 `http://相机IP:18765`，全部使用 Bearer 令牌。POST 使用 JSON 对象和 `Content-Type: application/json`。

| 方法/路径 | 内容 |
|---|---|
| GET `/status` | 设备版本、识别模式、相机是否就绪、最新帧序号 |
| GET `/stream.mjpg` | 持续 multipart JPEG，每部分独立携带 `Content-Length` 与 `X-Frame-Meta`；慢客户端只取最新帧 |
| GET `/frame.jpg` | 原始实时 JPEG；`X-Frame-Meta` 是 Base64 JSON 同帧数据 |
| GET `/screen.jpg` | 最近一次本机屏幕绘制图像，用于检查（包含现场画面） |
| GET `/ground/status` 或 `/api/status` | 设备标定、网格、目标点、修订号、存储状态和树莓派接收设置 |
| POST `/mode` | `{mode:"off" / "yolo11" / "red_blob" / "apriltag"}` |
| POST `/stop` | `{}`；退出设备应用，完整释放资源后恢复系统界面 |
| POST `/api/calibration` | `{width_m,height_m,points:[[u,v],...四点],lens:可选}` |
| POST `/api/calibration/import` | 导入标定 JSON；核对设备/成像身份并重新计算矩阵 |
| GET `/api/calibration/export` | 导出设备本地标定 |
| POST `/api/calibration/clear` | `{}`；清除设备标定与目标点 |
| POST `/api/point` | `{pixel:[u,v]}` 或 `{world:[x,y]}`；返回地面位置，区域外拒绝 |
| POST `/api/check` | `{pixel:[u,v],actual:[x,y]}`；保存独立实测校验点 |
| POST `/api/targets` | `{pixel:[u,v],name:可选}`；也支持 `{world:[x,y]}` |
| POST `/api/targets/remove` | `{id}` |
| POST `/api/targets/clear` | `{}` |
| GET `/api/targets/export` | 导出当前匹配标定下的整批目标点 |
| POST `/api/pi/config` | `{url:"http://树莓派IP:18766/targets"}`；接收地址保存在设备 `pi.json` |
| POST `/api/pi/send` | `{}`；由设备主动发送当前整批目标点，并验证接收确认 |

`/api/point` 返回 `x,y,inside,pixel,units,assumption`。`assumption:"visible_ground_plane"` 表示把所选位置当作可见地面；它不识别物体高度。网格是同一地平面示意，不是障碍物地图。

`/frame.jpg?after=session_id:seq` 及电脑 `/api/frame.jpg?after=...` 最多等待 1 秒取新帧，减少重复获取。设备没有订阅者时暂停连续网络 JPEG 编码；屏幕检查图片只在请求时生成。`/status` 同时给出实际处理 `fps`、实际编码 `stream_fps`、屏幕刷新 `screen_fps` 和分阶段 `timings_ms`。

2.1.1 增加 `pc_viewers`（连续图像订阅数）、`local_preview_enabled`、`local_preview_seq`（本机实际预览绘制累计次数）、`screen_page`。存在连续图像订阅时，本机停止相机图像绘制，仅保留操作界面；最后一个订阅断开后自动恢复本机预览。`display_seq` 计全部本机界面重绘，按钮界面空闲时不持续重绘。单次 `/frame.jpg` 检查不会切换本机操作模式。

2.2.0 的设备 `/ground/status` 与 `/api/status` 返回 `ETag`。客户端可发送 `If-None-Match`；数据未变化时返回无正文的 304。标定/目标修订、兼容性、读取错误、树莓派地址或发送结果消息变化时返回 200 和完整 JSON。不带条件头的原有客户端行为保持一致。

2.2.0 的 `/status` 和帧元数据增加 `camera_fps`（传感器请求帧率）、`fps_limit`（处理上限）、`geometry_id`（成像坐标身份）和 `pixel_format`（`nv21` / `rgb888`）。新设备默认 60 / 45；有旧正式标定的设备升级时保留 30 / 30。`geometry_id` 分别为 `640x480-sensor60-v2` 和旧的 `640x480-rgb-v1`；不兼容的标定禁止用于坐标计算与导入。

网络帧增加 `agent_frame_age_ms`：设备自身单调时钟上，从该图像 `cam.read()` 返回到发送响应前的时间。它包括双缓冲推理的等待，但不包含曝光前等待、USB 传输、电脑解码和显示，因此不能当作端到端延迟。

电脑网页的「实际处理 FPS」来自设备实际处理计数；「电脑显示 FPS」在浏览器成功绘制不同帧之后计数，按近两秒采样，冻结时显示「已冻结」。它们与本机 `screen_fps` 含义不同。

## 帧与识别结果

每个识别结果含 `label,score,bbox,anchor,anchor_kind`，标定匹配时附 `world:{x,y,inside}`。坐标在设备上计算后随该 JPEG 发给电脑和设备屏幕。

- YOLO 使用原模型、0.5 置信度、0.45 IoU；双缓冲返回上一输入的推理结果，程序保留该输入的原始 RGB 帧及采集时间，与结果一起发布；不复用上一结果给下一画面。输入 320 × 224；锚点 `bbox_bottom_approx` 为框底中心近似。
- 红色锚点 `marker_center_assume_ground` 假设标记贴地。
- AprilTag 输入 160 × 120；锚点 `tag_center_assume_ground` 假设标签贴地。
- `(session_id,seq)` 标识帧；进入/退出普通模式或 YOLO 产生新会话，序号重新开始。
- `captured_at` 是设备时钟；电脑 `received_at` 是电脑时钟，不可直接相减声称端到端延迟。
- 设备使用 `monotonic_at` 检查自身新帧有效期；电脑仅在新帧到达时刷新本地有效期。超过 3 秒无新帧，禁止新坐标操作和目标点发送。

相机的物理移动不能仅凭 UID 检测，仍需重新标定。持久化文件保留不代表原安装几何仍有效。

## 电脑 API 与连接行为

电脑保留上表中的 `/api/*` 数据接口及 `/api/targets.csv`，所有数据修改代理到设备。另提供：

| 方法/路径 | 内容 |
|---|---|
| GET `/api/status` | `application:"maixcam-ground-console"`、版本、连接/帧信息、设备标定与目标状态 |
| GET `/api/frame.jpg` | 转发原始 JPEG 及设备已换算的同帧识别信息 |
| POST `/api/connect` | `{host,username,password}`；空 host 自动尝试 USB，异步连接 |
| POST `/api/disconnect` | `{}`；**只断开电脑查看，设备本机程序继续运行** |
| POST `/api/mode` | 转发设备识别模式切换 |

已运行的 2.2.0 应用直接复用，电脑重连不重置识别模式和凭据。尚未运行时可启动带屏幕的设备程序。菜单应用首次安装使用 `tools/install_native.py`。

## 设备主动发送的批次协议

在设备屏幕「目标点列表」点击发送，或在电脑请求 `/api/pi/send` 后，MaixCAM 向已配置的接收地址 POST：

```json
{
  "schema": 2,
  "type": "ground_targets",
  "request_id": "本次请求的唯一ID",
  "frame": "ground",
  "units": "m",
  "navigation_sent": false,
  "device_id": "相机身份摘要",
  "calibration_created_at": 1234567890.0,
  "revision": 4,
  "targets": [
    {"id":"目标ID","name":"目标 1","x":0.3,"y":0.6,
     "pixel":[200,300],"source":"manual_ground","units":"m","created_at":1234567891.0}
  ]
}
```

示例坐标仅用于说明字段。树莓派接收程序保存后返回：

```json
{"accepted":true,"request_id":"与请求相同的ID","revision":4}
```

只有 `accepted:true`、请求 ID、修订号全部匹配，设备才显示接收成功。HTTP 200 或旧批次回执不足以确认。超时不自动重复发送，用户可重试；接收端示例以整批快照覆盖文件，不累加车辆动作。`navigation_sent:false` 及回执中的 `vehicle_motion_verified:false` 表示此接口交付的是目标数据，没有执行导航运动。

接收程序：`examples/pi_target_receiver.py`，默认 `0.0.0.0:18766/targets`，只保存数据。读取程序：`examples/pi_read_targets.py`，读取设备 `/api/targets/export`，令牌从私有文件获得。两端需网络互通；生产接入时应在自己的局域网与接收权限下使用。

树莓派应以 `device_id + calibration_created_at + revision` 区分坐标版本，先完成 ground 与导航地图坐标系的对齐，再接入规划/车辆执行。本版未实现地图对齐、跟踪 ID、速度估计或底盘控制。
