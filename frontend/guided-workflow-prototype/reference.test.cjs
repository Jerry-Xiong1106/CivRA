const test = require('node:test');
const assert = require('node:assert/strict');
const R = require('./reference-logic.js');
const {laws,samples} = require('./reference-data.js');
const P = require('./policy.js');

test('exact article lookup supports Arabic, Chinese and full-width numbers',()=>{
  for(const query of ['第680条','第六百八十条','６８０'])assert.deepEqual(R.searchLaws(laws,query).map(l=>l.number),['680']);
  assert.equal(R.searchLaws(laws,'第999999条').length,0);
});
test('domain filter retains procedure articles but does not substitute unrelated law',()=>{
  assert.equal(R.searchLaws(laws,'开庭以后还能调解吗？','traffic')[0].number,'145');
  assert.equal(R.searchLaws(laws,'第680条','traffic').length,0);
  assert.equal(R.searchLaws(laws,'完全不在样例中的宇航规则').length,0);
});
test('case filters combine domain, court, region, year and issue',()=>{
  const filtered=R.filterCases(samples,{domain:'traffic',region:'广州示例',court:'示例法院甲',year:'2024',query:'医疗'});
  assert.equal(filtered.length,4);
  assert.ok(filtered.every(s=>s.domain==='traffic'&&s.region==='广州示例'&&s.year===2024));
  assert.equal(R.filterCases(samples,{region:'广州示例',court:'示例法院乙'}).length,0);
});
test('charts count the filtered denominator and handle empty selection',()=>{
  const filtered=R.filterCases(samples,{domain:'loan',year:'2025'}),rows=R.summarize(filtered,'outcome');
  assert.equal(filtered.length,2);assert.equal(rows.reduce((n,r)=>n+r.count,0),2);
  assert.equal(rows.reduce((n,r)=>n+r.percent,0),100);assert.deepEqual(R.summarize([],'outcome'),[]);
});
test('all simulated cases are explicitly synthetic with no fabricated citation identifiers',()=>{
  assert.equal(samples.length,36);assert.equal(samples.filter(s=>s.domain==='traffic').length,24);
  assert.equal(new Set(samples.map(s=>s.id)).size,samples.length);
  assert.ok(samples.every(s=>s.synthetic&&s.id.startsWith('DEMO-')&&s.caseNumber===null&&s.sourceUrl===null&&s.sourceRefs.length===0));
});
test('deleted material and missing facts cannot leave displayed dangling links',()=>{
  const c={fields:{story:{}},files:[{id:'a'}],materialLinks:[{fileId:'a',field:'story'},{fileId:'deleted',field:'story'},{fileId:'a',field:'absent'}]};
  assert.deepEqual(R.validLinks(c),[{fileId:'a',field:'story'}]);c.files=[];assert.deepEqual(R.validLinks(c),[]);
});
test('collecting references and marking relations cannot confirm facts or unlock route',()=>{
  const c={fields:{dispute:{value:'traffic',status:'confirmed'}},files:[]};
  const before=P.basis(c);c.referenceIds=[laws[0].id];c.sampleIds=[samples[0].id];c.claimFactIds=['story'];
  assert.equal(P.basis(c),before);assert.equal(P.assess(c).canSelectRoute,false);assert.equal(P.assess(c).canDraft,false);
});
