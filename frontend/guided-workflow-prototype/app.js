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
    researchQuery:'', researchSearched:false,researchTab:'laws',lawScope:'all',visualTab:'timeline',
    caseFilters:{domain:'current',region:'all',court:'all',year:'all',outcome:'all',query:''},compareIds:[],sampleLimit:6};
  let toastTimer;
  function newCase(title, domain='traffic') {
    return {id:uid(),title,fields:{dispute:makeFact(domain)},messages:[],files:[],route:null,
      intakeConfirmation:null,routeConfirmation:null,draft:null,draftForm:{},tasks:[],audit:[],referenceIds:[],sampleIds:[],claimFactIds:[],materialLinks:[]};
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
  for(const c of state.cases){c.referenceIds ||= [];c.sampleIds ||= [];c.claimFactIds ||= [];c.materialLinks ||= [];}
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
    <div class="sidebar-foot"><button class="button small" data-action="rules">⚙　调整收集规则</button><div>本地交互原型 · v2.1<br>示例数据，尚未连接后端</div></div>
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
    <div class="panel"><div class="row between"><h3>本案材料</h3><span class="pill">${c.files.length} 份</span></div>${c.files.length?c.files.map(f=>`<div class="file-row"><span class="file-icon">文件</span><div class="grow"><strong>${esc(f.name)}</strong><small>${(f.size/1024).toFixed(1)} KB · 仅本地登记 · 待接入解析</small></div><button class="link" data-action="file-detail" data-id="${f.id}">登记与关联</button><button class="link" data-action="remove-file" data-id="${f.id}">移除记录</button></div>`).join(''):'<p>尚未登记材料。没有材料时，也可以先说明案情。</p>'}</div>
    <div class="notice warn" style="margin-top:20px">${c.fields.dispute?.value==='traffic'?'如主张医疗费，后续核对医疗票据与诊疗记录。缺少时可补充或解释。':'后续按诉求核对材料及金额来源，缺少的内容单独列为补充事项。'}这是材料整理提示。</div>`;}
  function pills(items,active,action){return `<div class="subtabs" role="group">${items.map(([id,label])=>`<button class="subtab ${id===active?'active':''}" data-action="${action}" data-id="${id}" aria-pressed="${id===active}">${label}</button>`).join('')}</div>`;}
  function research(){return `${heading('LEGAL BASIS','法条与类案，一起核对依据','阅读原文、比较争点，再决定哪些资料需要进一步核实。')}${researchContent()}`;}
  function researchContent(){const c=current();return `${pills([['laws','法律条文'],['cases','相似案例'],...(ui.screen==='case'?[['saved',`本案收藏 ${(c.referenceIds||[]).length+(c.sampleIds||[]).length}`]]:[])],ui.researchTab==='stats'?'cases':ui.researchTab,'research-tab')}
    ${['cases','stats'].includes(ui.researchTab)?caseLibrary(ui.researchTab==='stats'):ui.researchTab==='saved'?savedReferences(c):lawLibrary(c)}`;}
  function lawLibrary(c){const domain=ui.lawScope==='case'&&ui.screen==='case'?c.fields.dispute?.value:'all';const laws=R.searchLaws(REF.laws,ui.researchQuery,domain);
    return `<form class="reference-search" id="research-form"><input id="research-input" aria-label="法律咨询问题" placeholder="搜索条号或主题，如：第680条、医疗费、开庭后调解" value="${esc(ui.researchQuery)}"><button class="button primary">查询内置法条 →</button></form>
    <div class="row between wrap"><div class="row wrap">${ui.screen==='case'?`<button class="button small ${ui.lawScope==='case'?'primary':''}" data-action="law-scope" data-id="case">本案类型</button>`:''}<button class="button small ${ui.lawScope==='all'?'primary':''}" data-action="law-scope" data-id="all">全部内置法条</button><button class="link" data-action="example-law">试问：开庭以后还能调解吗？</button></div><span class="note">${laws.length} 条匹配 / ${REF.laws.length} 条内置样例</span></div>
    <p class="note" style="margin-top:14px">此处使用本地条文和关键词匹配，正式 RAG 待接入。点击条文可阅读完整内容、来源版本与定位。</p>
    ${laws.length?laws.map(law=>lawCard(law,c)).join(''):'<div class="flat-empty"><h3>内置样例中没有匹配条文</h3><p>这不表示法律中没有相关规定。可以调整关键词、条号或检索范围。</p><button class="button" data-action="clear-law">查看全部样例</button></div>'}`;
  }
  function lawCard(law,c){const pinned=(c.referenceIds||[]).includes(law.id);return `<article class="legal-card"><div class="row between wrap"><h3>${esc(law.title)} · ${law.article}</h3><span class="pill">${esc(law.version)}</span></div><p>${esc(law.text)}</p><div class="row between wrap"><small>生效日期 ${law.effective}${law.page?` · 随库 PDF 第 ${law.page} 页`:''}</small><div class="row"><button class="link" data-action="law-detail" data-id="${law.id}">原文与来源 ↗</button>${ui.screen==='case'?`<button class="button small ${pinned?'primary':''}" data-action="pin-law" data-id="${law.id}">${pinned?'✓ 已加入本案':'＋ 加入本案参考'}</button>`:''}</div></div></article>`;}
  function savedReferences(c){const laws=REF.laws.filter(x=>(c.referenceIds||[]).includes(x.id));const cases=REF.samples.filter(x=>(c.sampleIds||[]).includes(x.id));
    if(!laws.length&&!cases.length)return '<div class="flat-empty"><h3>本案还没有收藏参考资料</h3><p>法条和模拟案例可分别收藏，收藏不会自动认定其适用于本案。</p><button class="button" data-action="research-tab" data-id="laws">浏览法律条文</button></div>';
    return `<div class="notice">收藏用于后续核对。模拟案例有单独标识，不能作为真实裁判引用或填入正式文书。</div>${laws.map(l=>lawCard(l,c)).join('')}${cases.length?`<h3 style="margin:25px 0 15px">模拟案例参考 · ${cases.length} 条</h3><div class="sample-grid">${cases.map(s=>sampleCard(s)).join('')}</div>`:''}`;
  }
  function effectiveFilters(){const f={...ui.caseFilters};if(f.domain==='current')f.domain=ui.screen==='case'?(current().fields.dispute?.value||'all'):'all';return f;}
  function selectFilter(key,label,options){return `<label>${label}<select data-filter="${key}" aria-label="${label}">${options.map(([value,text])=>`<option value="${value}" ${String(ui.caseFilters[key])===String(value)?'selected':''}>${text}</option>`).join('')}</select></label>`;}
  function caseFilters(){return `<div class="sample-filters">${selectFilter('domain','纠纷类型',[['current',ui.screen==='case'?'当前案件类型':'全部类型'],['all','全部类型'],...Object.entries(REF.domainLabels)])}${selectFilter('region','样本地区',[['all','全部地区'],['广州示例','广州示例'],['深圳示例','深圳示例']])}${selectFilter('court','样本法院',[['all','全部法院'],['示例法院甲','示例法院甲'],['示例法院乙','示例法院乙']])}${selectFilter('year','裁判年份',[['all','全部年份'],['2024','2024'],['2025','2025'],['2026','2026']])}${selectFilter('outcome','结果分类',[['all','全部结果'],['请求获支持','请求获支持'],['部分支持','部分支持'],['信息不全','信息不全']])}</div>
    <form id="case-filter-form" class="reference-search compact"><input id="sample-query" aria-label="搜索模拟案例争点" placeholder="按争点搜索，例如医疗、维修、本金、质量" value="${esc(ui.caseFilters.query)}"><button class="button">筛选样本</button><button type="button" class="link" data-action="reset-filters">重置</button></form>`;}
  function sampleCard(s){const c=current(),saved=(c.sampleIds||[]).includes(s.id),selected=ui.compareIds.includes(s.id);return `<article class="sample-card"><div class="row between"><span class="eyebrow">${esc(s.id)}</span><span class="pill warn">模拟案例</span></div><h3>${esc(s.issue)}</h3><div class="sample-meta">${esc(s.domainLabel)} · ${esc(s.region)}<br>${esc(s.court)} · ${s.year} 年</div><p>${esc(s.facts)}</p><div class="row between wrap"><span class="pill">${esc(s.outcome)}</span><button class="link" data-action="sample-detail" data-id="${s.id}">理由与本案差异 ↗</button></div><div class="sample-actions"><button class="button small ${selected?'primary':''}" data-action="compare-sample" data-id="${s.id}">${selected?'✓ 已加入对比':'加入对比'}</button>${ui.screen==='case'?`<button class="link" data-action="pin-sample" data-id="${s.id}">${saved?'✓ 已收藏':'收藏参考'}</button>`:''}</div></article>`;}
  function comparison(){const samples=ui.compareIds.map(id=>REF.samples.find(s=>s.id===id)).filter(Boolean);if(!samples.length)return '';const c=current();
    return `<section class="comparison-panel"><div class="row between"><h3>本案与样本对比</h3><button class="link" data-action="clear-comparison">清空对比</button></div><p class="note">按领域和已填写信息对照，未计算法律相似度。样本事实与裁判理由均为虚构。</p><div class="table-scroll"><table class="compare-table"><thead><tr><th>比较维度</th><th>${ui.screen==='case'?'当前案件':'尚未选择具体案件'}</th>${samples.map(s=>`<th>${esc(s.id)}<br><span class="pill warn">模拟</span></th>`).join('')}</tr></thead><tbody>
    <tr><th>纠纷类型</th><td>${ui.screen==='case'?esc(P.domains[c.fields.dispute?.value]||'待确定'):'—'}</td>${samples.map(s=>`<td>${esc(s.domainLabel)}</td>`).join('')}</tr>
    <tr><th>事实与争点</th><td>${ui.screen==='case'?esc(c.fields.story?.value||'待补充'):'—'}</td>${samples.map(s=>`<td>${esc(s.facts)}</td>`).join('')}</tr>
    <tr><th>处理目标 / 结果</th><td>${ui.screen==='case'?esc(c.fields.goal?.value||'目标待补充'):'—'}</td>${samples.map(s=>`<td>${esc(s.outcome)}<br><small>仅是样本设定</small></td>`).join('')}</tr>
    <tr><th>需核对的差异</th><td>以本案事实与材料逐项核对</td>${samples.map(s=>`<td>${esc(s.check)}</td>`).join('')}</tr></tbody></table></div></section>`;
  }
  function chart(samples,key,title){const rows=R.summarize(samples,key);return `<section class="chart-panel"><div class="row between"><h3>${title}</h3><span class="note">${samples.length} 条</span></div>${rows.length?rows.map((row,i)=>`<div class="chart-row"><div class="row between"><span>${esc(row.label)}</span><span>${row.count} <small>(${row.percent.toFixed(1)}%)</small></span></div><div class="chart-track"><span class="chart-fill tone-${i%3}" style="width:${row.percent}%"></span></div></div>`).join(''):'<p class="flat-empty">当前筛选没有样本</p>'}</section>`;}
  function caseLibrary(statistics){const samples=R.filterCases(REF.samples,effectiveFilters());return `<div class="notice warn"><strong>模拟样本库</strong>　${REF.samples.length} 条界面样例，其中交通事故 24 条。所有案情、法院及结果均为演示设定，真实案例检索待接入。</div>${caseFilters()}
    <div class="row between wrap" style="margin:18px 0"><span class="note">当前筛选 ${samples.length} 条 · 统计仅反映这些模拟样本，不表示本案胜诉概率</span>${statistics?'<button class="link" data-action="browse-cases">浏览案例参考 ↗</button>':'<button class="link" data-action="show-statistics">查看案例统计 ↗</button>'}</div>
    ${statistics?`<div class="metric-grid"><div><strong>${samples.length}</strong><span>当前样本数</span></div><div><strong>${new Set(samples.map(s=>s.court)).size}</strong><span>示例法院数</span></div><div><strong>${samples.length?`${Math.min(...samples.map(s=>s.year))}–${Math.max(...samples.map(s=>s.year))}`:'—'}</strong><span>样本年份范围</span></div></div><div class="chart-grid">${chart(samples,'outcome','裁判结果分布')}${chart(samples,'region','地区分布')}${chart(samples,'year','年份分布')}${chart(samples,'issue','争议焦点分布')}${chart(samples,'court','法院分布')}${chart(samples,'domainLabel','纠纷类型分布')}</div>`:''}
    ${comparison()}${samples.length?`<div class="sample-grid">${samples.slice(0,ui.sampleLimit).map(s=>sampleCard(s)).join('')}</div>${samples.length>ui.sampleLimit?'<div class="footer-action"><button class="button" data-action="more-samples">加载更多样本</button></div>':''}`:'<div class="flat-empty"><h3>没有符合条件的样本</h3><p>可以清空法院、地区或争点条件，重新筛选。</p><button class="button" data-action="reset-filters">重置筛选</button></div>'}`;
  }
  function consultView(){return `<div class="page mode-page">${heading('LEGAL CONSULTATION','先了解法律，再决定下一步','直接阅读法条与类案展示，无需先填写完整案情。')}${researchContent()}<div class="panel"><div class="row between wrap"><div><h3>准备处理自己的纠纷？</h3><p>进入案件办理，逐步核对案情与路径。</p></div><button class="button" data-action="to-case">进入案件办理 →</button></div></div></div>`;}
  function outcomes(c,r){if(!r.intakeConfirmed)return `${heading('SCENARIOS','结果分析','核对案情后，再围绕具体请求分析不同情形。')}${locked('先确认分析所依据的案情',['补充并确认案情摘要'])}`;
    const scenarios=REF.scenarios[c.fields.dispute?.value]||[];return `${heading('SCENARIOS','从前提和证据，比较可能情形','这里恢复情形分析的展示方式，实际结论等待后端依据本案生成。')}
    <div class="notice warn">以下是界面演示情形，不是对本案的实际判断。不会从模拟样本比例推算胜诉率。</div>
    ${scenarios.length?`<div class="scenario-grid">${scenarios.map((s,i)=>`<article class="scenario-card"><div class="row between"><span class="eyebrow">SCENARIO 0${i+1}</span><span class="pill warn">演示情形</span></div><h3>${['相关请求可能获得支持','部分请求仍有争议','信息不足，暂不能判断'][i]}</h3><h4>需核实的前提</h4><p>${esc(s.check)}</p><h4>本案目前记录</h4>${s.focus.map(id=>{const f=P.applicable(c,state.rules).find(f=>f.id===id);return f?`<div class="scenario-fact"><span>${f.label}</span><p>${esc(displayFact(f,c.fields[id]))}</p><button class="link" data-action="edit" data-field="${id}">核对</button></div>`:'';}).join('')}<div class="sample-actions"><button class="button small" data-action="browse-cases">查看类案展示</button><button class="link" data-action="view" data-view="evidence">核对本案材料</button></div></article>`).join('')}</div>`:'<div class="panel"><h3>本类纠纷尚未配置分析样例</h3><p>可以继续查阅法律、整理材料，真实分析待接入。</p></div>'}`;
  }
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
  function visuals(c){return `${heading('CASE MAP','让案情、材料与参考连起来','本案记录与案例统计分别展示，每条对应关系都可以追溯和修改。')}
    ${pills([['timeline','案件时间线'],['relations','主张与证据'],['statistics','案例统计']],ui.visualTab,'visual-tab')}
    ${ui.visualTab==='statistics'?caseLibrary(true):ui.visualTab==='relations'?relationshipMap(c):caseTimeline(c)}`;}
  function caseTimeline(c){return `<div class="notice"><strong>实际程序阶段</strong>　${esc(P.phaseLabels[c.fields.phase?.value]||'待确认')}<br><strong>当前处理意向</strong>　${esc(routeName(c))}　${!readiness().routeConfirmed&&c.route?'（需重新确认）':''}</div>
    <div class="metric-grid"><div><strong>${P.applicable(c,state.rules).filter(f=>c.fields[f.id]?.status==='confirmed').length}</strong><span>已确认事实字段</span></div><div><strong>${c.files.length}</strong><span>已登记材料</span></div><div><strong>${R.validLinks(c).length}</strong><span>用户标记的材料关联</span></div></div>
    <div class="timeline"><div><strong>${esc(c.fields.when?.value||'发生时间待补充')}</strong><p>${esc(c.fields.story?.value||'尚未记录主要经过')}</p><span class="pill">用户填写的案情时间</span></div>${c.audit.map(a=>`<div><strong>${esc(a.time)}</strong><p>${esc(a.text)}</p><span class="pill">系统操作记录 · 非案发时间</span></div>`).join('')}<div><strong>目前记录</strong><p>${c.files.length} 份材料 · ${c.draft?'已生成演示草稿':'尚未生成草稿'} · ${(c.referenceIds||[]).length} 条收藏法条</p></div></div><div class="footer-action"><button class="button" data-action="visual-tab" data-id="relations">查看主张与证据 →</button></div>`;}
  function relationshipMap(c){const fields=P.applicable(c,state.rules).filter(f=>!['dispute','role','phase','goal'].includes(f.id));const links=R.validLinks(c);const claimIds=c.claimFactIds||[];
    return `<div class="row between wrap" style="margin-bottom:18px"><div><h3>当前请求 → 事实 → 材料</h3><p class="note">绿色边框表示已标记；点击材料名称查看具体关系，不自动推定证明力。</p></div><button class="button primary" data-action="graph-edit">整理对应关系</button></div>
    <div class="relation-map"><section class="map-column claim-column"><div class="map-column-title">01 · 当前请求</div><div class="map-node claim-node"><span class="eyebrow">CLAIM</span><h3>${esc(c.fields.goal?.value||'请求目标待补充')}</h3><span class="pill ${c.fields.goal?.status==='confirmed'?'good':'warn'}">${c.fields.goal?.status==='confirmed'?'用户已确认':'待补充或确认'}</span><p>${claimIds.length} 条事实已由用户标记为与请求相关</p><p>${claimIds.map(id=>esc(P.fields.find(f=>f.id===id)?.label||id)).join("<br>→ ")}</p><button class="link" data-action="edit" data-field="goal">核对请求</button></div><div class="map-legend"><span class="solid-line"></span> 用户标记关联<br><span class="dashed-line"></span> 尚未建立关联</div></section>
    <section class="map-column"><div class="map-column-title">02 · 案件事实</div>${fields.map(f=>{const related=claimIds.includes(f.id),fileLinks=links.filter(l=>l.field===f.id);return `<article class="map-node ${related?'connected':'unlinked'}"><div class="row between"><span class="eyebrow">${esc(f.label)}</span><span class="pill ${c.fields[f.id]?.status==='confirmed'?'good':'warn'}">${c.fields[f.id]?.status==='confirmed'?'已确认':c.fields[f.id]?.status==='unknown'?'待核实':'待确认'}</span></div><p>${esc(displayFact(f,c.fields[f.id]))}</p><div class="map-connection">${related?'← 与当前请求相关':'请求关联待标记'} · ${fileLinks.length} 份材料 →</div>${fileLinks.map(l=>`<button class="map-file-link" data-action="file-detail" data-id="${l.fileId}">${esc(c.files.find(x=>x.id===l.fileId)?.name)} ↗</button>`).join('')}</article>`;}).join('')}</section>
    <section class="map-column"><div class="map-column-title">03 · 已登记材料</div>${c.files.length?c.files.map((f,i)=>{const matches=links.filter(l=>l.fileId===f.id);return `<article class="map-node ${matches.length?'connected':'unlinked'}"><span class="eyebrow">E${String(i+1).padStart(3,'0')} · ${f.demo?'演示记录':'本地登记'}</span><h3>${esc(f.name)}</h3><p>${matches.length?matches.map(l=>`${esc(P.fields.find(x=>x.id===l.field)?.label||l.field)}${l.note?`：${esc(l.note)}`:''}`).join('<br>'):'尚未关联到事实'}</p><button class="link" data-action="file-detail" data-id="${f.id}">查看材料记录 ↗</button></article>`;}).join(''):'<div class="map-node unlinked"><h3>材料尚未登记</h3><p>添加材料后，再标记它与哪项事实有关。</p><button class="button small" data-action="upload">添加材料</button></div>'}<div class="note">原型未解析材料。用户标记“有关联”不表示材料真实或足以证明该事实。</div></section></div>
    <div class="panel"><div class="row between wrap"><div><h3>想先看一份完整图谱？</h3><p>新建独立演示案件，包含虚构材料名称与明确标注的关联。</p></div><button class="button" data-action="graph-demo">打开图谱演示案件</button></div></div>`;
  }
  function graphEditor(){const c=current(),fields=P.applicable(c,state.rules).filter(f=>!['dispute','role','phase','goal'].includes(f.id)&&Object.hasOwn(c.fields,f.id));const links=R.validLinks(c);
    openDialog('整理主张、事实与材料',`<form id="graph-form"><h3>哪些事实与当前请求有关？</h3><p>这里只显示已有记录的事实；请先补充缺失字段，再建立关系。不评价法律证明力。</p><div class="graph-checks">${fields.map(f=>`<label class="checkbox"><input type="checkbox" name="claim-field" value="${f.id}" ${(c.claimFactIds||[]).includes(f.id)?'checked':''}>${esc(f.label)}</label>`).join('')}</div><h3 style="margin:22px 0 12px">为每份材料标记关联事实</h3><p>一份材料可以关联多项事实。取消勾选即可解除关联。</p>${c.files.length?c.files.map(file=>`<div class="graph-file-form"><strong>${esc(file.name)}</strong><div class="graph-checks">${fields.map(f=>`<label class="checkbox"><input type="checkbox" name="material-link" data-file="${file.id}" value="${f.id}" ${links.some(l=>l.fileId===file.id&&l.field===f.id)?'checked':''}>${esc(f.label)}</label>`).join('')}</div></div>`).join(''):'<p>尚无材料，可先保存请求与事实的关系。</p>'}</form>`,`<button class="button" data-action="close">取消</button><button class="button primary" type="submit" form="graph-form">保存用户标记</button>`);
  }
  function lawDetail(id){const law=REF.laws.find(l=>l.id===id);if(!law)return;openDialog(`${esc(law.title)} · ${law.article}`,`<div class="notice">条文原文 · 请结合适用条件核对，收藏不会自动认定适用于本案。</div><p class="source-text">${esc(law.text)}</p><dl class="source-meta"><dt>来源 ID</dt><dd>${esc(law.id)}</dd><dt>版本 / 生效</dt><dd>${esc(law.version)} / ${law.effective}</dd><dt>来源定位</dt><dd>${esc(law.file||law.title)} · ${law.article}${law.page?` · 随库 PDF 第 ${law.page} 页（并非网页页码）`:''}</dd></dl><a class="button" href="${esc(law.url)}" target="_blank" rel="noopener noreferrer">打开法院官方全文 ↗</a>`);}
  function sampleDetail(id){const s=REF.samples.find(x=>x.id===id);if(!s)return;const c=current();openDialog(`模拟案例 · ${esc(s.issue)}`,`<div class="notice warn">${esc(s.sourceLabel)}。无真实案号、法院原文和出处链接，不可作为司法引用。</div><dl class="source-meta"><dt>演示编号</dt><dd>${s.id}</dd><dt>类型 / 地区</dt><dd>${esc(s.domainLabel)} / ${esc(s.region)}</dd><dt>法院 / 年份</dt><dd>${esc(s.court)} / ${s.year}</dd><dt>案号 / 原文链接</dt><dd>未提供（虚构样例）</dd></dl><h3>样例事实</h3><p>${esc(s.facts)}</p><h3>理由展示</h3><p>${esc(s.reason)}</p><h3>与当前案件需要核对的差异</h3><p>${esc(s.check)}</p>${ui.screen==='case'?`<div class="notice">本案当前记录：${esc(c.fields.story?.value||'尚未填写事实')}<br>尚未进行真实案例相似度分析。</div>`:''}`);}
  function fileDetail(id){const c=current(),f=c.files.find(x=>x.id===id);if(!f)return;const links=R.validLinks(c).filter(l=>l.fileId===id);openDialog('材料登记与关联',`<h3>${esc(f.name)}</h3><p>${(f.size/1024).toFixed(1)} KB · ${f.demo?'演示材料记录':'仅保存文件元数据'}</p><div class="notice warn">此原型不保存或解析文件正文。材料尚未经过 OCR、真伪或证明力核验。</div><h3>用户标记的关联事实</h3>${links.length?links.map(l=>`<p>${esc(P.fields.find(x=>x.id===l.field)?.label||l.field)}${l.note?`：${esc(l.note)}`:''}</p>`).join(''):'<p>尚未标记</p>'}`);}

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
    if(a==='consult'){ui.screen='consult';ui.menu=false;if(ui.researchTab==='saved')ui.researchTab='laws';return render();}
    if(a==='to-case'){ui.screen='case';ui.view='chat';return render();}
    if(a==='overview')return go('review');
    if(a==='rules')return rulesDialog();
    if(a==='edit')return editField(button.dataset.field);
    if(a==='unknown')return editField(button.dataset.field,true);
    if(a==='upload')return document.querySelector('#upload').click();
    if(a==='remove-file'){c.files=c.files.filter(f=>f.id!==button.dataset.id);c.materialLinks=R.validLinks(c);audit(c,'移除一份材料登记，并清理对应关系。');save();return render();}
    if(a==='new'){openDialog('新建案件',`<form id="new-form"><label for="new-title">案件名称</label><input type="text" id="new-title" placeholder="例如：房屋买卖合同纠纷" maxlength="120" required><label for="new-domain" style="margin-top:18px">初步纠纷类型</label><select id="new-domain">${Object.entries(P.domains).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></form>`,`<button class="button" data-action="close">取消</button><button class="button primary" type="submit" form="new-form">开始收集案情</button>`);return;}
    if(a==='confirm-intake'){if(r.blockers.length)return toast('仍有必答信息未完成。');c.intakeConfirmation={basis:r.signature,at:new Date().toISOString(),actor:'local-user'};audit(c,'用户确认本版案情摘要。');save();go('workflow');return;}
    if(a==='choose-route')return chooseRoute(button.dataset.route);
    if(a==='confirm-route'){const route=button.dataset.route;if(!r.canSelectRoute||!P.routeOptions(c).some(o=>o.id===route)){dialog.close();return toast('案情已变化，请重新确认。');}c.route=route;c.routeConfirmation={basis:r.signature,at:new Date().toISOString(),actor:'local-user'};audit(c,`选择“${routeName(c)}”，实际程序阶段保持“${P.phaseLabels[c.fields.phase.value]}”。`);save();dialog.close();go('workflow');toast('已保存你的选择。');return;}
    if(a==='research-tab'){ui.researchTab=button.dataset.id;return render();}
    if(a==='law-scope'){ui.lawScope=button.dataset.id;return render();}
    if(a==='clear-law'){ui.researchQuery='';ui.lawScope='all';return render();}
    if(a==='law-detail')return lawDetail(button.dataset.id);
    if(a==='sample-detail')return sampleDetail(button.dataset.id);
    if(a==='file-detail')return fileDetail(button.dataset.id);
    if(a==='pin-law'||a==='pin-sample'){const key=a==='pin-law'?'referenceIds':'sampleIds';c[key]=R.toggleId(c[key]||[],button.dataset.id);save();render();return;}
    if(a==='visual-tab'){ui.visualTab=button.dataset.id;return render();}
    if(a==='browse-cases'){ui.researchTab='cases';if(ui.screen==='consult')return render();return go('research');}
    if(a==='show-statistics'){if(ui.screen==='consult'){ui.researchTab='stats';return render();}ui.visualTab='statistics';return go('visuals');}
    if(a==='compare-sample'){if(!ui.compareIds.includes(button.dataset.id)&&ui.compareIds.length>=3)return toast('最多同时对比三条样本，请先取消一条。');ui.compareIds=R.toggleId(ui.compareIds,button.dataset.id);return render();}
    if(a==='clear-comparison'){ui.compareIds=[];return render();}
    if(a==='more-samples'){ui.sampleLimit+=6;return render();}
    if(a==='reset-filters'){ui.caseFilters={domain:'current',region:'all',court:'all',year:'all',outcome:'all',query:''};ui.sampleLimit=6;return render();}
    if(a==='graph-edit')return graphEditor();
    if(a==='graph-demo'){let demo=state.cases.find(x=>x.graphDemo);if(!demo){demo=seed().cases[1];demo.graphDemo=true;demo.title='图谱演示 · 借款材料';demo.files=[{id:uid(),name:'演示_转账记录.pdf',size:128000,demo:true},{id:uid(),name:'演示_借款说明.txt',size:1200,demo:true}];demo.claimFactIds=['principal','story','has_repaid'];demo.materialLinks=[{fileId:demo.files[0].id,field:'principal'},{fileId:demo.files[1].id,field:'story'},{fileId:demo.files[1].id,field:'when'}];audit(demo,'载入虚构的图谱演示记录；材料未解析，关联仅为展示。');state.cases.push(demo);}state.activeId=demo.id;ui.visualTab='relations';save();return go('visuals');}
    if(a==='example-law'){ui.researchTab='laws';ui.lawScope='all';ui.researchQuery='开庭以后还能调解吗？';ui.researchSearched=true;return render();}
    if(a==='task'){if(!r.routeConfirmed)return toast('请重新确认当前处理路径。');openDialog('记录实际办理反馈',`<form id="task-form" data-index="${button.dataset.index}"><label for="task-feedback">实际发生了什么？</label><textarea id="task-feedback" placeholder="例如：已联系对方，对方表示愿意了解调解" maxlength="2000" required></textarea><p class="note">该记录不会自动变更实际程序阶段。实际立案或开庭后，请更新对应案情字段。</p></form>`,`<button class="button primary" type="submit" form="task-form">保存反馈</button>`);return;}
    if(a==='export'){if(!r.canDraft||c.draft?.basis!==r.signature)return toast('请重新核对案情并生成草稿。');return download('CivRA_起诉状_演示草稿.md',`# 民事起诉状（本地演示草稿）\n\n${c.draft.text}\n\n本文件为界面演示输出，尚未填写法院原始模板。`);}
  }
  document.addEventListener('click',event=>{const b=event.target.closest('[data-action]');if(b)action(b);});
  document.addEventListener('input',event=>{if(event.target.id==='case-search'){ui.query=event.target.value;document.querySelector('#case-list').innerHTML=caseList();}});
  document.addEventListener('change',event=>{if(event.target.dataset.filter){ui.caseFilters[event.target.dataset.filter]=event.target.value;ui.sampleLimit=6;render();}});
  document.addEventListener('submit',event=>{
    const form=event.target;event.preventDefault();const c=current(),r=readiness();
    if(form.id==='question-form'){const value=document.querySelector('#answer').value.trim();if(!value)return;const f=P.fields.find(f=>f.id===form.dataset.field);if(f.mode==='known'&&!P.known(makeFact(value)))return toast('请填写明确内容，未知项不能计作已确认。');changeFact(form.dataset.field,makeFact(value));ui.activeQuestion=null;render();toast('已确认，继续处理下一项。');}
    if(form.id==='field-form'){const unknown=form.dataset.unknown==='true';const value=document.querySelector(unknown?'#unknown-reason':'#edit-value').value.trim();if(!value)return;changeFact(form.dataset.field,unknown?{value:'',status:'unknown',reason:value}:makeFact(value));dialog.close();render();}
    if(form.id==='chat-form'){const input=document.querySelector('#chat-input');if(!input.value.trim())return;c.messages.push(input.value.trim());audit(c,'保存一条补充陈述，尚未自动更新事实。');save();render();toast('已保留补充陈述，请通过案情卡确认结构化内容。');}
    if(form.id==='new-form'){const created=newCase(document.querySelector('#new-title').value.trim(),document.querySelector('#new-domain').value);if(!created.title)return;state.cases.unshift(created);state.activeId=created.id;ui.screen='case';ui.view='chat';ui.menu=false;ui.activeQuestion=null;save();dialog.close();render();}
    if(form.id==='rules-form'){const rules={};for(const [name,value]of new FormData(form)){const id=name.slice(5),f=P.fields.find(x=>x.id===id);if(f&&value!==f.mode)rules[id]=value;}state.rules=rules;state.cases.forEach(c=>{c.intakeConfirmation=null;c.routeConfirmation=null;});save();dialog.close();render();toast('已更新本地规则，请重新核对案情。');}
    if(form.id==='graph-form'){c.claimFactIds=Array.from(form.querySelectorAll('[name=claim-field]:checked'),el=>el.value);c.materialLinks=Array.from(form.querySelectorAll('[name=material-link]:checked'),el=>({fileId:el.dataset.file,field:el.value}));audit(c,'用户更新主张、事实与材料的对应关系；未认定证明力。');save();dialog.close();render();toast('已保存对应关系。');}
    if(form.id==='case-filter-form'){ui.caseFilters.query=document.querySelector('#sample-query').value.trim();ui.sampleLimit=6;render();}
    if(form.id==='research-form'){ui.researchQuery=document.querySelector('#research-input').value.trim();ui.researchSearched=true;render();}
    if(form.id==='task-form'){if(!r.routeConfirmed)return toast('请重新确认路线。');const feedback=document.querySelector('#task-feedback').value.trim();if(!feedback)return;const key=`${c.route}:${form.dataset.index}`;if(!c.tasks.includes(key))c.tasks.push(key);audit(c,`用户反馈：${feedback}`);save();dialog.close();render();}
    if(form.id==='draft-form'){if(!r.canDraft)return toast('当前不满足文书准备条件。');if(!document.querySelector('#approve-wording').checked)return;const wording=document.querySelector('#wording').value.trim();if(!wording)return;c.draftForm={plaintiff:document.querySelector('#plaintiff').value.trim(),defendant:document.querySelector('#defendant').value.trim(),wording};const f=c.draftForm;c.draft={basis:r.signature,text:`原告：${f.plaintiff||'［姓名或名称待补充］'}\n被告：${f.defendant||'［姓名或名称待补充］'}\n身份及送达信息：［待补充并核对］\n\n诉讼请求\n${f.wording}\n\n事实与理由\n${c.fields.story?.value||'［待补充］'}\n\n法律依据与证据对应关系：［待检索、关联并核对］\n\n此致\n［法院名称待核实］\n\n具状人：［待签署］\n日期：［待填写］`};audit(c,'用户确认正式表述，生成一份演示草稿。');save();render();toast('已生成演示草稿，未知内容保留为待补充。');}
  });
  document.querySelector('#upload').addEventListener('change',event=>{const c=current();for(const file of event.target.files)c.files.push({id:uid(),name:file.name,size:file.size});save();event.target.value='';go('evidence');toast('已登记本地材料信息，尚未解析文件内容。');});
  dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}});
  render();
})();
