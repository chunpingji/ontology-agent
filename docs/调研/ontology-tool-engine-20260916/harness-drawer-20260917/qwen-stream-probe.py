import json
from pathlib import Path
from time import monotonic
from sqlalchemy import create_engine
from app.models.model_request import LocalModelPool, LocalModelRequest
from app.services.llm.local_client import LocalModelClient, responses_create
from app.services.llm.model_runtime import model_scope
from app.config import settings

out = Path('/tmp/harness-qwen-stream-probe')
out.mkdir(exist_ok=True)
engine = create_engine('sqlite:///' + str(out / 'requests.sqlite'))
LocalModelPool.__table__.create(engine, checkfirst=True)
LocalModelRequest.__table__.create(engine, checkfirst=True)
client = LocalModelClient('http://127.0.0.1:8080/v1', settings.local_llm_api_key)
model = 'Qwen3.6-35B-A3B'
start = monotonic()
events = []
def observe(kind, value):
    events.append({'kind': kind, 'time_s': round(monotonic()-start, 3),
                   'channel': value.get('channel'), 'characters': len(value.get('text','')),
                   'status': value.get('status')})
schema = {'type': 'object', 'properties': {'label': {'type': 'string'}},
          'required': ['label'], 'additionalProperties': False}
tool = {'type':'function', 'name':'read_demo_label', 'description':'Read a synthetic label.',
        'strict':False, 'parameters':{'type':'object','properties':{},'additionalProperties':False}}
instructions = 'This is a synthetic streaming protocol test. First call read_demo_label. After its result return only JSON with label equal to the returned label.'
inputs = [{'role':'user', 'content':'请调用工具读取测试标签，然后返回规定 JSON。'}]
with model_scope(bind=engine, on_harness_event=observe):
    first = responses_create(client, model=model, input_items=inputs, instructions=instructions,
        tools=[tool], tool_choice={'type':'function','name':'read_demo_label'},
        reasoning={'effort':'none'}, max_output_tokens=256, total_timeout_s=90)
    calls = [item for item in first.output_items if item['type']=='function_call']
    if first.response_status != 'completed' or len(calls)!=1:
        raise RuntimeError('probe_tool_call_not_completed')
    inputs += first.output_items + [{'type':'function_call_output','call_id':calls[0]['call_id'],
                                     'output':json.dumps({'label':'流式验证'})}]
    second = responses_create(client, model=model, input_items=inputs, instructions=instructions,
        text_format={'type':'json_schema','name':'probe','strict':False,'schema':schema},
        reasoning={'effort':'none'}, max_output_tokens=256, total_timeout_s=90)
text = ''.join(part['text'] for item in second.output_items if item['type']=='message'
               for part in item['content'] if part['type']=='output_text')
assert json.loads(text) == {'label':'流式验证'}
assert second.response_status == 'completed'
assert any(item['kind']=='delta' for item in events)
result = {'model':model, 'elapsed_s':round(monotonic()-start,3), 'stream_events':events,
          'tool_call_paired':True,'structured_answer':json.loads(text),
          'usage':[first.usage,second.usage]}
(out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False))
