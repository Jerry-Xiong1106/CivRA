"""Live two-entry acceptance, with empty synthetic cases and bounded budgets."""
from datetime import datetime, timezone
from getpass import getpass
import json, os, sys, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.stdout.reconfigure(encoding='utf-8')
from backend.common.config import load_dotenv
from backend.agent.factory import build_runner
from backend.agent.model.llm import make_chat_model
from backend.agent.runtime.store import RunStore
from backend.case import CaseService,SQLiteCaseRepository
from backend.case.models import CaseSnapshot
from backend.case.research.knowledge_bridge import CorpusSourceResolver
from backend.common.schemas import RunConfig,UserReply
load_dotenv()
key=os.environ.get('DEEPSEEK_API_KEY') or getpass('DeepSeek API key (not echoed): ').strip()
model=make_chat_model(model='deepseek-flash',base_url='https://api.deepseek.com',api_key=key,timeout_s=45)
model=model.model_copy(update={'extra_body':{'thinking':{'type':'disabled'}}})
base=ROOT/'var/manual-agent'/('two-routes-'+datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S'))
base.mkdir(parents=True)
queries=[('rule','借车给朋友，发生事故时车主是否一定承担责任？请解释一般规则。','consultation'),
         ('personal','被电动车撞了，他伤得比我重，他的医药费大于我，怎么处理？','case_work'),
         ('meaning','民事起诉状是什么？我只想了解它的用途。','consultation'),
         ('routes','朋友欠我一万元，我想先比较协商、调解、诉讼，然后选一个处理方向。费用不知道就列为未知。','case_work')]
queries.extend([
    ('lease', '房东拖着不退押金，我想追回，应该从哪里开始？', 'case_work'),
    ('sales', '我买的家具到货破损，商家拒绝退款，帮我处理。', 'case_work'),
    ('renovation', '装修公司没有按合同完工，我要准备交涉材料。', 'case_work'),
    ('definition', '我只想了解合同解除和撤销有什么区别。', 'consultation'),
])
results=[]
for name,message,expected in queries:
    d=base/name;d.mkdir();stamp=datetime.now(timezone.utc)
    repo=SQLiteCaseRepository(d/'cases.sqlite3')
    case=repo.create_case(CaseSnapshot(case_id='smoke_'+uuid.uuid4().hex,case_version=1,synthetic=True,
        dispute_type=None,jurisdiction='CN-Mainland',stage='consultation',selected_route=None,created_at=stamp,updated_at=stamp))
    s=build_runner(llm=model,case_service=CaseService(repo,source_resolver=CorpusSourceResolver.from_directory()),
        store=RunStore(d/'runs'),config=RunConfig(max_model_calls=8,max_tool_calls=8,max_total_tokens=100000,max_output_tokens=1600,max_run_seconds=150))
    info=s.runner.start_run(case.case_id,message)
    initial=info.conversation_route;waits=[]
    while info.status=='waiting_user' and len(waits)<2:
        waits.append(info.wait.model_dump(mode='json'))
        if info.wait.kind=='route_selection':
            if 'mediation' not in {o.route for o in info.wait.options}:break
            reply=UserReply(route_choice='mediation',text='选择调解，请给我初步清单，未知信息保留待补。')
        else:
            reply=UserReply(text='细节暂不清楚，请先给有条件的初步解释，不需要替我选择处理路线。')
        info=s.runner.resume_run(info.run_id,reply,wait_id=info.wait.wait_id,expected_case_version=info.case_version)
    trace=[t.model_dump(mode='json') for t in s.runner.get_trace(info.run_id)]
    cp=s.runner.store.load_checkpoint(info.run_id)
    checks={'route_matches':initial==expected,'completed':info.status=='completed',
            'nonempty':bool(info.final and info.final.answer),'search_bound':cp.search_calls<=3,'question_bound':cp.clarification_rounds<=2}
    if name in ('rule','meaning','definition'):checks['no_questions']=not waits
    if name=='routes':checks['route_selected']=repo.get_case(case.case_id).selected_route=='mediation'
    if expected == "case_work":
        checks["task_recorded"] = bool(info.tasks)
        checks["at_most_one_current"] = sum(t.status in ("active","partial","waiting_input") for t in info.tasks) <= 1
    result={'prompt':message,'initial_route':initial,'expected_route':expected,'checks':checks,'waits':waits,
            'final':info.model_dump(mode='json'),'trace':trace,'routing_reason':cp.routing_reason}
    (d/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    results.append({'scenario':name,'checks':checks,'route':initial,'model_calls':info.usage.model_calls})
    print(json.dumps(results[-1],ensure_ascii=False),flush=True)
(base/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print('OUTPUT',base)
