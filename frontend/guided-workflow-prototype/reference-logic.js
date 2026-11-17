(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.ReferenceLogic=factory();})(globalThis,function(){
  'use strict';
  const normalize=value=>String(value||'').normalize('NFKC').replace(/\s+/g,'').toLowerCase();
  function searchLaws(laws,query='',domain='all'){
    const pool=laws.filter(law=>domain==='all'||law.domain===domain||law.domain==='procedure');
    const q=normalize(query);if(!q)return pool;
    const article=q.match(/第([0-9零〇一二三四五六七八九十百千万]+)条/)||q.match(/^([0-9]+)$/);
    if(article)return pool.filter(law=>law.number===article[1]||normalize(law.article)===`第${article[1]}条`);
    return pool.map(law=>({law,score:(law.topics||[]).filter(word=>q.includes(word)).length+(normalize(law.text).includes(q)?2:0)+(normalize(law.title).includes(q)?1:0)}))
      .filter(x=>x.score>0).sort((a,b)=>b.score-a.score).map(x=>x.law);
  }
  function filterCases(samples,filters={}){
    return samples.filter(s=>(!filters.domain||filters.domain==='all'||s.domain===filters.domain)&&
      (!filters.region||filters.region==='all'||s.region===filters.region)&&(!filters.court||filters.court==='all'||s.court===filters.court)&&
      (!filters.year||filters.year==='all'||String(s.year)===String(filters.year))&&(!filters.outcome||filters.outcome==='all'||s.outcome===filters.outcome)&&
      (!filters.query||normalize(`${s.id}${s.title}${s.facts}${s.reason}`).includes(normalize(filters.query))));
  }
  function summarize(samples,dimension){const counts=new Map();for(const s of samples){const label=String(s[dimension]??'未知');counts.set(label,(counts.get(label)||0)+1);}
    return Array.from(counts,([label,count])=>({label,count,percent:samples.length?count/samples.length*100:0})).sort((a,b)=>b.count-a.count||a.label.localeCompare(b.label,'zh-CN'));
  }
  function validLinks(c){const ids=new Set((c.files||[]).map(f=>f.id));return (c.materialLinks||[]).filter(l=>ids.has(l.fileId)&&Object.hasOwn(c.fields,l.field));}
  function toggleId(ids,id){return ids.includes(id)?ids.filter(x=>x!==id):[...ids,id];}
  return {searchLaws,filterCases,summarize,validLinks,toggleId};
});
