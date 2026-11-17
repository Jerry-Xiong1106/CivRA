"""Manual terminal for real CaseService + DeepSeek Flash. No source/config edits."""
import argparse
from datetime import datetime, timezone
from getpass import getpass
import json
import os
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'scripts/agent'))
from backend.common.config import load_dotenv
from backend.agent.factory import build_runner
from backend.agent.model.llm import make_chat_model,response_from_message
from backend.agent.runtime.store import RunStore
from backend.common.schemas import RunConfig
from backend.case import CaseService,SQLiteCaseRepository
from backend.case.models import CaseSnapshot
from backend.case.research.knowledge_bridge import CorpusSourceResolver
from backend.case.drafting.documents import MeteredLLMDraftingGateway
from backend.case.review.verification import MeteredLLMReviewGateway
from backend.knowledge.retrieval.service import RetrievalService
from langchain_core.messages import convert_to_messages
from run_agent import Progress,ask_user,report

def create_blank_case(repo):
    stamp=datetime.now(timezone.utc)
    return repo.create_case(CaseSnapshot(
        case_id='manual_'+uuid.uuid4().hex,case_version=1,synthetic=True,
        dispute_type=None,jurisdiction='CN-Mainland',stage='consultation',selected_route=None,
        created_at=stamp,updated_at=stamp,
    ))


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('--check',action='store_true')
    parser.add_argument('--fresh',action='store_true',help='start every new case empty, without a fixture menu')
    args=parser.parse_args()
    if args.check:
        print('Interactive imports OK; no API called.');return
    load_dotenv()
    print('\nCivRA 真实业务交互测试 | DeepSeek 官方 deepseek-flash | 非思考模式')
    print('密钥仅存在于本进程内存，不写入 .env、源码或运行记录。')
    key=os.environ.get('DEEPSEEK_API_KEY') or getpass('请输入 DeepSeek API Key（不回显）：').strip()
    if not key:print('未输入密钥，退出。');return
    model=make_chat_model(model='deepseek-flash',base_url='https://api.deepseek.com',api_key=key,timeout_s=60)
    model=model.model_copy(update={'extra_body':{'thinking':{'type':'disabled'}}})
    directory=ROOT/'var/manual-agent'/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    directory.mkdir(parents=True)
    repo=SQLiteCaseRepository(directory/'cases.sqlite3')
    gateway_calls=[]
    def call_model(*,messages,max_tokens,temperature):
        if len(gateway_calls)>=6:raise RuntimeError('本次运行的额外模型调用已达6次上限')
        print('\n  [业务内部 AI] 正在起草或进行语义核验……',flush=True)
        record={'messages':messages,'status':'running'};gateway_calls.append(record)
        try:
            reply=model.bind(max_tokens=max_tokens,temperature=temperature).invoke(convert_to_messages(messages))
            response=response_from_message(reply,model)
            record.update(status='completed',response=response.model_dump(mode='json'));return response
        except Exception as exc:
            record.update(status='error',error_type=type(exc).__name__);raise
    service=CaseService(repo,source_resolver=CorpusSourceResolver.from_directory(),
        drafting_gateway=MeteredLLMDraftingGateway(call_model),semantic_gateway=MeteredLLMReviewGateway(call_model))
    config=RunConfig(max_steps=40,max_model_calls=12,max_tool_calls=16,max_total_tokens=180000,
                     max_run_seconds=300,max_output_tokens=2200)
    setup=build_runner(llm=model,case_service=service,retrieval_service=RetrievalService.from_config(),
                       store=RunStore(directory/'runs'),config=config)
    case_id=None
    print('Real tools: '+', '.join(t.name for t in setup.tools))
    print('Records: '+str(directory))
    print('Start with an empty case. Commands: /new, /state, /exit')
    try:
        while True:
            if case_id is None:
                case=create_blank_case(repo)
                case_id=case.case_id
                print('New empty case: '+case_id)
            question=input('\n你：').strip()
            if not question:continue
            if question=='/exit':break
            if question=='/new':case_id=None;continue
            if question=='/state':print(repo.get_case(case_id).model_dump_json(indent=2));continue
            gateway_calls.clear()
            progress=Progress(setup.runner,case_id)
            # Progress must follow the new run rather than the case's previous run.
            existing={cp.run_id for cp in setup.runner.store.list_checkpoints()}
            original_print=progress._print_new_steps
            def print_new():
                if progress.run_id is None:
                    progress.run_id=next((cp.run_id for cp in setup.runner.store.list_checkpoints()
                        if cp.case_id==case_id and cp.run_id not in existing),None)
                    if progress.run_id is None:return
                    print('正在识别咨询或办理入口；检索和追问受公共限制。',flush=True)
                original_print()
            progress._print_new_steps=print_new
            try:
                info=progress.run(lambda:setup.runner.start_run(case_id,question,actor_id='synthetic_user'))
                while info.status=='waiting_user':
                    reply=ask_user(info.wait,None)
                    if reply is None:info=setup.runner.cancel_run(info.run_id);break
                    info=progress.run(lambda:setup.runner.resume_run(info.run_id,reply,wait_id=info.wait.wait_id,
                        expected_case_version=info.case_version,actor_id='synthetic_user'))
                report(setup.runner,info)
                (directory/(info.run_id+'-gateway.json')).write_text(json.dumps(gateway_calls,ensure_ascii=False,indent=2),encoding='utf-8')
                print('本次额外起草/审查模型调用：'+str(len(gateway_calls))+'；与上方主Agent用量分开统计。')
            except Exception as exc:
                print('运行异常：'+type(exc).__name__+' '+str(exc).replace(key,'[REDACTED]')[:500])
            print('下一条问题会开启新运行，并沿用当前案件数据；/new 可创建独立测试案件。')
    finally:
        print('已退出，记录保留在 '+str(directory))

if __name__=='__main__':
    try:main()
    except (KeyboardInterrupt,EOFError):print('\n交互已结束。')
