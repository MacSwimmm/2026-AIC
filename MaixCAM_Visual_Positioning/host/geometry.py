"""Pixel/ground homography with optional Brown-Conrady lens correction.

Only the Python standard library is required on the PC. Coordinates use metres,
with A=(0,0), B=(width,0), C=(width,height), D=(0,height).
"""
import math
import time


def number(value):
    if isinstance(value, bool):
        raise ValueError("请输入有效数字")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("坐标必须是有限数字")
    return result


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def inverse(h):
    a, b, c, d, e, f, g, j, k = sum(h, [])
    det = a * (e*k-f*j) - b * (d*k-f*g) + c * (d*j-e*g)
    if abs(det) < 1e-14:
        raise ValueError("标定点退化，请选择分布充分的四个地面角点")
    return [[v/det for v in row] for row in [[e*k-f*j, c*j-b*k, b*f-c*e], [f*g-d*k, a*k-c*g, c*d-a*f], [d*j-e*g, b*g-a*j, a*e-b*d]]]


def project(h, point):
    x, y = point
    z = h[2][0]*x + h[2][1]*y + h[2][2]
    if abs(z) < 1e-10:
        raise ValueError("该点接近地平线，无法可靠计算")
    return [(h[0][0]*x+h[0][1]*y+h[0][2])/z, (h[1][0]*x+h[1][1]*y+h[1][2])/z]


def solve(a, b):
    m = [list(row) + [v] for row, v in zip(a, b)]
    n = len(m)
    for i in range(n):
        pivot = max(range(i, n), key=lambda j: abs(m[j][i]))
        if abs(m[pivot][i]) < 1e-10:
            raise ValueError("标定点过于接近或共线，请重新选择")
        m[i], m[pivot] = m[pivot], m[i]
        scale = m[i][i]
        m[i] = [v/scale for v in m[i]]
        for j in range(n):
            if j != i:
                scale = m[j][i]
                m[j] = [x-scale*y for x, y in zip(m[j], m[i])]
    return [row[-1] for row in m]


def normalize(points):
    x = sum(p[0] for p in points)/len(points)
    y = sum(p[1] for p in points)/len(points)
    mean_distance = sum(math.hypot(p[0]-x, p[1]-y) for p in points)/len(points)
    if mean_distance < 1e-8:
        raise ValueError("标定点距离太近")
    s = math.sqrt(2)/mean_distance
    t = [[s, 0, -s*x], [0, s, -s*y], [0, 0, 1]]
    return [project(t, p) for p in points], t


def homography(src, dst):
    p, t = normalize(src)
    q, u = normalize(dst)
    a, b = [], []
    for (x, y), (X, Y) in zip(p, q):
        a.extend([[x,y,1,0,0,0,-X*x,-X*y], [0,0,0,x,y,1,-Y*x,-Y*y]])
        b.extend([X,Y])
    v = solve(a, b)
    h = multiply(inverse(u), multiply([v[:3], v[3:6], v[6:]+[1]], t))
    s = max(abs(x) for row in h for x in row)
    return [[x/s for x in row] for row in h]


def convex(points):
    crosses = []
    for i in range(4):
        a,b,c = points[i],points[(i+1)%4],points[(i+2)%4]
        crosses.append((b[0]-a[0])*(c[1]-b[1])-(b[1]-a[1])*(c[0]-b[0]))
    return all(x > 1e-6 for x in crosses) or all(x < -1e-6 for x in crosses)


class Lens:
    def __init__(self, data=None, size=None):
        self.data = None
        if data:
            if data.get("model", "brown") != "brown":
                raise ValueError("镜头参数仅支持 Brown 模型，鱼眼模型不能直接导入")
            self.fx,self.fy,self.cx,self.cy = [number(data[k]) for k in ("fx","fy","cx","cy")]
            if self.fx <= 0 or self.fy <= 0:
                raise ValueError("镜头焦距参数必须大于零")
            if list(data.get("image_size", [])) != list(size or []):
                raise ValueError("镜头参数分辨率与当前画面不一致")
            dist = list(data.get("dist", []))
            if len(dist) not in (4,5):
                raise ValueError("镜头畸变参数需要 4 或 5 项")
            self.k1,self.k2,self.p1,self.p2,self.k3 = [number(x) for x in (dist+[0])[:5]]
            self.data = dict(data)

    def _distort(self,x,y):
        r = x*x+y*y
        radial = 1+self.k1*r+self.k2*r*r+self.k3*r*r*r
        return [x*radial+2*self.p1*x*y+self.p2*(r+2*x*x), y*radial+self.p1*(r+2*y*y)+2*self.p2*x*y]

    def distort(self,p):
        if not self.data:
            return list(p)
        x,y = self._distort((p[0]-self.cx)/self.fx,(p[1]-self.cy)/self.fy)
        return [x*self.fx+self.cx, y*self.fy+self.cy]

    def undistort(self,p):
        if not self.data:
            return list(p)
        xd,yd = (p[0]-self.cx)/self.fx,(p[1]-self.cy)/self.fy
        x,y = xd,yd
        for _ in range(40):
            dx,dy = self._distort(x,y)
            if math.hypot(dx-xd,dy-yd) < 1e-11:
                return [x*self.fx+self.cx,y*self.fy+self.cy]
            eps=1e-6
            ax,ay=self._distort(x+eps,y)
            bx,by=self._distort(x,y+eps)
            j11,j21,j12,j22=(ax-dx)/eps,(ay-dy)/eps,(bx-dx)/eps,(by-dy)/eps
            det=j11*j22-j12*j21
            if abs(det)<1e-12:
                break
            ex,ey=dx-xd,dy-yd
            x-=(j22*ex-j12*ey)/det
            y-=(-j21*ex+j11*ey)/det
            if not math.isfinite(x+y) or abs(x)+abs(y)>20:
                break
        raise ValueError("镜头畸变校正未收敛，请检查镜头参数")


