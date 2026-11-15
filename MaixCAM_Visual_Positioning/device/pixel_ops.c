/* BT.601 limited-range NV21/RGB conversion; no camera, SDK, allocator or global state. */
static unsigned char clip(int v){return v<0?0:v>255?255:v;}
__attribute__((visibility("default")))
int nv21_to_rgb(const unsigned char *src,unsigned char *dst,int w,int h){
    if(w<=0||h<=0||(w&1)||(h&1))return -1;
    const unsigned char *uv=src+w*h;
    for(int y=0;y<h;y++){
        const unsigned char *p=src+y*w,*c=uv+(y/2)*w;
        unsigned char *q=dst+y*w*3;
        for(int x=0;x<w;x+=2){
            int v=c[x]-128,u=c[x+1]-128;
            int r=409*v,g=-100*u-208*v,b=516*u;
            for(int k=0;k<2;k++){
                int yy=p[x+k]-16;int l=298*(yy<0?0:yy)+128;
                q[3*(x+k)]=clip((l+r)>>8);
                q[3*(x+k)+1]=clip((l+g)>>8);
                q[3*(x+k)+2]=clip((l+b)>>8);
            }
        }
    }
    return 0;
}
__attribute__((visibility("default")))
int rgb_to_nv21(const unsigned char *src,unsigned char *dst,int w,int h){
    if(w<=0||h<=0||(w&1)||(h&1))return -1;
    unsigned char *uv=dst+w*h;
    for(int y=0;y<h;y++)for(int x=0;x<w;x++){
        const unsigned char *p=src+3*(y*w+x);
        dst[y*w+x]=clip(((66*p[0]+129*p[1]+25*p[2]+128)>>8)+16);
        if(!(y&1)&&!(x&1)){
            int r=0,g=0,b=0;
            for(int dy=0;dy<2;dy++)for(int dx=0;dx<2;dx++){
                const unsigned char *s=src+3*((y+dy)*w+x+dx);r+=s[0];g+=s[1];b+=s[2];
            }
            r=(r+2)/4;g=(g+2)/4;b=(b+2)/4;
            uv[(y/2)*w+x]=clip(((112*r-94*g-18*b+128)>>8)+128);
            uv[(y/2)*w+x+1]=clip(((-38*r-74*g+112*b+128)>>8)+128);
        }
    }
    return 0;
}
