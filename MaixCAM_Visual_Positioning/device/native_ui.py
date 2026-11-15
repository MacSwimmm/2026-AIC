"""Touchscreen UI; all image/display/touch calls remain on the camera main thread."""
import time

MODES = ("off", "yolo11", "red_blob", "apriltag")
MODE_NAMES = ("普通画面", "YOLO11", "红色标记", "AprilTag")


class Screen:
    def __init__(self, store, set_mode, exit_app, pi_link=None):
        from maix import display, touchscreen, image
        self.image = image
        self.display = display.Display()
        self.touch = touchscreen.TouchScreen()
        self.width, self.height = self.display.width(), self.display.height()
        self.vw = min(self.width - 140, int((self.height - 64) * 4 / 3))
        self.vh = int(self.vw * 3 / 4)
        self.store, self.set_mode, self.exit_app = store, set_mode, exit_app
        self.pi_link = pi_link; self.sending = False
        self.page = "live"; self.buttons = []; self.pressed = False
        self.corners = []; self.frozen = None; self.selection = None
        self.calibration_id = None; self.message = "请先完成正式场地标定"
        self.fields = ["5", "5"]; self.field = 0; self.replace_field = True
        self.target_index = 0
        self.screen_jpeg = None; self.last_capture = 0
        self.font = ""
        from pathlib import Path
        preferred = Path('/maixapp/share/font/SourceHanSansCN-Regular.otf')
        fonts = [preferred] if preferred.exists() else list(Path('/maixapp/share/font').glob('*.ttf'))
        if fonts:
            image.load_font("aic_cn", str(fonts[0]), 18)
            self.font = "aic_cn"

    def text(self, canvas, x, y, text, color=None):
        canvas.draw_string(int(x), int(y), str(text), color or self.image.COLOR_WHITE,
                           scale=1, wrap=False, font=self.font)

    def button(self, canvas, rect, label, action):
        x, y, w, h = map(int, rect)
        canvas.draw_rect(x, y, w, h, self.image.COLOR_GREEN, thickness=1)
        self.text(canvas, x + 7, y + (h - 18) // 2, label)
        self.buttons.append(((x, y, w, h), action))

    def side_buttons(self, canvas, items):
        x = self.vw + 6; w = self.width - x - 3
        h = min(44, (self.height - 38) // len(items))
        for i, (name, action) in enumerate(items):
            self.button(canvas, (x, 32 + i * h, w, h - 3), name, action)

    def action(self, action, meta):
        if action == "exit": self.exit_app(); return
        if action == "back": self.page = "live"; self.frozen = None; self.corners = []; return
        if action == "calibrate":
            self.page = "size"; self.selection = None
            if self.store.cal: self.fields = [str(self.store.cal.width), str(self.store.cal.height)]
            self.field = 0; self.replace_field = True; return
        if action.startswith("field:"):
            self.field = int(action[-1]); self.replace_field = True; return
        if action.startswith("key:"):
            key = action[4:]
            old = "" if self.replace_field else self.fields[self.field]
            if key == "←": self.fields[self.field] = old[:-1]
            elif len(old) < 9 and (key != "." or "." not in old): self.fields[self.field] = old + key
            self.replace_field = False; return
        if action == "freeze":
            from geometry import number
            if any(not 0 < number(v) <= 1000 for v in self.fields): raise ValueError("宽长需大于零")
            self.page = "corners"; self.corners = []; self.message = "依次点击 A、B、C、D"; return
        if action == "undo":
            if self.corners: self.corners.pop()
            return
        if action == "apply":
            self.store.post("/api/calibration", {"width_m": float(self.fields[0]), "height_m": float(self.fields[1]),
                "points": self.corners, "lens": self.store.cal.lens.data if self.store.cal else None}, meta)
            self.page = "live"; self.frozen = None; self.selection = None
            self.message = "标定已保存到本机，请实测校验"; return
        if action == "save":
            if not self.selection: raise ValueError("先点击地面选点")
            p = self.store.post("/api/targets", {"pixel": self.selection["pixel"]}, meta)
            self.message = f"已保存 {p['name']} ({p['x']:.2f}, {p['y']:.2f}) m"; return
        if action == "mode":
            mode = MODES[(MODES.index(meta.get("mode", "off")) + 1) % len(MODES)]
            self.set_mode(mode); self.message = "正在切换识别，请稍候…"; return
        if action == "targets": self.page = "targets"; self.target_index = 0; return
        if action == "send":
            if self.sending: raise ValueError('正在发送，请稍候')
            batch = self.store.export_targets(meta)
            self.sending = True; self.message = '正在发送目标点…'
            import threading
            def send():
                try: self.message = self.pi_link.send(batch)['message']
                except ValueError as e: self.message = str(e)
                finally: self.sending = False
            threading.Thread(target=send, daemon=True).start(); return
        if action in ("prev", "next"):
            self.target_index = max(0, min(len(self.store.targets) - 1, self.target_index + (-1 if action == "prev" else 1)))
            return
        if action == "delete":
            if self.store.targets:
                p = self.store.targets[min(self.target_index, len(self.store.targets) - 1)]
                self.store.post("/api/targets/remove", {"id": p["id"]}, meta)
                self.target_index = max(0, self.target_index - 1)
            return
        if action == "clear_cal": self.page = "confirm_clear"; return
        if action == "confirm_clear":
            self.store.post("/api/calibration/clear", {}, meta)
            self.selection = None; self.page = "live"; self.message = "标定及目标点已清除"; return

    def tap(self, x, y, meta):
        for (bx, by, w, h), action in self.buttons:
            if bx <= x < bx + w and by <= y < by + h:
                self.action(action, meta); return
        if self.page in ("live", "corners") and 0 <= x < self.vw and 32 <= y < 32 + self.vh:
            pixel = [x * 640 / self.vw, (y - 32) * 480 / self.vh]
            if self.page == "corners":
                if len(self.corners) < 4: self.corners.append(pixel)
            else:
                self.selection = None
                self.selection = self.store.post("/api/point", {"pixel": pixel}, meta)
                self.message = f"X {self.selection['x']:.3f}  Y {self.selection['y']:.3f} m"

    def draw(self, frame, meta):
        image = self.image
        cal_id = self.store.cal.created_at if self.store.cal else None
        if cal_id != self.calibration_id:
            self.selection = None; self.calibration_id = cal_id
            if self.page == "corners": self.page = "live"; self.frozen = None
        canvas = image.Image(self.width, self.height, image.Format.FMT_RGB888)
        self.buttons = []
        self.text(canvas, 6, 5, f"地面定位  {meta.get('fps', 0):.1f} fps  · 本机已保存 {len(self.store.targets)} 点")
        if self.page in ("live", "corners"):
            if self.page == "corners":
                if self.frozen is None: self.frozen = frame.resize(self.vw, self.vh)
                preview = self.frozen.copy()
            else: preview = frame.resize(self.vw, self.vh)
            def pos(p): return [int(p[0] * self.vw / 640), int(p[1] * self.vh / 480)]
            if self.store.compatible(meta) and self.page == "live":
                for line in self.store.grid:
                    points = [pos(p) for p in line["points"]]
                    for a, b in zip(points, points[1:]):
                        if all(-1000 < v < 2000 for v in (*a, *b)):
                            preview.draw_line(*a, *b, image.COLOR_GREEN)
                for p in self.store.targets:
                    preview.draw_circle(*pos(p["pixel"]), 4, image.COLOR_BLUE, thickness=2)
            if self.page == "live":
                for obj in meta.get("detections", []):
                    x, y, w, h = obj["bbox"]
                    p = pos([x, y]); q = pos([x + w, y + h])
                    preview.draw_rect(*p, max(1, q[0] - p[0]), max(1, q[1] - p[1]), image.COLOR_YELLOW)
                    world = obj.get("world")
                    label = obj["label"] + (f" {world['x']:.2f},{world['y']:.2f}m" if world and world["inside"] else "")
                    self.text(preview, p[0], max(0, p[1] - 18), label, image.COLOR_YELLOW)
                if self.selection: preview.draw_circle(*pos(self.selection["pixel"]), 6, image.COLOR_WHITE, thickness=2)
            else:
                for i, p in enumerate(self.corners):
                    x, y = pos(p); preview.draw_circle(x, y, 5, image.COLOR_YELLOW, thickness=2)
                    self.text(preview, min(self.vw - 20, x + 6), max(0, y - 22), "ABCD"[i], image.COLOR_YELLOW)
            canvas.draw_image(0, 32, preview)
            if self.page == "corners":
                self.side_buttons(canvas, [(f"已选 {len(self.corners)}/4", "noop"), ("撤销上一点", "undo"),
                    ("应用并保存", "apply"), ("取消标定", "back")])
            else:
                mode_name = MODE_NAMES[MODES.index(meta.get("mode", "off"))]
                self.side_buttons(canvas, [("场地标定", "calibrate"), ("保存选点", "save"), ("目标点列表", "targets"),
                    (mode_name, "mode"), ("清除标定", "clear_cal"), ("退出应用", "exit")])
        elif self.page == "size":
            self.text(canvas, 12, 37, "输入实际场地尺寸，单位：米")
            for i, label in enumerate(("宽 X", "长 Y")):
                self.button(canvas, (12 + i * self.width // 2, 68, self.width // 2 - 20, 38),
                            f"{'>' if self.field == i else ''}{label}: {self.fields[i]}", f"field:{i}")
            key_w = (self.width - 155) // 3; key_h = (self.height - 145) // 4
            for i, key in enumerate(("1", "2", "3", "4", "5", "6", "7", "8", "9", ".", "0", "←")):
                self.button(canvas, (12 + (i % 3) * key_w, 115 + (i // 3) * key_h, key_w - 5, key_h - 4), key, "key:" + key)
            self.button(canvas, (self.width - 135, 125, 125, 60), "下一步选点", "freeze")
            self.button(canvas, (self.width - 135, 205, 125, 60), "取消", "back")
        elif self.page == "targets":
            points = self.store.targets
            self.text(canvas, 12, 42, f"设备本地目标点 / 共 {len(points)} 个")
            start = max(0, self.target_index - 2)
            for row, p in enumerate(points[start:start + 5]):
                self.text(canvas, 12, 76 + row * 30, f"{'>' if start+row == self.target_index else ' '} {p['name']}   {p['x']:.3f}, {p['y']:.3f} m")
            y = self.height - 132
            self.button(canvas, (8, y, self.width - 16, 42), '发送目标点到树莓派', 'send')
            y += 50
            for i, (label, action) in enumerate((("上一点", "prev"), ("下一点", "next"), ("删除选中", "delete"), ("返回", "back"))):
                self.button(canvas, (8 + i * self.width // 4, y, self.width // 4 - 12, 42), label, action)
        elif self.page == "confirm_clear":
            self.text(canvas, 14, 70, "清除本机标定及全部目标点？")
            self.text(canvas, 14, 105, "重新标定也会清除旧坐标系的目标点。")
            self.button(canvas, (14, 170, 180, 55), "确认清除", "confirm_clear")
            self.button(canvas, (215, 170, 160, 55), "取消", "back")
        footer = self.message
        if self.store.load_error: footer = "本机数据读取失败，请备份后重新标定"
        self.text(canvas, 5, self.height - 25, footer[:35], image.COLOR_YELLOW)
        self.display.show(canvas)
        if time.monotonic() - self.last_capture > 1:
            self.screen_jpeg = canvas.to_jpeg(quality=80).to_bytes()
            self.last_capture = time.monotonic()
        x, y, pressed = self.touch.read()
        if pressed and not self.pressed:
            try:
                with self.store.lock: self.tap(x, y, meta)
            except (ValueError, KeyError, TypeError, OSError) as e: self.message = str(e)
        self.pressed = bool(pressed)

    def close(self):
        self.touch.close(); self.display.close()