class Calibration:
    def __init__(self, data):
        self.width,self.height = number(data["width_m"]),number(data["height_m"])
        if not (0 < self.width <= 1000 and 0 < self.height <= 1000):
            raise ValueError("区域宽高需要大于零且不超过 1000 米")
        self.size = [int(number(x)) for x in data["image_size"]]
        if len(self.size)!=2 or min(self.size)<32:
            raise ValueError("缺少有效的画面分辨率")
        self.points = [[number(x) for x in p] for p in data["points"]]
        if len(self.points)!=4 or any(len(p)!=2 for p in self.points):
            raise ValueError("请依次选择 A、B、C、D 四个角点")
        if any(not (0<=p[0]<self.size[0] and 0<=p[1]<self.size[1]) for p in self.points):
            raise ValueError("标定点必须在画面内")
        self.lens=Lens(data.get("lens"),self.size)
        points=[self.lens.undistort(p) for p in self.points]
        area=abs(sum(points[i][0]*points[(i+1)%4][1]-points[(i+1)%4][0]*points[i][1] for i in range(4)))/2
        if not convex(points) or area<self.size[0]*self.size[1]*.003:
            raise ValueError("四点应沿实际矩形的边界依次选取，不能交叉或过于集中")
        world=[[0,0],[self.width,0],[self.width,self.height],[0,self.height]]
        self.h=homography(points,world)
        self.inv=inverse(self.h)
        # A horizon crossing would make even a convex patch unusable.
        ds=[self.h[2][0]*p[0]+self.h[2][1]*p[1]+self.h[2][2] for p in points]
        if min(ds)*max(ds)<=0:
            raise ValueError("标定区域跨越地平线，请重新选择地面区域")
        self.device_id=str(data.get("device_id", ""))
        self.geometry_id=str(data.get("geometry_id", "640x480-rgb-v1"))
        self.created_at=number(data.get("created_at") or time.time())
        self.checks=[]
        for item in data.get("checks",[])[:100]:
            self.check(item["pixel"],item["actual"])

    def to_world(self,p):
        x,y=project(self.h,self.lens.undistort([number(v) for v in p]))
        inside=-1e-7<=x<=self.width+1e-7 and -1e-7<=y<=self.height+1e-7
        return {"x":x,"y":y,"inside":inside}

    def to_pixel(self,p):
        return self.lens.distort(project(self.inv,p))

    def grid(self,step=.5):
        step=number(step)
        if step<=0:
            raise ValueError("网格间距需要大于零")
        step=max(step,max(self.width,self.height)/80)
        lines=[]
        for axis,length,other in [(0,self.width,self.height),(1,self.height,self.width)]:
            values=[i*step for i in range(int(length/step)+1)]
            if length-values[-1]>1e-7:values.append(length)
            for v in values:
                points=[]
                for i in range(25):
                    p=[v,other*i/24] if axis==0 else [other*i/24,v]
                    points.append(self.to_pixel(p))
                lines.append({"axis":axis,"value":v,"points":points})
        return lines

    def check(self,pixel,actual):
        if len(pixel)!=2 or len(actual)!=2:
            raise ValueError("验证点需要像素和实际二维坐标")
        p=[number(x) for x in pixel];a=[number(x) for x in actual]
        got=self.to_world(p)
        if not got["inside"] or not (0<=a[0]<=self.width and 0<=a[1]<=self.height):
            raise ValueError("验证点需要位于标定区域内")
        if min(math.hypot(p[0]-q[0],p[1]-q[1]) for q in self.points)<3:
            raise ValueError("请使用四个标定角点以外的独立实测点")
        if any(math.hypot(p[0]-q["pixel"][0],p[1]-q["pixel"][1])<2 for q in self.checks):
            raise ValueError("这个实测点已记录，请选择另一个位置")
        item={"pixel":p,"actual":a,"estimated":[got["x"],got["y"]],"error_m":math.hypot(got["x"]-a[0],got["y"]-a[1])}
        self.checks.append(item)
        return item

    def export(self):
        errors=[p["error_m"] for p in self.checks]
        return {"schema":1,"width_m":self.width,"height_m":self.height,"image_size":self.size,"points":self.points,"lens":self.lens.data,"device_id":self.device_id,"geometry_id":self.geometry_id,"created_at":self.created_at,"checks":self.checks,"validation":{"count":len(errors),"rmse_m":math.sqrt(sum(e*e for e in errors)/len(errors)) if errors else None,"max_error_m":max(errors) if errors else None},"homography":self.h}
