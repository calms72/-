'use strict';
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="control-token"]').content;
let state = null, libraryKey = '', activityKey = '', lastJob = '', toastTimer;
const dirty = new Set();
const symbols = {open:'▥',grasp:'◉',fist:'▣',peace:'Ⅱ',point:'↑',thumb:'↗'};
function element(tag, attrs = {}, text = '') {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
  el.textContent = text;
  return el;
}
function alertMessage(message) {
  $('alert').textContent = message; $('alert').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('alert').hidden = true, 7000);
}
async function api(path, data) {
  const response = await fetch('/api/' + path, data === undefined ? {} : {
    method: 'POST', headers: {'Content-Type':'application/json','X-Control-Token':token}, body:JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '请求未完成');
  return result;
}
async function command(path, data = {}) {
  try { const result = await api(path,data); await refresh(); return result; }
  catch (error) { alertMessage(error.message); await refresh(); return null; }
}
function angleFromRaw(raw, joint) { return (raw-joint.zero_raw)/(joint.end_raw-joint.zero_raw)*joint.end_angle; }
function displayAngle(raw, joint) { return Math.max(0,Math.min(joint.end_angle,angleFromRaw(raw,joint))).toFixed(1); }
function timeString(value) { return value ? new Date(value).toLocaleTimeString('zh-CN',{hour12:false}) : '尚未确认'; }
function buildJoints() {
  $('joint-grid').replaceChildren();
  state.joints.forEach((joint, index) => {
    const id = joint.id;
    const card = element('article',{'class':'joint-card','id':`joint-card-${id}`,'aria-labelledby':`joint-name-${id}`});
    const top = element('div',{'class':'joint-top'});
    top.append(element('h4',{'id':`joint-name-${id}`},joint.name),element('span',{'class':'channel'},`ID / 0${id}`));
    const row = element('div',{'class':'angle-row'});
    const number = element('input',{'type':'number','id':`angle-${id}`,'class':'angle-input','min':'0','max':joint.end_angle,'step':'0.1','aria-label':`${joint.name}${joint.calibrated?'角度':'参考角度'}，单位度`});
    number.value = displayAngle((state.desired_positions || state.positions)[index],joint);
    row.append(number,element('span',{'class':'degree','aria-hidden':'true'},'°'),element('span',{'class':'angle-type','id':`angle-type-${id}`},joint.calibrated?'已标定':'参考角度'));
    const slider = element('input',{'type':'range','id':`slider-${id}`,'min':'0','max':joint.end_angle,'step':'0.1','aria-label':`${joint.name}角度调节，单位度`});
    slider.value = number.value;
    const scales = element('div',{'class':'range-labels','aria-hidden':'true'}); scales.append(element('span',{},'0°'),element('span',{},joint.end_angle+'°'));
    const bottom = element('div',{'class':'joint-bottom'}); const feedback = element('span');
    feedback.append(document.createTextNode('位置 '),element('strong',{'id':`raw-${id}`},String(state.positions[index])));
    const apply = element('button',{'type':'button','class':'button secondary apply','id':`apply-${id}`,'aria-label':`应用${joint.name}角度`},'应用');
    bottom.append(feedback,apply); card.append(top,row,slider,scales,bottom); $('joint-grid').append(card);
    function change(source, target) { target.value=source.value; dirty.add(id); card.classList.add('dirty'); }
    slider.addEventListener('input',()=>change(slider,number)); number.addEventListener('input',()=>change(number,slider));
    apply.addEventListener('click', async()=> {
      if (!number.reportValidity()) return;
      const result=await command('joint',{id,angle:Number(number.value),duration_ms:Number($('duration').value)});
      if (result) {dirty.delete(id);card.classList.remove('dirty');}
    });
  });
  $('gestures').replaceChildren();
  state.gestures.forEach(gesture=> {
    const button=element('button',{'type':'button','class':'gesture','data-gesture':gesture.id});
    const symbol=element('span',{'class':'gesture-symbol','aria-hidden':'true'},symbols[gesture.id]);
    const text=element('span');text.append(element('strong',{},gesture.name),element('small',{},gesture.hint));button.append(symbol,text);
    button.addEventListener('click',async()=>{const result=await command('gesture',{id:gesture.id,duration_ms:Number($('duration').value)});if(result)dirty.clear();});
    $('gestures').append(button);
  });
}
function renderLibrary() {
  const key=JSON.stringify(state.recordings.map(r=>[r.id,r.name,r.frames.length]));
  if(key!==libraryKey){
    libraryKey=key;$('recordings').replaceChildren();
    if(!state.recordings.length)$('recordings').append(element('div',{'class':'empty-state'},'这里将保存你的第一个动作序列。'));
    state.recordings.forEach(record=>{
      const item=element('article',{'class':'saved-item'}),icon=element('span',{'class':'saved-icon','aria-hidden':'true'},'≋');
      const info=element('div',{'class':'saved-info'});info.append(element('h4',{},record.name),element('p',{},`${record.frames.length} 个姿态 · ${new Date(record.created_at).toLocaleString('zh-CN',{hour12:false})}`));
      const actions=element('div',{'class':'saved-actions'}),play=element('button',{'class':'button secondary compact','data-play':record.id,'aria-label':`回放动作${record.name}`},'▶ 回放');
      play.addEventListener('click',()=>command(`recordings/${record.id}/play`,{}));
      actions.append(play,element('a',{'href':`/api/recordings/${record.id}/markdown`,'download':'','aria-label':`导出${record.name}的 Markdown 记录`},'MD ↓'),element('a',{'href':`/api/recordings/${record.id}/export`,'download':'','aria-label':`导出${record.name}的 JSON 数据`},'JSON ↓'));
      item.append(icon,info,actions);$('recordings').append(item);
    });
  }
  $('saved-count').textContent=state.recordings.length+' 个';
  document.querySelectorAll('[data-play]').forEach(button=>button.disabled=!state.connected||state.busy);
}
function renderHand() {
  [[3,112,151,53,45],[4,143,141,60,54],[5,175,144,56,47],[6,203,153,40,33]].forEach(([id,x,y,a,b])=>{
    const ratio=Math.max(0,Math.min(1,state.positions[id-1]/4095));
    const bend=ratio*1.7,midX=x+Math.sin(bend)*a*.35,midY=y-Math.cos(bend)*a;
    const endX=midX+Math.sin(bend*1.7)*b*.6,endY=midY-Math.cos(bend*1.7)*b;
    $(`finger-${id}`).setAttribute('d',`M${x} ${y}L${midX} ${midY}L${endX} ${endY}`);
  });
  const flex=state.positions[1]/4095,swing=state.positions[0]/2000;
  const x=67+swing*30,y=171+flex*20;
  $('finger-2').setAttribute('d',`M97 207L${x} ${y}L${54+swing*56+flex*23} ${139+flex*53}`);
}
function render() {
  $('simulation-banner').hidden=!state.simulate;
  const online=state.connected;
  $('connection').classList.toggle('online',online);$('connection').querySelector('span').textContent=online?(state.simulate?'模拟连接':'开发板在线'):'尚未连接';
  $('connect-btn').textContent=online?'重新连接':'连接开发板';$('connect-btn').disabled=state.busy;
  $('stop-btn').disabled=!online;$('serial-port').textContent=state.port;$('confirmed-at').textContent=timeString(state.confirmed_at);
  const count=state.joints.filter(j=>j.calibrated).length;$('mapping-label').textContent=count===6?'六个关节均已标定':`参考角度 · 已标定 ${count}/6`;
  state.joints.forEach((joint,index)=>{
    const id=joint.id;$(`raw-${id}`).textContent=state.positions[index];
    if(!dirty.has(id)&&document.activeElement!==$(`angle-${id}`)&&document.activeElement!==$(`slider-${id}`)){
      $(`angle-${id}`).value=displayAngle((state.desired_positions || state.positions)[index],joint);$(`slider-${id}`).value=$(`angle-${id}`).value;
    }
    $(`apply-${id}`).disabled=!online||state.busy;$(`slider-${id}`).disabled=!online||state.busy;$(`angle-${id}`).disabled=!online||state.busy;
    $(`joint-card-${id}`).classList.toggle('dirty',dirty.has(id));
  });
  document.querySelectorAll('[data-gesture]').forEach(button=>button.disabled=!online||state.busy);
  $('cycle-btn').disabled=!online||state.busy;
  $('record-start').hidden=!!state.recording;$('record-save').hidden=!state.recording;
  $('record-start').disabled=!online||state.busy;$('record-save').disabled=state.busy;
  $('record-name').disabled=!!state.recording;$('record-live').hidden=!state.recording;
  if(state.recording){const seconds=Math.floor(state.recording.elapsed_ms/1000);$('record-timer').textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');$('record-count').textContent=state.recording.frames.length+' 个姿态';}
  $('calibration-btn').disabled=state.busy||!!state.recording;
  const job=state.job,holding=online&&state.holding&&!state.simulate&&(!job||job.status==='done');
  $('status-text').textContent=state.busy?job.message:holding?'已应用，正在保持；点击自然张开或停止并张开可释放':state.message;
  $('job-progress').hidden=!state.busy;
  if(state.busy){$('job-progress').setAttribute('aria-valuenow',job.progress);$('job-progress').firstElementChild.style.width=job.progress+'%';}
  if(job&&job.id+job.status!==lastJob){lastJob=job.id+job.status;if(job.status==='error')alertMessage(job.message);}
  renderHand();renderLibrary();
  const activity=JSON.stringify(state.recent.map(r=>[r.id,r.status]));
  if(activity!==activityKey){activityKey=activity;$('activity-list').replaceChildren();
    if(!state.recent.length)$('activity-list').append(element('p',{'class':'muted'},'你的下一次操作，将出现在这里。'));
    [...state.recent].reverse().slice(0,5).forEach(job=>{const row=element('div',{'class':'activity-row'});row.append(element('time',{'datetime':job.finished_at},timeString(job.finished_at)),element('span',{},job.label),element('span',{'class':'result'+(job.status==='error'?' error':'')},job.status==='done'?(state.simulate?'模拟完成':`${job.feedback_frames} 条反馈 · 完成`):job.status==='stopped'?'已停止并张开':'异常'));$('activity-list').append(row);});
  }
}
let refreshQueue=Promise.resolve();
function refresh(){
  // A command must wait for its own fresh read, even while an older poll is pending.
  refreshQueue=refreshQueue.then(async()=>{
    try{const previous=state;state=await api('state');if(!previous)buildJoints();render();}
    catch(error){$('status-text').textContent='本地服务未连接，请检查程序是否运行。';alertMessage(error.message);}
  });
  return refreshQueue;
}
$('connect-btn').addEventListener('click',async()=>{$('connect-btn').disabled=true;$('status-text').textContent='正在连接开发板…';await command('connect',{});});
$('stop-btn').addEventListener('click',()=>command('stop',{}));
$('cycle-btn').addEventListener('click',async()=>{const result=await command('gesture',{id:'grasp-cycle'});if(result)dirty.clear();});
$('record-start').addEventListener('click',()=>command('recordings/start',{name:$('record-name').value}));
$('record-save').addEventListener('click',()=>command('recordings/save',{}));
$('calibration-btn').addEventListener('click',()=>{
  $('calibration-fields').replaceChildren();
  state.joints.forEach(joint=>{
    const row=element('div',{'class':'cal-row'});row.append(element('strong',{},joint.name));
    [['zero_raw','零度位置',0,joint.limit,1],['end_raw','末端位置',0,joint.limit,1],['end_angle','末端角度（°）',1,180,.1]].forEach(([field,text,min,max,step])=>{
      const id=`cal-${joint.id}-${field}`,label=element('label',{'for':id},text),input=element('input',{'id':id,'type':'number','min':min,'max':max,'step':step,'required':'','aria-label':`${joint.name}${text}`});input.value=joint[field];label.append(input);row.append(label);
    });
    const label=element('label',{'class':'measured','for':`cal-${joint.id}-measured`}),input=element('input',{'id':`cal-${joint.id}-measured`,'type':'checkbox','aria-label':`${joint.name}已完成实测标定`});input.checked=joint.calibrated;label.append(input,document.createTextNode('已实测'));row.append(label);$('calibration-fields').append(row);
  });$('calibration-dialog').showModal();
});
$('calibration-close').addEventListener('click',()=>$('calibration-dialog').close());$('calibration-cancel').addEventListener('click',()=>$('calibration-dialog').close());
$('calibration-form').addEventListener('submit',async event=>{
  event.preventDefault();const joints=state.joints.map(joint=>({zero_raw:Number($(`cal-${joint.id}-zero_raw`).value),end_raw:Number($(`cal-${joint.id}-end_raw`).value),end_angle:Number($(`cal-${joint.id}-end_angle`).value),calibrated:$(`cal-${joint.id}-measured`).checked}));
  const result=await command('calibration',{joints});if(result){dirty.clear();buildJoints();render();$('calibration-dialog').close();$('calibration-btn').focus();}
});
refresh();setInterval(refresh,900);
