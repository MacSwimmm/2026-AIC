"""Pure pixel conversion for local drawing; never used as recognition input."""
import ctypes as c
from pathlib import Path


class Pixels:
    def __init__(self):
        self.lib=c.CDLL(str(Path(__file__).with_name('_pixel_ops.so')))
        self.to_rgb=self.lib.nv21_to_rgb;self.to_nv21=self.lib.rgb_to_nv21
        for f in (self.to_rgb,self.to_nv21):
            f.argtypes=[c.c_void_p,c.c_void_p,c.c_int,c.c_int];f.restype=c.c_int

    @staticmethod
    def size(frame):
        w,h=frame.width(),frame.height()
        if w<=0 or h<=0 or w%2 or h%2:raise ValueError('NV21 needs even image dimensions')
        return w,h

    def rgb(self,frame):
        from maix import image
        w,h=self.size(frame)
        if frame.format()!=image.Format.FMT_YVU420SP:raise ValueError('Expected NV21')
        raw=frame.to_bytes(copy=False)
        if len(raw)!=w*h*3//2:raise ValueError('Invalid NV21 allocation')
        result=image.Image(w,h,image.Format.FMT_RGB888)
        # This is a view of result's allocation, retained until the C call ends.
        view=image.image2cv(result,ensure_bgr=False,copy=False)
        if self.to_rgb(c.c_char_p(raw),view.ctypes.data,w,h):raise RuntimeError('RGB conversion failed')
        return result

    def nv21(self,frame):
        from maix import image
        w,h=self.size(frame)
        if frame.format()!=image.Format.FMT_RGB888:raise ValueError('Expected RGB')
        raw=frame.to_bytes(copy=False)
        if len(raw)!=w*h*3:raise ValueError('Invalid RGB allocation')
        output=c.create_string_buffer(w*h*3//2)
        if self.to_nv21(c.c_char_p(raw),output,w,h):raise RuntimeError('NV21 conversion failed')
        # The returned image owns a copy; it never references this temporary.
        return image.from_bytes(w,h,image.Format.FMT_YVU420SP,output.raw,copy=True)
