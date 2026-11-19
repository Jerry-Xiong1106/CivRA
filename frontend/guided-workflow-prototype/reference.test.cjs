const test = require('node:test');
const assert = require('node:assert/strict');
const R = require('./reference-logic.js');
const {samples} = require('./reference-data.js');

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
