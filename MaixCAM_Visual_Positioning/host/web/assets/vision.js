/* ControlDesk vision application: all physical coordinates come from the backend. */
(() => {
  'use strict';
  const $ = s => document.querySelector(s);
  const canvas=$('#cameraCanvas'),ctx=canvas.getContext('2d');
  const map=$('#mapCanvas'),mc=map.getContext('2d');
  let status=null,image=null,frameMeta=null,frozen=false,calibrating=false,corners=[],selection=null,lens=null,mapRect=null;
  let lastTargetMarkup='',lastDetMarkup='',tab='calibrate',framePending=false;
  const cornerNames=['A · 原点 (0, 0)','B · X 端 (宽度, 0)','C · 对角 (宽度, 长度)','D · Y 端 (0, 长度)'];
  const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const format=n=>Number(n).toFixed(3);
  const live=()=>Boolean(status?.device.connected);
  const calibrated=()=>Boolean(status?.calibration_compatible);
  function message(text,error=false){$('#message').textContent=text;$('#message').classList.toggle('error',error);}
  async function api(path,data){
    const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)};
    const r=await fetch(path,{...options,cache:'no-store'});
    const result=await r.json();
    if(!r.ok)throw new Error(result.error||'操作失败');
    return result;
  }
  function action(selector,fn){$(selector).addEventListener('click',async()=>{try{await fn();}catch(e){message(e.message,true);}});}
  function updateCorners(){
    $('#cornerList').innerHTML=cornerNames.map((name,i)=>`<li class="${i<corners.length?'done':calibrating&&i===corners.length?'next':''}">${name}${corners[i]?` · ${Math.round(corners[i][0])}, ${Math.round(corners[i][1])} px`:''}</li>`).join('');
    $('#undoCorner').disabled=!calibrating||!corners.length;
    $('#cancelCalibrate').disabled=!calibrating;
    $('#applyCalibration').disabled=!calibrating||corners.length!==4||!live();
  }
  function setFrozen(value){
    frozen=value;$('#freeze').textContent=frozen?'恢复实时画面':'冻结画面';draw();
  }
  function setTab(name){
    tab=name;
    for(const b of document.querySelectorAll('[data-tab]')){const active=b.dataset.tab===name;b.classList.toggle('active',active);b.setAttribute('aria-selected',String(active));$('#pane-'+b.dataset.tab).hidden=!active;}
  }
  function draw(){
    const W=canvas.width,H=canvas.height;
    ctx.clearRect(0,0,W,H);
    if(image)ctx.drawImage(image,0,0,W,H);
    const scale=W/640;
    ctx.lineWidth=1.3*scale;
    if(calibrated()&&$('#showGrid').checked&&!calibrating){
      for(const line of status.grid){
        ctx.strokeStyle='rgba(54,245,143,.55)';ctx.beginPath();
        line.points.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));ctx.stroke();
      }
      const p=status.calibration.points;ctx.strokeStyle='#65ffb0';ctx.lineWidth=2*scale;ctx.beginPath();p.forEach((v,i)=>i?ctx.lineTo(...v):ctx.moveTo(...v));ctx.closePath();ctx.stroke();
      p.forEach((v,i)=>mark(ctx,v,String.fromCharCode(65+i),'#57ff9e',scale));
    }
    if(calibrating){
      if(corners.length){ctx.strokeStyle='#f0d995';ctx.lineWidth=2*scale;ctx.beginPath();corners.forEach((v,i)=>i?ctx.lineTo(...v):ctx.moveTo(...v));if(corners.length===4)ctx.closePath();ctx.stroke();}
      corners.forEach((v,i)=>mark(ctx,v,String.fromCharCode(65+i),'#f0d995',scale));
    }
    for(const obj of live()&&!frozen?frameMeta?.detections||[]:[]){
      const [x,y,w,h]=obj.bbox;
      ctx.strokeStyle='#ffce79';ctx.lineWidth=1.5*scale;ctx.strokeRect(x,y,w,h);
      ctx.fillStyle='#07120dd9';ctx.fillRect(x,Math.max(0,y-20*scale),Math.min(W-x,190*scale),20*scale);
      ctx.fillStyle='#ffdda7';ctx.font=`${12*scale}px Arial`;ctx.fillText(`${obj.label} ${Math.round(obj.score*100)}%`,x+4*scale,Math.max(14*scale,y-5*scale));
      mark(ctx,obj.anchor,'','#ffce79',scale);
    }
    if(calibrated())for(const point of status.targets)mark(ctx,point.pixel,'●','#a7caff',scale);
    if(selection&&calibrated())mark(ctx,selection.pixel,'＋','#f4f9f6',scale);
    $('#cameraEmpty').hidden=Boolean(image);
    $('#stageTag').textContent=frozen?'FROZEN / 静止画面':live()?'LIVE / 实时画面':'OFFLINE / 画面已断开';
    if(!live()&&image){ctx.fillStyle='#080c09aa';ctx.fillRect(0,0,W,H);ctx.fillStyle='#f1c8a4';ctx.font=`${18*scale}px Arial`;ctx.textAlign='center';ctx.fillText('画面已断开 · 请重新连接',W/2,H/2);ctx.textAlign='left';}
    drawMap();
  }
  function mark(c,p,label,color,scale=1){
    c.beginPath();c.fillStyle='#08100c';c.strokeStyle=color;c.lineWidth=2*scale;c.arc(p[0],p[1],5*scale,0,Math.PI*2);c.fill();c.stroke();
    if(label){c.font=`${14*scale}px Arial`;const x=Math.min(c.canvas.width-25*scale,Math.max(5*scale,p[0]+9*scale));const y=Math.max(18*scale,p[1]-8*scale);c.fillStyle='#07120de0';c.fillRect(x-2*scale,y-14*scale,c.measureText(label).width+5*scale,18*scale);c.fillStyle=color;c.fillText(label,x,y);}
  }
  function drawMap(){
    const rect=map.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2),W=Math.max(260,rect.width),H=rect.height;
    map.width=Math.round(W*dpr);map.height=Math.round(H*dpr);mc.setTransform(dpr,0,0,dpr,0,0);mc.clearRect(0,0,W,H);
    mapRect=null;
    if(!calibrated()){mc.fillStyle='#90aa9a';mc.font='13px Arial';mc.textAlign='center';mc.fillText('完成地面标定后显示坐标图',W/2,H/2);mc.textAlign='left';return;}
    const c=status.calibration,margin=48,s=Math.min((W-margin-25)/c.width_m,(H-margin-30)/c.height_m);
    const x=margin+(W-margin-25-c.width_m*s)/2,y=22+(H-margin-30-c.height_m*s)/2;
    mapRect={x,y,s,w:c.width_m*s,h:c.height_m*s};
    const pos=(X,Y)=>[x+X*s,y+(c.height_m-Y)*s];
    mc.lineWidth=1;mc.strokeStyle='#284b35';mc.font='11px Arial';
    const step=Math.max(.5,Math.ceil(Math.max(c.width_m,c.height_m)/10*2)/2);
    for(let X=0;X<c.width_m+1e-6;X+=step){const p=pos(X,0),q=pos(X,c.height_m);mc.beginPath();mc.moveTo(...p);mc.lineTo(...q);mc.stroke();mc.fillStyle='#a2b8ab';mc.textAlign='center';if(c.width_m/step<8||Math.round(X/step)%2===0)mc.fillText(Number(X.toFixed(2)),p[0],p[1]+18);}
    for(let Y=0;Y<c.height_m+1e-6;Y+=step){const p=pos(0,Y),q=pos(c.width_m,Y);mc.beginPath();mc.moveTo(...p);mc.lineTo(...q);mc.stroke();mc.fillStyle='#a2b8ab';mc.textAlign='right';if(c.height_m/step<8||Math.round(Y/step)%2===0)mc.fillText(Number(Y.toFixed(2)),p[0]-10,p[1]+4);}
    mc.strokeStyle='#56b67b';mc.strokeRect(x,y,c.width_m*s,c.height_m*s);mc.fillStyle='#c0dbc9';mc.textAlign='right';mc.fillText('X / m',W-12,H-8);mc.textAlign='left';mc.fillText('Y / m',12,16);mc.fillText('A',x-16,y+c.height_m*s+18);
    for(const obj of status.detections||[])if(obj.world?.inside){const p=pos(obj.world.x,obj.world.y);mc.beginPath();mc.fillStyle='#ffce79';mc.arc(...p,4,0,Math.PI*2);mc.fill();}
    for(const target of status.targets){const p=pos(target.x,target.y);mc.fillStyle='#a7caff';mc.beginPath();mc.arc(...p,4,0,Math.PI*2);mc.fill();mc.fillText(target.name.slice(0,10),Math.min(W-85,p[0]+8),Math.max(16,p[1]-7));}
    if(selection){const p=pos(selection.x,selection.y);mc.strokeStyle='#f1fff5';mc.lineWidth=2;mc.beginPath();mc.arc(...p,7,0,Math.PI*2);mc.stroke();}
  }
  function clearSelection(){selection=null;$('#coordinate').innerHTML='X —　Y — <small>m</small>';$('#saveTarget').disabled=true;$('#addCheck').disabled=true;}
  function render(){
    if(!status)return;
    const s=status.device,meta=s.metadata||{},online=s.connected;
    $('#connectionState').className='connection '+(online?'online':s.reconnecting?'retrying':'offline');$('#connectionState span').textContent=s.busy?'连接中…':online?'相机在线':s.reconnecting?'恢复连接中…':'未连接';
    $('#connect').disabled=s.busy;$('#disconnect').disabled=(!online&&!s.reconnecting)||s.busy;
    $('#connect').textContent=online||s.reconnecting?'重新连接':s.busy?'连接中…':'连接相机';
    $('#freeze').disabled=!online||calibrating;$('#startCalibrate').disabled=!online;$('#applyMode').disabled=!online;
    $('#frameInfo').textContent=online?`${meta.width} × ${meta.height} / ${meta.fps||'—'} fps / ${meta.mode||'off'}`:'等待实时画面';
    $('#calibrationInfo').textContent=calibrated()?`${status.calibration.width_m} × ${status.calibration.height_m} m / ${status.calibration.validation.count?'已录入实测点':'待实测校验'}`:status.calibration?'已保存标定 / 等待匹配相机':'尚未标定';
    for(const id of ['exportTargets','exportCSV'])$("#"+id).setAttribute('aria-disabled',String(!calibrated()));
    $('#exportCalibration').setAttribute('aria-disabled',String(!status.calibration));$('#snapshot').setAttribute('aria-disabled',String(!online));
    if(!calibrated())clearSelection();
    $('#saveTarget').disabled=!calibrated()||!selection||calibrating;$('#addCheck').disabled=!calibrated()||!selection||calibrating;
    if(!online&&calibrating&&!s.reconnecting){calibrating=false;corners=[];setFrozen(false);message('相机连接已中断，请重新连接后重新选取标定点。',true);}
    updateCorners();
    const targetHTML=(status.targets||[]).map(t=>`<div class="target-row"><div><b>${escape(t.name)}</b><span>地面目标 · ${escape(t.id)}</span></div><code class="target-value">${format(t.x)}, ${format(t.y)} m</code><button data-remove="${escape(t.id)}" aria-label="删除 ${escape(t.name)}">×</button></div>`).join('')||'<p class="help">点击可见地面，再保存为目标点。</p>';
    if(targetHTML!==lastTargetMarkup){$('#targetList').innerHTML=targetHTML;lastTargetMarkup=targetHTML;}
    $('#targetCount').textContent=(status.targets?.length||0)+' 个';
    const detHTML=(status.detections||[]).map(o=>`<div class="detection-row"><div><b>${escape(o.label)}</b><span>${Math.round(o.score*100)}% · ${o.anchor_kind==='bbox_bottom_approx'?'框底中心近似':'假设标记贴地'}</span></div><code>${o.world?.inside?`${format(o.world.x)}, ${format(o.world.y)} m`:'未标定 / 区域外'}</code></div>`).join('')||'<p class="help">暂无识别结果</p>';
    if(detHTML!==lastDetMarkup){$('#detectionList').innerHTML=detHTML;lastDetMarkup=detHTML;}
    const v=status.calibration?.validation;
    $('#validationResults').textContent=v?.count?`${v.count} 个独立实测点\n均方根误差 ${(v.rmse_m*100).toFixed(1)} cm\n最大误差 ${(v.max_error_m*100).toFixed(1)} cm`:'还没有独立实测点';
    draw();
  }
  let previousDeviceMessage='',syncCal=false;
  async function pollStatus(){
    try{
      status=await api('/api/status');
      if(!syncCal&&status.calibration){$('#widthM').value=status.calibration.width_m;$('#heightM').value=status.calibration.height_m;lens=status.calibration.lens;updateLens();syncCal=true;}
      const msg=status.device.message;
      if(msg!==previousDeviceMessage){previousDeviceMessage=msg;message(msg,!status.device.connected&&!status.device.busy&&Boolean(status.device.host));}
      render();
    }catch(e){message('上位机服务连接中断：'+e.message,true);if(status){status.device.connected=false;render();}}
    setTimeout(pollStatus,500);
  }
  async function pollFrame(){
    if(!frozen&&live()&&!framePending){
      framePending=true;let url;
      try{
        const r=await fetch('/api/frame.jpg',{cache:'no-store'});if(!r.ok)throw new Error('没有可用画面');
        const meta=JSON.parse(atob(r.headers.get('X-Frame-Meta')));
        const blob=await r.blob();url=URL.createObjectURL(blob);
        const next=new Image();next.src=url;await next.decode();
        if(!frozen){image=next;frameMeta=meta;canvas.width=next.naturalWidth;canvas.height=next.naturalHeight;draw();}
      }catch(e){/* Status polling exposes connection failures. */}
      finally{if(url)URL.revokeObjectURL(url);framePending=false;}
    }
    setTimeout(pollFrame,110);
  }
  async function selectPoint(body){
    clearSelection();
    try{selection=await api('/api/point',body);$('#coordinate').innerHTML=`X ${format(selection.x)}　Y ${format(selection.y)} <small>m</small>`;$('#selectionHint').textContent='当前地面选点 / 相对原点 A';$('#saveTarget').disabled=false;$('#addCheck').disabled=false;
      const [x,y]=selection.pixel;
      const onObject=(frameMeta?.detections||[]).some(o=>x>=o.bbox[0]&&x<=o.bbox[0]+o.bbox[2]&&y>=o.bbox[1]&&y<=o.bbox[1]+o.bbox[3]);
      message(onObject?'该点落在识别框内：请确认选的是可见地面，物体顶部的换算值不代表其落地位置。':'已换算地面坐标，可保存为目标点。',onObject);draw();
    }catch(e){message(e.message,true);}
  }
  canvas.addEventListener('click',event=>{
    if(!live()||!image){message('请先连接相机并等待画面。',true);return;}
    const r=canvas.getBoundingClientRect(),pixel=[(event.clientX-r.left)*canvas.width/r.width,(event.clientY-r.top)*canvas.height/r.height];
    if(calibrating){if(corners.length===4){message('已选满四点，可应用标定或撤销后调整。');return;}corners.push(pixel);updateCorners();message(corners.length===4?'四个角点已选好，点击「应用标定」。':`接下来选择 ${cornerNames[corners.length]}。`);draw();}
    else if(calibrated())selectPoint({pixel});
    else message('先在右侧完成四个地面角点的标定。',true);
  });
  map.addEventListener('click',event=>{if(!mapRect||!calibrated())return;const r=map.getBoundingClientRect(),x=event.clientX-r.left,y=event.clientY-r.top,m=mapRect;if(x<m.x||x>m.x+m.w||y<m.y||y>m.y+m.h)return;selectPoint({world:[(x-m.x)/m.s,status.calibration.height_m-(y-m.y)/m.s]});});
  action('#connect',async()=>{calibrating=false;corners=[];setFrozen(false);clearSelection();await api('/api/connect',{host:$('#deviceHost').value,username:$('#deviceUser').value||'root',password:$('#devicePassword').value||'root'});message('正在连接设备并启动实时画面…');});
  action('#disconnect',async()=>{await api('/api/disconnect',{});clearSelection();message('已断开，设备将返回原应用菜单。');});
  action('#freeze',()=>{setFrozen(!frozen);message(frozen?'画面已冻结，坐标仍使用当前相机标定。':'已恢复实时画面。');});
  action('#startCalibrate',()=>{if(!image)throw new Error('请等待画面加载');setFrozen(true);calibrating=true;corners=[];clearSelection();updateCorners();message('先选 A：你希望作为 (0,0) 原点的地面角点。');draw();});
  action('#undoCorner',()=>{corners.pop();updateCorners();draw();});
  action('#cancelCalibrate',()=>{calibrating=false;corners=[];setFrozen(false);updateCorners();message('已取消本次选点。');});
  action('#applyCalibration',async()=>{const c=await api('/api/calibration',{width_m:Number($('#widthM').value),height_m:Number($('#heightM').value),points:corners,lens});status.calibration=c;calibrating=false;corners=[];setFrozen(false);message('标定已保存。请点击独立实测点，在「精度校验」中检查现场误差。');});
  action('#clearCalibration',async()=>{await api('/api/calibration/clear',{});calibrating=false;corners=[];clearSelection();setFrozen(false);message('标定及其目标点已清除。');});
  action('#saveTarget',async()=>{if(!selection)return;await api('/api/targets',{pixel:selection.pixel});message('地面目标点已保存。');});
  action('#clearTargets',async()=>{await api('/api/targets/clear',{});message('目标点已清空。');});
  $('#targetList').addEventListener('click',async e=>{const b=e.target.closest('[data-remove]');if(b)try{await api('/api/targets/remove',{id:b.dataset.remove});}catch(err){message(err.message,true);}});
  action('#applyMode',async()=>{await api('/api/mode',{mode:$('#detectionMode').value});message('识别方式已提交，等待相机切换。');});
  action('#addCheck',async()=>{if(!selection)return;const check=await api('/api/check',{pixel:selection.pixel,actual:[Number($('#actualX').value),Number($('#actualY').value)]});message(`独立点误差 ${(check.error_m*100).toFixed(1)} cm，已记录。`);});
  action('#importCalibration',()=>$('#calibrationFile').click());
  $('#calibrationFile').addEventListener('change',async e=>{try{const f=e.target.files[0];if(!f)return;const data=JSON.parse(await f.text());await api('/api/calibration/import',data);syncCal=false;clearSelection();message('已导入标定。请确认相机位置未变化，并用实测点重新核验。');}catch(err){message(err.message,true);}finally{e.target.value='';}});
  function updateLens(){$('#lensInfo').textContent=lens?`已载入 ${lens.image_size?.join(' × ')} / Brown 畸变参数`:'未启用畸变校正';}
  action('#importLens',()=>$('#lensFile').click());
  $('#lensFile').addEventListener('change',async e=>{try{const f=e.target.files[0];if(!f)return;lens=JSON.parse(await f.text());updateLens();message('镜头参数已载入；重新选择四点并应用标定后生效。');}catch(err){message(err.message,true);}finally{e.target.value='';}});
  action('#clearLens',()=>{lens=null;updateLens();message('镜头参数已移除；重新标定后生效。');});
  $('#showGrid').addEventListener('change',draw);
  document.querySelectorAll('[data-tab]').forEach(b=>b.addEventListener('click',()=>setTab(b.dataset.tab)));
  new ResizeObserver(drawMap).observe(map);
  // Reuse the template's actual dot-field renderer and footer displacement grid.
  const hero=$('#pixelField'),field=new ControlDeskPixels(hero),reduce=matchMedia('(prefers-reduced-motion: reduce)');let visible=true;
  new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;}).observe(hero);
  function dots(){if(visible&&!document.hidden){const r=hero.getBoundingClientRect();if(field.width!==r.width||field.height!==r.height)field.resize(r.width,r.height,Math.min(devicePixelRatio||1,1.5));field.paint(performance.now(),{speed:.5,spacing:9},'#26ed89',reduce.matches);}setTimeout(dots,reduce.matches?1000:100);}
  pollStatus();pollFrame();dots();
})();
