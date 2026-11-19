(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.ReferenceLogic=factory();})(globalThis,function(){
  'use strict';
  const normalize=value=>String(value||'').normalize('NFKC').replace(/\s+/g,'').toLowerCase();
  function filterCases(samples,filters={}){
    return samples.filter(s=>(!filters.domain||filters.domain==='all'||s.domain===filters.domain)&&
      (!filters.region||filters.region==='all'||s.region===filters.region)&&(!filters.court||filters.court==='all'||s.court===filters.court)&&
      (!filters.year||filters.year==='all'||String(s.year)===String(filters.year))&&(!filters.outcome||filters.outcome==='all'||s.outcome===filters.outcome)&&
      (!filters.query||normalize(`${s.id}${s.title}${s.facts}${s.reason}`).includes(normalize(filters.query))));
  }
  function summarize(samples,dimension){const counts=new Map();for(const s of samples){const label=String(s[dimension]??'未知');counts.set(label,(counts.get(label)||0)+1);}
    return Array.from(counts,([label,count])=>({label,count,percent:samples.length?count/samples.length*100:0})).sort((a,b)=>b.count-a.count||a.label.localeCompare(b.label,'zh-CN'));
  }
  return {filterCases,summarize};
});
