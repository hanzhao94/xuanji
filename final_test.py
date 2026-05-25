import urllib.request, json, time, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

tests = [
    ('简单查询', '查一下今天北京的天气'),
    ('计算任务', '计算 123 * 456'),
    ('文件操作', '在C:/temp写一个test.txt，内容是hello'),
    ('游戏任务', '用Python写一个贪吃蛇游戏'),
]

for name, task_text in tests:
    task = {'task': task_text}
    body = json.dumps(task).encode('utf-8')
    req = urllib.request.Request('http://127.0.0.1:18790/api/tasks', data=body, headers={'Content-Type': 'application/json'})
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read().decode())
    task_id = result.get('task_id')
    print(f'[{name}] Submitted: {task_id}')
    
    for i in range(20):
        time.sleep(3)
        req2 = urllib.request.Request(f'http://127.0.0.1:18790/api/tasks/{task_id}')
        resp2 = urllib.request.urlopen(req2, timeout=5)
        task_data = json.loads(resp2.read().decode()).get('task', {})
        status = task_data.get('status', '?')
        persona = task_data.get('persona', '?')
        if status in ('done', 'error'):
            r = task_data.get('result') or task_data.get('error') or ''
            steps = task_data.get('steps', [])
            print(f'  [{name}] {status} (persona={persona}) steps={len(steps)}')
            print(f'  Result: {r[:150]}')
            break
        else:
            print(f'  [{name}] Poll {i+1}: {status}')
    print()
