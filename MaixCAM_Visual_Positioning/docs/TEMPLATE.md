# 上位机模板来源

- 仓库：<https://github.com/MacSwimmm/upper-computer-template>
- 使用提交：`71f6c076b7723165c5e875ad8a2ecdb82b27a908`
- 提交说明：`fix: prevent cross-surface hover stalls and live-data resets`

按用户要求复用该模板，以下文件原样复制到 `host/web/assets/`：

- `dashboard.css`
- `signal-theme.css`
- `pixel-field.js`
- `distortion-grid.js`
- `footer-distortion.js`

本项目用 `vision.css` 扩展布局，用 `vision.js` 接入真实相机、标定和坐标数据。页面结构沿用模板的导航、黑绿主题、点阵首屏和页底字标；页底继续使用模板的实际位移网格代码。

工作区为相机、标定和地面坐标操作重新组织，没有保留模板的任务调度示例数据。相机画面和标定点直接使用原生画布，不施加会影响定位判断的悬浮形变。

模板仓库本身未做修改。原 2026-AIC 电机固件和原上位机未做功能修改。
