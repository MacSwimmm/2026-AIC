# 地面坐标接口

电脑端服务默认绑定 `127.0.0.1:29860`。写入接口使用 `POST` 和 `Content-Type: application/json`。这些接口供本地上位机和后续集成使用；本版未连接底盘导航。

## 坐标约定

- 原点 A `(0,0)`，AB 沿 X 正方向，AD 沿 Y 正方向，单位 `m`。
- 完整相机图像使用左上角为原点的像素坐标 `[u,v]`，默认分辨率 640×480。
- 标定映射方向：完整图像像素 → 可选镜头去畸变 → 地面单应性变换 → `[x,y]`。
- 网格显示使用逆方向：地面 → 逆单应性 → 镜头畸变 → 完整画面像素。
- YOLO 输入为 320×224；坐标先按各轴比例还原到完整图像。AprilTag 输入为 160×120，同样还原。
- 地面图仅是同一坐标系的示意图，不是经过测量的障碍物地图。

## 常用接口

| 方法与路径 | 内容 |
|---|---|
| GET `/api/status` | 连接状态、最新帧信息、识别结果、标定、网格和已保存目标 |
| GET `/api/frame.jpg` | 实时 JPEG；`X-Frame-Meta` 为 Base64 编码的 JSON 同帧信息 |
| POST `/api/connect` | `{host, username, password}`；空地址时查找 USB 连接，异步完成部署 |
| POST `/api/disconnect` | `{}`；停止取图程序并返回设备菜单 |
| POST `/api/mode` | `{mode: "off" / "yolo11" / "red_blob" / "apriltag"}` |
| POST `/api/calibration` | `{width_m, height_m, points: [[u,v],...四点], lens: 可选参数}` |
| POST `/api/point` | `{pixel:[u,v]}` 或 `{world:[x,y]}`；返回位置，区域外返回错误 |
| POST `/api/targets` | `{pixel:[u,v], name: 可选名称}`；保存虚拟目标点 |
| POST `/api/targets/remove` | `{id}`；删除一个目标点 |
| POST `/api/targets/clear` | `{}`；清空目标点 |
| GET `/api/targets/export` | JSON，米制坐标，`navigation_sent:false` |
| GET `/api/targets.csv` | CSV，米制坐标 |
| POST `/api/check` | `{pixel:[u,v], actual:[x,y]}`；记录一个独立实测校验点 |
| GET `/api/calibration/export` | 导出标定 JSON |
| POST `/api/calibration/import` | 导入标定 JSON；检查相机和成像设置并重新计算矩阵 |
| POST `/api/calibration/clear` | `{}`；清除标定及旧目标点 |

`/api/point` 返回示例中的字段含义：

```json
{"x":1.2,"y":2.8,"inside":true,"pixel":[320,180],"units":"m","assumption":"visible_ground_plane"}
```

上例仅说明字段，不是某台现场相机的实际换算结果。

## 识别结果与时间

每个识别结果包含 `label`、`score`、完整画面的 `bbox`、`anchor`、`anchor_kind`，标定匹配时额外提供 `world:{x,y,inside}`。

- `bbox_bottom_approx`：框底中心，仅是物体落地位置近似。
- `marker_center_assume_ground`：红色标记中心，假设它贴地。
- `tag_center_assume_ground`：AprilTag 中心，假设它贴地。

`session_id` 标识一次设备取图进程；同一会话中的帧序号 `seq` 标识一次采集。进入或退出 YOLO 时取图进程重启，产生新会话并从头计数，因此应使用 `(session_id, seq)` 判断帧身份。`captured_at` 来自设备时钟；`received_at` 来自电脑时钟。两端时钟未经同步时不要直接相减计算延迟。电脑仅在接收到新帧时更新有效时间；断线或帧序号停止前进超过 3 秒，不再允许新坐标操作。HTTP 服务反复返回同一张旧图不能延长有效期。

相机身份来自硬件 UID 的摘要。标定与相机身份、分辨率、成像约定绑定；换相机或分辨率后拒绝沿用旧标定。物理移动相机无法仅凭这些字段检测，仍需重新标定。

## 后续与导航对接

未来可以读取已保存的 `ground` 目标点，或者从识别结果取目标坐标，再进行与导航地图坐标系的对齐、时间检查和路径规划。当前没有实现地图坐标对齐、目标跟踪 ID、速度估计、避障或车辆控制，不应把本接口的识别框近似位置直接当作已经验证的导航定位。

## 设备侧

取图服务位于相机端 18765 端口，使用每次部署生成的 Bearer 令牌；浏览器通过本机服务读取画面。相机、模型、图像缓冲区只由设备主线程操作；HTTP 线程读取成对的不可变 JPEG/识别快照，避免多客户端竞争相机。
