(() => {
  'use strict';
  const P = window.FlowPolicy;
  const REF = window.ReferenceData;
  const R = window.ReferenceLogic;
  const KEY = 'civra.guided-workflow.prototype.v2';
  const TABS = [['chat','案情沟通'],['research','法律依据'],['workflow','维权路径'],['evidence','证据材料'],['outcomes','结果分析'],['draft','起诉状草稿'],['visuals','案情图谱']];
  const lockIcon = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="4" y="8" width="12" height="9" rx="2"/><path d="M6.5 8V5a3.5 3.5 0 0 1 7 0v3M10 11v3"/></svg>';
  const app = document.querySelector('#app');
  const dialog = document.querySelector('#dialog');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, s => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[s]));
  const clone = value => JSON.parse(JSON.stringify(value));
  const makeFact = (value, status='confirmed') => ({value,status});
  const uid = () => crypto.randomUUID();
  const ui = {view:'chat', screen:'case', menu:false, query:'', ruleEdit:null, activeQuestion:null,
    researchQuery:'',researchSearched:false,referenceFilters:{region:'all',court:'all',year:'all'},referenceLimit:6,referencesOpen:false};
  let toastTimer;
  function newCase(title, domain='traffic') {
    return {id:uid(),title,fields:{dispute:makeFact(domain)},messages:[],files:[],route:null,
      intakeConfirmation:null,routeConfirmation:null,draft:null,draftForm:{},tasks:[],audit:[]};
  }
  function seed() {
    const traffic = newCase('交通事故 · 医疗费用赔偿');
    traffic.fields = {
      dispute:makeFact('traffic'), role:makeFact('claimant'), counterparty:makeFact('王某，另一方机动车驾驶人','unconfirmed'),
      story:makeFact('11月5日在广州发生交通事故，我受伤后就诊，已经产生医疗费用。双方尚未就赔偿达成一致。'),
      when:makeFact('2026年11月5日'),where:makeFact('广东省广州市'),phase:makeFact('not_filed'),
      liability:{value:'',status:'unknown',reason:'暂未取得事故认定书，等待交警反馈'},
      insurance:{value:'',status:'unknown',reason:'暂不清楚对方投保情况，准备向对方了解'}
    };
    traffic.narrative='我在广州出了交通事故，11月5日受伤去医院看病。现在已经有一些医疗票据，但还没跟对方谈好赔偿，想先把事情理清楚。';
    const loan = newCase('借款未归还 · 诉讼准备','loan');
    loan.fields = {dispute:makeFact('loan'),role:makeFact('claimant'),counterparty:makeFact('李某，借款人'),
      story:makeFact('我向李某转账借款，约定还款期限已过，催要后尚未归还。'),when:makeFact('2026年3月'),where:makeFact('广东省深圳市'),phase:makeFact('not_filed'),
      goal:makeFact('要求归还实际尚欠借款本金'),principal:makeFact('30,000元'),has_repaid:makeFact('no')};
    loan.intakeConfirmation={basis:P.basis(loan),at:'演示记录',actor:'demo-user'};
    loan.route='litigation';loan.routeConfirmation={basis:P.basis(loan),at:'演示记录',actor:'demo-user'};
    const mediation = newCase('已开庭 · 调解意向核对');
    mediation.fields=clone(traffic.fields);
    mediation.fields.counterparty.status='confirmed';mediation.fields.phase=makeFact('hearing');
    mediation.fields.goal=makeFact('希望在保留诉讼进度的同时，了解调解渠道');
    mediation.intakeConfirmation={basis:P.basis(mediation),at:'演示记录',actor:'demo-user'};
    mediation.route='mediation';mediation.routeConfirmation={basis:P.basis(mediation),at:'演示记录',actor:'demo-user'};
    return {version:2,activeId:traffic.id,cases:[traffic,loan,mediation],rules:{}};
  }
  function load() {
    try { const data=JSON.parse(localStorage.getItem(KEY)); if(data?.version===2 && Array.isArray(data.cases) && data.cases.length && data.cases.every(c=>c.fields && c.messages && c.files)) return data; } catch {}
    return seed();
  }
  let state=load();
  const current = () => state.cases.find(c=>c.id===state.activeId) || state.cases[0];
  const readiness = () => P.assess(current(),state.rules);
  function toast(text) {clearTimeout(toastTimer);const el=document.querySelector('#toast');el.textContent=text;el.style.display='block';toastTimer=setTimeout(()=>el.style.display='none',3600);}
  function save(){try{localStorage.setItem(KEY,JSON.stringify(state));}catch{toast('浏览器存储已满，本次改动仅保留在当前页面。');}}
  function audit(c,text){c.audit.push({text,time:new Date().toLocaleString('zh-CN')});}
  function changeFact(id,fact) {
    const c=current();c.fields[id]=fact;c.intakeConfirmation=null;c.routeConfirmation=null;
    audit(c,`更新“${P.fields.find(f=>f.id===id)?.label || id}”，后续操作需重新核对案情。`);
    save();
  }
  const displayFact=(f,v)=>v?.status==='unknown' ? `暂不清楚 · ${v.reason}` : f.options?.[v?.value] || v?.value || '待补充';
  function routeName(c){return P.routeOptions(c).find(r=>r.id===c.route)?.title || ({mediation:'调解',negotiation:'协商',litigation:'诉讼事项'}[c.route]) || '尚未选择';}
  function tabLocked(id,r){return (id==='workflow' && !r.canSelectRoute) || (id==='draft' && !r.canDraft) || (id==='outcomes' && !r.intakeConfirmed);}
  function sidebar(){return `<aside class="sidebar"><div class="brand"><span class="brand-symbol">C</span><div><strong>CivRA</strong><small>CIVIL LAW ASSISTANT</small></div></div>
    <button class="button new" data-action="new"><span>＋</span> 新建案件</button>
    <button class="case ${ui.screen==='consult'?'active':''}" data-action="consult"><strong>⌕　只咨询法律</strong><small>查法条、看出处，随时开始</small></button>
    <div class="search-filter"><span>⌕</span><input id="case-search" aria-label="搜索案件" placeholder="搜索案件" value="${esc(ui.query)}"></div>
    <div class="sidebar-group"><div class="sidebar-label">我的案件 <span class="muted">${state.cases.length}</span></div>
    <div id="case-list">${caseList()}</div></div>
    <div class="sidebar-foot"><button class="button small" data-action="rules">⚙　调整收集规则</button><div>本地交互原型 · v2<br>示例数据，尚未连接后端</div></div>
    <div class="profile"><b>U</b><div>我的工作台<br><small class="muted">固定步骤 · 逐项确认</small></div></div></aside>`;}
  function caseList(){return state.cases.filter(c=>c.title.includes(ui.query)).map(c=>`<button class="case ${c.id===current().id && ui.screen==='case'?'active':''}" data-action="case" data-id="${c.id}"><strong>${esc(c.title)}</strong><small><span class="status-dot"></span>${P.assess(c,state.rules).intakeConfirmed?esc(routeName(c)):'案情收集中'}</small></button>`).join('') || '<p class="note">没有匹配的案件</p>';}
  function steps(r){const step=ui.view==='review'?1:!r.intakeConfirmed?0:!r.routeConfirmed?2:ui.view==='results'?4:3;
    const done=[r.blockers.length===0,r.intakeConfirmed,r.routeConfirmed,false,false];
    return `<div class="steps" aria-label="案件办理步骤">${[['chat','收集案情'],['review','确认摘要'],['workflow','选择路径'],['workflow','办理事项'],['results','核对成果']].map(([view,label],i)=>`${i?'<div class="step-line"></div>':''}<button class="step ${i===step?'active':done[i]?'done':''}" data-action="view" data-view="${view}" aria-current="${i===step?'step':'false'}"><i>${done[i]&&i!==step?'✓':i+1}</i>${label}</button>`).join('')}</div>`;}
  function render(){const c=current(),r=readiness();
    app.innerHTML=`<div class="layout ${ui.menu?'menu-open':''}"><button class="backdrop" data-action="menu" aria-label="收起案件列表"></button>${sidebar()}<main class="main">
      <header class="header"><div class="row grow"><button class="mobile-menu" data-action="menu" aria-label="展开案件列表">☰</button><div class="grow"><div class="header-title">${ui.screen==='consult'?'法律咨询':esc(c.title)}</div><div class="header-sub">${ui.screen==='consult'?'无需建案，可直接了解法律规定':`${esc(P.phaseLabels[c.fields.phase?.value] || '程序阶段待确认')}　·　${r.intakeConfirmed?'已确认案情摘要':'正在整理案情'}`}</div></div></div><span class="pill">本地设计预览</span><button class="button small" data-action="${ui.screen==='consult'?'to-case':'overview'}">${ui.screen==='consult'?'进入案件办理':'案情概览 ↗'}</button></header>
      ${ui.screen==='case'?`<nav class="tabs" aria-label="案件功能">${TABS.map(([id,title])=>`<button class="tab ${ui.view===id || (id==='chat'&&ui.view==='review')?'active':''} ${tabLocked(id,r)?'locked':''}" data-action="view" data-view="${id}" ${tabLocked(id,r)?'title="点击查看进入条件"':''}>${title}${tabLocked(id,r)?lockIcon:''}</button>`).join('')}</nav>${steps(r)}`:''}
      <div class="content" id="content">${ui.screen==='consult'?consultView():pageView(c,r)}</div></main></div>`;
  }
  function heading(kicker,title,sub,extra=''){return `<div class="page-title"><div class="row between"><div><div class="eyebrow">${kicker}</div><h1 style="margin-top:8px">${title}</h1></div>${extra}</div><p>${sub}</p></div>`;}
  function pageView(c,r){const view={chat:intake,review:review,workflow:workflow,evidence:evidence,research:research,outcomes:outcomes,draft:draft,visuals:visuals,results:results}[ui.view]||intake;return `<div class="page">${view(c,r)}</div>`;}
  function inputFor(f,value='',id='answer'){if(f.kind==='select')return `<select id="${id}" name="${id}" aria-label="${esc(f.label)}" required><option value="">请选择</option>${Object.entries(f.options).map(([k,v])=>`<option value="${k}" ${k===value?'selected':''}>${v}</option>`).join('')}</select>`;return `<textarea id="${id}" name="${id}" aria-label="${esc(f.label)}" maxlength="5000" placeholder="${esc(f.question)}" required>${esc(value)}</textarea>`;}
  function intake(c,r){const missing=r.blockers.find(f=>!f.fact?.value)||r.blockers[0];const focused=ui.activeQuestion && r.items.find(f=>f.id===ui.activeQuestion&&!f.satisfied);const q=focused||missing;
    return `${heading('CASE INTAKE','先把案情说清楚','你可以对话补充，也可以逐项填写。确认案情后，即可选择维权路径。',`<span class="pill good">步骤 01 / 05</span>`)}
      <div class="intake-grid"><section class="conversation"><div class="conversation-intro">${c.narrative?'以下为示例案情，可修改后体验完整流程':'先说明基本情况，上传材料也可以从这里开始'}</div>
      ${c.narrative?`<div class="message user-message">${esc(c.narrative)}</div>`:''}
      <div class="assistant-label"><i>C</i> CivRA 案件助手</div>
      <div class="message">${r.intakeConfirmed?'这一版案情已经由你确认。你可以查看法律依据，或进入维权路径选择。':r.blockers.length?`案情已保存。进入路径选择前，还有 ${r.blockers.length} 项信息需要补充或确认。我们先处理下面这一项。`:'必答项已处理。请再核对一次案情摘要，然后由你确认进入下一步。'}</div>
      ${q?`<section class="question-card"><div class="question-head"><span>当前待办 · ${esc(q.group)}</span><span>${esc(P.modes[q.mode])}</span></div><form class="question-body" id="question-form" data-field="${q.id}"><h3>${esc(q.label)}</h3><p>${esc(q.question)}</p>${inputFor(q,q.fact?.value||'')}<div class="question-actions"><button class="button primary" type="submit">${q.fact?.value?'确认这项信息':'保存并确认'} →</button>${q.mode==='answered'?`<button class="button" type="button" data-action="unknown" data-field="${q.id}">暂不清楚</button>`:''}</div></form></section>`:
      `<div class="panel"><h3>${r.intakeConfirmed?'案情核对已完成':'可以核对案情摘要了'}</h3><p>已明确记录 ${r.completed} 项必答信息。暂不清楚的内容会继续保留为待补充。</p><button class="button primary" style="margin-top:18px" data-action="view" data-view="${r.intakeConfirmed?'workflow':'review'}">${r.intakeConfirmed?'进入维权路径':'核对案情摘要'} →</button></div>`}
      ${c.messages.length?`<div class="message-history">${c.messages.slice(-3).map(m=>`<div><small class="muted">补充记录</small><p>${esc(m)}</p></div>`).join('')}</div>`:''}
      <form class="composer" id="chat-form"><textarea id="chat-input" aria-label="补充案情" placeholder="还有其他情况？可以继续补充，或直接编辑右侧案情卡。"></textarea><div class="composer-footer"><button type="button" class="link" data-action="upload">＋ 上传材料</button><span>补充内容会保留为待核对记录</span><button class="button primary small" type="submit">发送 ↑</button></div></form>
      <p class="review-note">演示采用固定字段追问。正式接入后由 AI 提取候选信息，再由你确认。</p></section>${ledger(c,r)}</div>`;
  }
  function ledger(c,r){return `<aside class="ledger"><div class="ledger-head"><div class="row between"><h3>案情收集清单</h3><button class="link" data-action="rules">规则</button></div><div class="progress"><span style="width:${r.required.length?r.completed/r.required.length*100:100}%"></span></div><div class="row between ledger-count"><span>${r.completed} / ${r.required.length} 项已处理</span><span>${r.blockers.length} 项待核对</span></div></div><div class="facts">${r.items.map(f=>`<div class="fact"><span class="fact-icon ${f.satisfied?'':'warn'}">${f.satisfied?'✓':'○'}</span><div><div class="fact-label">${esc(f.label)} ${f.mode==='known'?'*':''}</div><div class="fact-value">${esc(displayFact(f,f.fact))}</div>${!f.satisfied?`<small style="color:#ae905c">${esc(f.reason)}</small>`:''}</div><button data-action="edit" data-field="${f.id}" aria-label="编辑${esc(f.label)}">编辑</button></div>`).join('')}</div><div class="ledger-foot"><p>* 必须有明确内容并确认。其他必答项允许说明暂不清楚的原因。</p><button class="button ${r.blockers.length?'':'primary'}" data-action="view" data-view="review">核对案情摘要 →</button></div></aside>`;}
  function review(c,r){return `${heading('REVIEW & CONFIRM','这是我们整理的案情','核对内容、保留未知项，再确认本版摘要。以后修改关键事实时会再次请你核对。')}
    ${r.blockers.length?`<div class="notice warn">还有 ${r.blockers.length} 项未满足进入条件。请补充或确认后，再选择维权路径。</div>`:`<div class="notice">必答项已处理。${r.intakeConfirmed?'本版摘要已经确认。':'请确认以下内容与自己的陈述一致。'}</div>`}
    <table class="fact-table"><thead><tr><th>收集项目</th><th>目前记录</th><th>操作</th></tr></thead><tbody>${r.items.map(f=>`<tr><td>${esc(f.label)}</td><td>${esc(displayFact(f,f.fact))}<br><small class="${f.satisfied?'muted':''}">${f.satisfied?(f.fact?.status==='unknown'?'已记录未知，后续补充':'已确认或非必答项'):esc(f.reason)}</small></td><td><button class="link" data-action="edit" data-field="${f.id}">核对 / 修改</button></td></tr>`).join('')}</tbody></table>
    <div class="footer-action"><button class="button" data-action="view" data-view="chat">继续沟通</button><button class="button primary" data-action="confirm-intake" ${r.blockers.length?'disabled':''}>${r.intakeConfirmed?'进入维权路径':'确认本版案情，进入路径选择'} →</button></div><p class="note" style="text-align:right;margin-top:12px">确认表示记录准确，不代表已经认定事实或满足法院全部要求。</p>`;}
  function locked(title,reasons,target='chat'){return `<div class="empty-state"><div class="empty-symbol">${lockIcon}</div><h2>${title}</h2><p>完成下列事项后，就可以继续这一步。法律查询和材料上传仍可使用。</p><ul class="missing-list">${reasons.map(s=>`<li>${esc(s)}</li>`).join('')}</ul><button class="button primary" data-action="view" data-view="${target}">${target==='workflow'?'前往选择维权路径':'前往补充并确认案情'} →</button></div>`;}
  function workflow(c,r){if(!r.canSelectRoute)return `${heading('CHOOSE A PATH','维权路径','先确认案情，再由你选择当前想处理的事项。')}${locked('先完成案情核对',[...r.blockers.map(f=>`${f.label} · ${f.reason}`),'确认本版案情摘要'])}`;
    const options=P.routeOptions(c);
    return `${heading('CHOOSE A PATH','下一步，你希望怎样处理？','路径由你选择。你可以返回调整，已经保存的事实和材料会继续保留。')}
    <div class="notice"><strong>实际程序阶段　${esc(P.phaseLabels[c.fields.phase.value])}</strong>　${c.route?`当前处理意向　${esc(routeName(c))}`:'尚未选择处理意向'}<br>${['filed','hearing','appeal'].includes(c.fields.phase.value)?'此阶段仍可了解调解。选择调解后，系统继续保留原来的诉讼阶段。':'选择路径只记录办理意向，不代表已经向法院或调解组织提交申请。'}</div>
    ${options.length?`<div class="card-grid">${options.map((o,i)=>`<article class="route-card ${r.routeConfirmed&&c.route===o.id?'selected':''}"><div class="row between"><span class="route-number">0${i+1}</span>${r.routeConfirmed&&c.route===o.id?'<span class="pill good">已选择</span>':''}</div><h3>${o.title}</h3><p>${o.description}</p><button class="button ${c.route===o.id&&r.routeConfirmed?'':'primary'}" data-action="choose-route" data-route="${o.id}">${c.route===o.id&&r.routeConfirmed?'重新确认此路径':'选择此路径'} →</button></article>`).join('')}</div>`:'<div class="panel"><h3>案件已处理完毕</h3><p>可以查看历史成果和记录。有新的程序进展时，请先更新实际阶段。</p></div>'}
    ${r.routeConfirmed?`<div class="panel"><div class="row between"><h3>${esc(routeName(c))} · 当前待办</h3><span class="pill good">由你确认</span></div>${routeTasks(c).map((t,i)=>`<div class="task-row"><span>0${i+1}</span><span>${esc(t.text)}</span><button class="button small" data-action="${t.view?'view':'task'}" ${t.view?`data-view="${t.view}"`:`data-index="${i}"`}>${t.view?'前往处理':c.tasks.includes(`${c.route}:${i}`)?'已记录反馈':'记录实际反馈'}</button></div>`).join('')}</div>`:''}
    <details class="details"><summary>为什么开庭后仍有调解选项？</summary><p>民事诉讼法第145条规定了判决前的调解。二审和执行阶段需分别对应适用程序，执行中显示“执行和解”。</p><a href="https://enipc.court.gov.cn/zh-cn/news/view-6231.html" target="_blank" rel="noopener">查看最高人民法院发布的民事诉讼法 ↗</a></details>`;
  }
  function routeTasks(c){if(c.route==='litigation'&&c.fields.phase?.value==='not_filed')return [{text:'核对请求、金额与材料',view:'evidence'},{text:'查阅法律依据和适用条件',view:'research'},{text:'选择模板，核对起诉状草稿',view:'draft'},{text:'记录实际办理结果'}];return [{text:'整理本次沟通的请求与材料',view:'evidence'},{text:'核实适用渠道和办理条件',view:'research'},{text:'记录实际沟通或办理反馈'}];}
  function evidence(c){return `${heading('MATERIALS','先把材料集中到这里','随时上传并补充。材料缺失会形成待办，上传本身不会改变案件程序阶段。')}
    <div class="dropzone"><h3>合同、票据、聊天记录、法院文书</h3><p>支持选择 PDF、照片、Word 和文本文件</p><button class="button primary" data-action="upload">＋ 选择材料</button><p class="note">原型只保存文件名称和大小，未运行 OCR 或重复票据识别。</p></div>
    <div class="panel"><div class="row between"><h3>本案材料</h3><span class="pill">${c.files.length} 份</span></div>${c.files.length?c.files.map(f=>`<div class="file-row"><span class="file-icon">文件</span><div class="grow"><strong>${esc(f.name)}</strong><small>${(f.size/1024).toFixed(1)} KB · 仅本地登记 · 待接入解析</small></div><button class="link" data-action="remove-file" data-id="${f.id}">移除记录</button></div>`).join(''):'<p>尚未登记材料。没有材料时，也可以先说明案情。</p>'}</div>
    <div class="notice warn" style="margin-top:20px">${c.fields.dispute?.value==='traffic'?'如主张医疗费，后续核对医疗票据与诊疗记录。缺少时可补充或解释。':'后续按诉求核对材料及金额来源，缺少的内容单独列为补充事项。'}这是材料整理提示。</div>`;}
  function research(){return `${heading('LEGAL BASIS','法律依据，随时查阅','可先了解法律，也可以围绕当前案件查询。阅读法律依据不受办事步骤限制。')}${researchContent()}`;}
  function researchContent(){return `<div class="consult-box"><form id="research-form"><input id="research-input" aria-label="法律咨询问题" placeholder="例如：开庭以后还能调解吗？" value="${esc(ui.researchQuery)}" required><button class="button primary">查询法律依据 →</button></form></div>
    <button class="button small" data-action="example-law">试问：开庭以后还能调解吗？</button>
    ${ui.researchSearched?(/调解|和解|开庭|执行/.test(ui.researchQuery)?`<article class="legal-card"><div class="row between"><h3>民事诉讼法 · 调解与执行和解</h3><span class="pill good">官方来源样例</span></div><p>判决前仍可进行调解。二审调解与执行和解分别适用相应程序，所以“实际程序阶段”和“希望如何处理”应分别记录。</p><small>相关条款　第145条、第179条、第241条<br>文件版本　2023年修正<br>来源　最高人民法院知识产权法庭</small><p><a target="_blank" rel="noopener" href="https://enipc.court.gov.cn/zh-cn/news/view-6231.html">前往法律原文，核对适用条款 ↗</a></p></article>`:'<div class="notice">本版仅内置“调解时点”的来源样例。正式接入后，此处调用现有 RAG 并展示原文、来源版本和定位信息。本次没有生成模拟检索结论。</div>'):'<div class="panel"><h3>从问题到法律原文</h3><p>输入问题 → 查看相关条款和解释 → 打开原文核对出处。需要处理具体案件时，再进入案情收集。</p></div>'}`;}
  function consultView(){return `<div class="page mode-page">${heading('LEGAL CONSULTATION','先了解法律，再决定下一步','不需要先填写完整案情。需要办理具体案件时，可以随时切换。')}${researchContent()}<div class="panel"><div class="row between wrap"><div><h3>准备处理自己的纠纷？</h3><p>进入案件办理，逐步核对案情与路径。</p></div><button class="button" data-action="to-case">进入案件办理 →</button></div></div></div>`;}
  function outcomesBase(c,r){if(!r.intakeConfirmed)return `${heading('SCENARIOS','结果分析','核对案情后，再围绕具体请求分析不同情形。')}${locked('先确认分析所依据的案情',['补充并确认案情摘要'])}`;return `${heading('SCENARIOS','围绕事实，分析不同情形','正式接入后由事实、材料与检索依据支撑分析。此处展示结果页结构。')}
    <div class="notice warn">这是界面结构样例，尚未对本案进行法律分析或预测结果。</div>
    <div class="card-grid">${[['需要证明什么','展示各项请求成立所需核实的事实，以及目前的来源。'],['哪些材料仍需补充','展示证据缺口、冲突和对方可能提出的争议。'],['哪些因素会改变分析','说明结论依赖的前提，更新事实后重新分析。']].map(([t,d])=>`<article class="route-card"><h3>${t}</h3><p>${d}</p><span class="pill">待后端分析结果</span></article>`).join('')}</div>`;}

  function draft(c,r){if(!r.canDraft)return `${heading('COMPLAINT DRAFT','起诉状草稿','在确认案情并明确选择诉讼准备后，进入模板与文书核对。')}${locked('暂未进入起诉状准备',r.draftReasons,r.intakeConfirmed&&(!r.routeConfirmed||c.route!=='litigation')?'workflow':'chat')}${c.draft?`<details class="details"><summary>查看此前保存的草稿（历史版本，仅供回看）</summary><article class="paper">${esc(c.draft.text)}</article><p>原始内容仍保留。确认当前案情与适用路径后，重新生成再导出。</p></details>`:''}`;
    const stale=c.draft && c.draft.basis!==r.signature;
    const domain=P.domains[c.fields.dispute?.value]||'其他纠纷';
    return `${heading('COMPLAINT DRAFT','先确认表述，再生成草稿','选择适用模板 → 补充文书信息 → 确认正式表述 → 预览导出。')}
    <div class="notice">已满足进入条件　案情已确认 · 用户选择诉讼 · 当前尚未起诉</div>
    <form id="draft-form"><div class="template"><strong>模板选择</strong><select id="template" aria-label="起诉状模板"><option value="${esc(c.fields.dispute?.value)}">${esc(domain)} · 起诉状结构预览</option></select><p class="note" style="margin-top:10px">正式版接入仓库中的法院原表和模板版本，本原型仅预览填写流程。</p></div>
    <div class="card-grid" style="grid-template-columns:1fr 1fr"><label class="stack" style="font-size:12px;gap:8px">原告姓名或名称<input type="text" id="plaintiff" maxlength="200" value="${esc(c.draftForm.plaintiff||'')}" placeholder="可暂留空，草稿显示待补充"></label><label class="stack" style="font-size:12px;gap:8px">被告姓名或名称<input type="text" id="defendant" maxlength="200" value="${esc(c.draftForm.defendant||'')}" placeholder="正式身份信息后续核对"></label></div>
    <div class="panel"><h3>正式表述预览</h3><p>下面只整理已确认的请求，不扩写尚未提供的责任结论或金额。</p><textarea id="wording" aria-label="正式表述" style="margin-top:15px" required maxlength="5000">${esc(c.draftForm.wording||`请求依法处理以下事项：${c.fields.goal?.value?.replace(/[。.!！]+$/,'')||'［待补充］'}。具体请求项目、金额及依据仍需逐项核对。`)}</textarea><label class="checkline"><input type="checkbox" id="approve-wording" required><span>我已核对以上表述，允许将它写入这份演示草稿。待补充信息继续保留为占位符。</span></label><button class="button primary" type="submit">确认表述并生成草稿 →</button></div></form>
    ${c.draft?`${stale?'<div class="notice warn" style="margin-top:20px">这份草稿依据的案情已变化，请重新生成后再导出。</div>':''}<article class="paper"><h2>民事起诉状</h2>${esc(c.draft.text)}</article><div class="footer-action"><span class="note">演示导出 Markdown，正式系统继续支持 Word / PDF</span><button class="button primary" data-action="export" ${stale?'disabled':''}>下载演示草稿 ↓</button></div>`:''}`;
  }
  function outcomes(c,r){return outcomesBase(c,r)+(r.intakeConfirmed?referenceSection(c,false):'');}
  function visualsBase(c){return `${heading('CASE MAP','一条时间线，串起案件进展','事实、所选路径与实际程序进度分别保留。这里展示当前本地记录。')}
    <div class="notice"><strong>实际程序阶段</strong>　${esc(P.phaseLabels[c.fields.phase?.value]||'待确认')}<br><strong>当前处理意向</strong>　${esc(routeName(c))}　${!readiness().routeConfirmed&&c.route?'（需重新确认）':''}</div>
    <div class="timeline"><div><strong>${esc(c.fields.when?.value||'发生时间待补充')}</strong><p>${esc(c.fields.story?.value||'尚未记录主要经过')}</p></div>${c.audit.map(a=>`<div><strong>${esc(a.time)}</strong><p>${esc(a.text)}</p></div>`).join('')}<div><strong>目前记录</strong><p>${c.files.length} 份材料 · ${c.draft?'已生成演示草稿':'尚未生成草稿'}</p></div></div>`;}
  function visuals(c){return visualsBase(c)+referenceSection(c,true);}
  function referenceSection(c,statistics){const domain=c.fields.dispute?.value;const all=REF.samples.filter(s=>s.domain===domain);const samples=statistics?R.filterCases(all,ui.referenceFilters):all.slice(0,3);
    const body=`<p class="reference-note">以下为模拟案例，用于演示参考方式，真实案例检索待接入。案例差异需结合本案核对，样本比例不表示胜诉概率。</p>
      ${statistics?`<div class="reference-filters">${referenceFilter('region','地区',[['all','全部地区'],['广州示例','广州示例'],['深圳示例','深圳示例']])}${referenceFilter('court','法院',[['all','全部法院'],['示例法院甲','示例法院甲'],['示例法院乙','示例法院乙']])}${referenceFilter('year','年份',[['all','全部年份'],['2024','2024'],['2025','2025'],['2026','2026']])}</div><div class="row between wrap"><p class="reference-note">${esc(P.domains[domain]||'当前类型')} · 当前筛选 ${samples.length} 条</p><button class="link" data-action="reset-reference-filters">重置筛选</button></div>${samples.length?`<div class="reference-chart"><h3>模拟样本结果分布</h3>${R.summarize(samples,'outcome').map(row=>`<div class="reference-bar"><div class="row between"><span>${esc(row.label)}</span><span>${row.count} 条 · ${row.percent.toFixed(1)}%</span></div><div class="reference-track"><span style="width:${row.percent}%"></span></div></div>`).join('')}</div>`:''}`:''}
      ${samples.length?`<div class="reference-cards">${samples.slice(0,statistics?ui.referenceLimit:3).map(s=>`<article class="reference-card"><div class="row between wrap"><span class="pill warn">模拟案例</span><small>${esc(s.id)}</small></div><h3>${esc(s.issue)}</h3><p>${esc(s.facts)}</p><div class="reference-meta">${esc(s.region)} · ${esc(s.court)} · ${s.year} 年</div><button class="link" data-action="reference-detail" data-id="${s.id}">查看理由与本案差异 ↗</button></article>`).join('')}</div>${statistics&&samples.length>ui.referenceLimit?'<div class="footer-action"><button class="button small" data-action="more-references">查看更多案例</button></div>':''}`:`<div class="reference-empty">${all.length?'当前筛选没有匹配样本，可以调整或重置筛选。':'当前纠纷类型尚未配置参考案例样例。'}</div>`}`;
    return statistics?`<details class="panel case-reference-section" id="visual-case-references" ${ui.referencesOpen?'open':''}><summary>参考案例与样本统计 <span class="note">展开查看</span></summary><div class="reference-body">${body}</div></details>`:`<section class="panel case-reference-section"><h3>参考案例</h3>${body}</section>`;
  }
  function referenceFilter(key,label,options){return `<label>${label}<select data-reference-filter="${key}" aria-label="参考案例${label}">${options.map(([id,name])=>`<option value="${id}" ${ui.referenceFilters[key]===id?'selected':''}>${name}</option>`).join('')}</select></label>`;}
  function referenceDetail(id){if(ui.screen!=='case'||!['visuals','outcomes'].includes(ui.view))return;const sample=REF.samples.find(s=>s.id===id&&s.domain===current().fields.dispute?.value);if(!sample)return;
    openDialog(`参考案例 · ${esc(sample.issue)}`,`<div class="notice warn">模拟案例，无真实案号或原文链接，不能作为裁判引用。</div><p>${esc(sample.region)} · ${esc(sample.court)} · ${sample.year} 年</p><h3>样例事实</h3><p>${esc(sample.facts)}</p><h3>理由展示</h3><p>${esc(sample.reason)}</p><h3>本案当前记录</h3><p>${esc(current().fields.story?.value||'待补充')}</p><h3>与本案需要核对的差异</h3><p>${esc(sample.check)}</p>`);
  }
  function results(c,r){return `${heading('REVIEW OUTPUTS','核对本案成果','整理已保存的内容和实际反馈，再决定下一步。成果生成不会自动标记案件办结。')}
    <div class="panel"><h3>可查看的记录</h3>${[
      ['案情摘要',r.intakeConfirmed?'本版已确认':'仍需核对','review'],
      ['材料记录',`${c.files.length} 份本地登记`,'evidence'],
      ['处理意向',r.routeConfirmed?routeName(c):'尚未确认当前路径','workflow'],
      ['办理反馈',`${c.audit.filter(a=>a.text.startsWith('用户反馈')).length} 条实际反馈`,'visuals'],
      ...(c.draft?[['起诉状演示草稿',c.draft.basis===r.signature?'可返回核对':'案情已变化，需要更新','draft']]:[])
    ].map(([label,detail,view])=>`<div class="task-row"><span>${label}</span><span>${esc(detail)}</span><button class="button small" data-action="view" data-view="${view}">查看</button></div>`).join('')}</div><div class="notice" style="margin-top:20px">协商、调解和诉讼都可以在这里回看各自的记录。是否达成协议、提交材料、立案或开庭，以实际发生并由你记录的信息为准。</div>`;}
  function openDialog(title,body,actions=''){dialog.innerHTML=`<div class="dialog-head"><h2>${title}</h2><button data-action="close" aria-label="关闭对话框">✕</button></div><div class="dialog-body">${body}</div>${actions?`<div class="dialog-actions">${actions}</div>`:''}`;if(!dialog.open)dialog.showModal();}
  function editField(id,unknown=false){const f=P.applicable(current(),state.rules).find(f=>f.id===id);if(!f)return;
    openDialog(unknown?`记录“${esc(f.label)}”暂不清楚`:`核对${esc(f.label)}`,`<form id="field-form" data-field="${id}" data-unknown="${unknown}"><p>${esc(f.question)}</p>${unknown?'<label for="unknown-reason">请说明原因或准备怎样核实</label><textarea id="unknown-reason" required maxlength="2000" placeholder="例如：目前没有取得材料，准备向相关人员了解"></textarea>':inputFor(f,current().fields[id]?.value||'','edit-value')}<p class="note" style="margin-top:14px">修改后会重新检查案情确认状态，原有路径选择保留在记录中。</p></form>`,`${!unknown&&f.mode==='answered'?`<button class="button" data-action="unknown" data-field="${id}">暂不清楚</button>`:''}<button class="button" data-action="close">取消</button><button class="button primary" type="submit" form="field-form">${unknown?'记录未知及原因':'保存并确认'}</button>`);}
  function rulesDialog(){const fixed=['dispute','role','phase'];
    openDialog('设置案情收集规则',`<p>设计演示中可以调整每项信息的进入条件。正式版由服务器配置并统一校验，普通案件用户不能修改规则。</p><form id="rules-form">${P.fields.map(f=>`<div class="rules-row"><span>${f.label}<small class="muted">　${f.domain?P.domains[f.domain]:'通用'}</small></span>${fixed.includes(f.id)?'<span class="pill">必填且确认 · 流程基础字段</span>':`<select name="rule-${f.id}" aria-label="${f.label}的收集规则">${Object.entries(P.modes).map(([k,v])=>`<option value="${k}" ${(state.rules[f.id]||f.mode)===k?'selected':''}>${v}</option>`).join('')}</select>`}</div>`).join('')}<p class="note" style="margin-top:16px">“必填且确认”需要明确值。“必须回应”允许记录未知及原因。规则改变后，需要重新确认案情摘要。</p></form>`,`<button class="button" data-action="close">取消</button><button class="button primary" form="rules-form" type="submit">应用本地演示规则</button>`);
  }
  function go(view){ui.screen='case';ui.view=view;ui.activeQuestion=null;render();document.querySelector('#content').scrollTop=0;}
  function chooseRoute(route){const c=current(),r=readiness(),o=P.routeOptions(c).find(x=>x.id===route);if(!r.canSelectRoute||!o){toast('请先补充并确认当前案情。');return;}
    openDialog(`确认${o.title}`,`<p>${o.description}</p><div class="notice">实际程序阶段继续保留为“${esc(P.phaseLabels[c.fields.phase.value])}”。本次操作只保存你的处理意向。</div><p>事实或程序阶段发生变化时，系统会请你重新确认。你仍可以随时查看法律、补充材料和切换处理意向。</p>`,`<button class="button" data-action="close">返回比较</button><button class="button primary" data-action="confirm-route" data-route="${route}">确认选择</button>`);
  }
  function download(name,text){const url=URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  async function action(button){const c=current(),r=readiness(),a=button.dataset.action;
    if(a==='view')return go(button.dataset.view);
    if(a==='close')return dialog.close();
    if(a==='menu'){ui.menu=!ui.menu;return render();}
    if(a==='case'){state.activeId=button.dataset.id;ui.screen='case';ui.view='chat';ui.menu=false;ui.activeQuestion=null;save();return render();}
    if(a==='consult'){ui.screen='consult';ui.menu=false;return render();}
    if(a==='to-case'){ui.screen='case';ui.view='chat';return render();}
    if(a==='overview')return go('review');
    if(a==='rules')return rulesDialog();
    if(a==='edit')return editField(button.dataset.field);
    if(a==='unknown')return editField(button.dataset.field,true);
    if(a==='upload')return document.querySelector('#upload').click();
    if(a==='remove-file'){c.files=c.files.filter(f=>f.id!==button.dataset.id);save();return render();}
    if(a==='new'){openDialog('新建案件',`<form id="new-form"><label for="new-title">案件名称</label><input type="text" id="new-title" placeholder="例如：房屋买卖合同纠纷" maxlength="120" required><label for="new-domain" style="margin-top:18px">初步纠纷类型</label><select id="new-domain">${Object.entries(P.domains).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></form>`,`<button class="button" data-action="close">取消</button><button class="button primary" type="submit" form="new-form">开始收集案情</button>`);return;}
    if(a==='confirm-intake'){if(r.blockers.length)return toast('仍有必答信息未完成。');c.intakeConfirmation={basis:r.signature,at:new Date().toISOString(),actor:'local-user'};audit(c,'用户确认本版案情摘要。');save();go('workflow');return;}
    if(a==='choose-route')return chooseRoute(button.dataset.route);
    if(a==='confirm-route'){const route=button.dataset.route;if(!r.canSelectRoute||!P.routeOptions(c).some(o=>o.id===route)){dialog.close();return toast('案情已变化，请重新确认。');}c.route=route;c.routeConfirmation={basis:r.signature,at:new Date().toISOString(),actor:'local-user'};audit(c,`选择“${routeName(c)}”，实际程序阶段保持“${P.phaseLabels[c.fields.phase.value]}”。`);save();dialog.close();go('workflow');toast('已保存你的选择。');return;}
    if(a==='reference-detail')return referenceDetail(button.dataset.id);
    if(a==='more-references'){ui.referenceLimit+=6;ui.referencesOpen=true;return render();}
    if(a==='reset-reference-filters'){ui.referenceFilters={region:'all',court:'all',year:'all'};ui.referenceLimit=6;ui.referencesOpen=true;return render();}
    if(a==='example-law'){ui.researchQuery='开庭以后还能调解吗？';ui.researchSearched=true;return render();}
    if(a==='task'){if(!r.routeConfirmed)return toast('请重新确认当前处理路径。');openDialog('记录实际办理反馈',`<form id="task-form" data-index="${button.dataset.index}"><label for="task-feedback">实际发生了什么？</label><textarea id="task-feedback" placeholder="例如：已联系对方，对方表示愿意了解调解" maxlength="2000" required></textarea><p class="note">该记录不会自动变更实际程序阶段。实际立案或开庭后，请更新对应案情字段。</p></form>`,`<button class="button primary" type="submit" form="task-form">保存反馈</button>`);return;}
    if(a==='export'){if(!r.canDraft||c.draft?.basis!==r.signature)return toast('请重新核对案情并生成草稿。');return download('CivRA_起诉状_演示草稿.md',`# 民事起诉状（本地演示草稿）\n\n${c.draft.text}\n\n本文件为界面演示输出，尚未填写法院原始模板。`);}
  }
  document.addEventListener('click',event=>{const b=event.target.closest('[data-action]');if(b)action(b);});
  document.addEventListener('input',event=>{if(event.target.id==='case-search'){ui.query=event.target.value;document.querySelector('#case-list').innerHTML=caseList();}});
  document.addEventListener('change',event=>{if(event.target.dataset.referenceFilter){ui.referenceFilters[event.target.dataset.referenceFilter]=event.target.value;ui.referenceLimit=6;ui.referencesOpen=true;render();}});
  document.addEventListener('submit',event=>{
    const form=event.target;event.preventDefault();const c=current(),r=readiness();
    if(form.id==='question-form'){const value=document.querySelector('#answer').value.trim();if(!value)return;const f=P.fields.find(f=>f.id===form.dataset.field);if(f.mode==='known'&&!P.known(makeFact(value)))return toast('请填写明确内容，未知项不能计作已确认。');changeFact(form.dataset.field,makeFact(value));ui.activeQuestion=null;render();toast('已确认，继续处理下一项。');}
    if(form.id==='field-form'){const unknown=form.dataset.unknown==='true';const value=document.querySelector(unknown?'#unknown-reason':'#edit-value').value.trim();if(!value)return;changeFact(form.dataset.field,unknown?{value:'',status:'unknown',reason:value}:makeFact(value));dialog.close();render();}
    if(form.id==='chat-form'){const input=document.querySelector('#chat-input');if(!input.value.trim())return;c.messages.push(input.value.trim());audit(c,'保存一条补充陈述，尚未自动更新事实。');save();render();toast('已保留补充陈述，请通过案情卡确认结构化内容。');}
    if(form.id==='new-form'){const created=newCase(document.querySelector('#new-title').value.trim(),document.querySelector('#new-domain').value);if(!created.title)return;state.cases.unshift(created);state.activeId=created.id;ui.screen='case';ui.view='chat';ui.menu=false;ui.activeQuestion=null;save();dialog.close();render();}
    if(form.id==='rules-form'){const rules={};for(const [name,value]of new FormData(form)){const id=name.slice(5),f=P.fields.find(x=>x.id===id);if(f&&value!==f.mode)rules[id]=value;}state.rules=rules;state.cases.forEach(c=>{c.intakeConfirmation=null;c.routeConfirmation=null;});save();dialog.close();render();toast('已更新本地规则，请重新核对案情。');}
    if(form.id==='research-form'){ui.researchQuery=document.querySelector('#research-input').value.trim();ui.researchSearched=true;render();}
    if(form.id==='task-form'){if(!r.routeConfirmed)return toast('请重新确认路线。');const feedback=document.querySelector('#task-feedback').value.trim();if(!feedback)return;const key=`${c.route}:${form.dataset.index}`;if(!c.tasks.includes(key))c.tasks.push(key);audit(c,`用户反馈：${feedback}`);save();dialog.close();render();}
    if(form.id==='draft-form'){if(!r.canDraft)return toast('当前不满足文书准备条件。');if(!document.querySelector('#approve-wording').checked)return;const wording=document.querySelector('#wording').value.trim();if(!wording)return;c.draftForm={plaintiff:document.querySelector('#plaintiff').value.trim(),defendant:document.querySelector('#defendant').value.trim(),wording};const f=c.draftForm;c.draft={basis:r.signature,text:`原告：${f.plaintiff||'［姓名或名称待补充］'}\n被告：${f.defendant||'［姓名或名称待补充］'}\n身份及送达信息：［待补充并核对］\n\n诉讼请求\n${f.wording}\n\n事实与理由\n${c.fields.story?.value||'［待补充］'}\n\n法律依据与证据对应关系：［待检索、关联并核对］\n\n此致\n［法院名称待核实］\n\n具状人：［待签署］\n日期：［待填写］`};audit(c,'用户确认正式表述，生成一份演示草稿。');save();render();toast('已生成演示草稿，未知内容保留为待补充。');}
  });
  document.querySelector('#upload').addEventListener('change',event=>{const c=current();for(const file of event.target.files)c.files.push({id:uid(),name:file.name,size:file.size});save();event.target.value='';go('evidence');toast('已登记本地材料信息，尚未解析文件内容。');});
  dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}});
  render();
})();
