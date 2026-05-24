# -*- coding: utf-8 -*-
"""
XuanJi end-to-end test - full chain ignition

Tests the complete chain:
  Runtime startup -> Agent process -> Ollama inference -> Return result
"""

import asyncio
import sys
import time
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'python'))


async def create_ollama_router():
    """Create Ollama LLMRouter"""
    from xuanji.llm.router import LLMRouter
    from xuanji.llm.ollama import OllamaAdapter
    
    router = LLMRouter()
    adapter = OllamaAdapter('ollama', {'model': 'qwen3.5:9b'})
    await adapter.ping()  # scan available models
    router.register('ollama', adapter)
    router.set_primary('ollama')
    return router


async def test_ollama_direct():
    """Test 1: Ollama direct chat"""
    print("\n" + "="*60)
    print("Test 1: Ollama direct chat")
    print("="*60)
    
    router = await create_ollama_router()
    
    result = await router.chat([
        {'role': 'system', 'content': 'You are a test assistant'},
        {'role': 'user', 'content': 'Reply "XuanJi test passed", nothing else'}
    ])
    
    content = result.content if hasattr(result, 'content') else str(result)
    print(f"Ollama reply: {content[:100]}")
    
    assert 'passed' in content.lower() or 'test' in content.lower() or 'pass' in content.lower() or 'xuanji' in content.lower() or 'pass' in content.lower() or 'through' in content.lower() or 'tongguo' in content.lower() or 'tongguo' in content.lower(), "Reply missing expected content"
    print("PASS: Ollama direct chat")
    return True


async def test_agent_runner():
    """Test 2: AgentRunner ReAct loop"""
    print("\n" + "="*60)
    print("Test 2: AgentRunner ReAct loop")
    print("="*60)
    
    from xuanji.agent_runner import AgentRunner, ToolRegistry
    
    router = await create_ollama_router()
    
    registry = ToolRegistry()
    registry.register(
        name='get_time',
        description='Get current time',
        params={},
        func=lambda: time.strftime('%Y-%m-%d %H:%M:%S'),
        category='system'
    )
    
    runner = AgentRunner(
        llm_router=router,
        tool_registry=registry,
        max_steps=3,
        model='qwen3.5:9b'  # explicitly set model
    )
    
    result = await runner.run("What time is it?")
    
    print(f"Task success: {result.success}")
    print(f"Steps: {result.total_steps}")
    print(f"Elapsed: {result.elapsed:.1f}s")
    print(f"Answer: {result.answer[:100]}")
    
    assert result.success, "AgentRunner task failed"
    print("PASS: AgentRunner ReAct loop")
    return True


async def test_message_bus():
    """Test 3: Message bus communication"""
    print("\n" + "="*60)
    print("Test 3: Message bus communication")
    print("="*60)
    
    from xuanji.bus import MessageBus, Message, MsgType, Subscription
    
    bus = MessageBus(capacity=64, use_native=False)
    
    sub = Subscription(agent_id=2, callback=None)
    bus.subscribe(sub)
    
    msg = Message(
        from_agent=1,
        to_agent=2,
        msg_type=MsgType.CHAT,
        payload=b'hello from agent 1'
    )
    
    published = bus.publish(msg)
    print(f"Published: {published}")
    print(f"Backend: {bus.backend}")
    
    received = bus.receive(agent_id=2, timeout_ms=1000)
    print(f"Received: {received}")
    
    bus.close()
    
    assert published, "Publish failed"
    assert received is not None, "Message not received"
    print("PASS: Message bus")
    return True


async def test_resource_arbiter():
    """Test 4: Resource arbitration"""
    print("\n" + "="*60)
    print("Test 4: Resource arbitration")
    print("="*60)
    
    from xuanji.arbiter import ResourceArbiter, ResourceRequest, ResourceType, ResourcePriority
    
    arbiter = ResourceArbiter()
    
    req1 = ResourceRequest(
        agent_id=1,
        agent_name='test_agent_1',
        resource_type=ResourceType.SCREEN,
        resource_name='screen',
        priority=ResourcePriority.P2_ENGINEERING
    )
    
    lease1 = arbiter.request(req1)
    print(f"Agent 1 request screen: {'OK' if lease1 else 'queued'}")
    
    req2 = ResourceRequest(
        agent_id=2,
        agent_name='test_agent_2',
        resource_type=ResourceType.SCREEN,
        resource_name='screen',
        priority=ResourcePriority.P3_CREATIVE
    )
    
    lease2 = arbiter.request(req2)
    print(f"Agent 2 request screen: {'OK' if lease2 else 'queued'}")
    
    if lease1:
        arbiter.release(lease_id=lease1.lease_id)
        print(f"Agent 1 released screen")
    
    arbiter.stop()
    
    assert lease1 is not None, "Agent 1 should get screen"
    assert lease2 is None, "Agent 2 should queue"
    print("PASS: Resource arbitration")
    return True


async def test_memory():
    """Test 5: Memory system"""
    print("\n" + "="*60)
    print("Test 5: Memory system")
    print("="*60)
    
    from xuanji.memory import MemoryManager
    
    mgr = MemoryManager()
    
    ctx = await mgr.begin_task("Test memory storage")
    
    await mgr.remember("This is a test memory", importance=5)
    
    memories = await mgr.search("test")
    print(f"Found {len(memories)} memories")
    
    await mgr.end_task(ctx, result="Test completed")
    
    print("PASS: Memory system")
    return True


async def test_full_chain():
    """Test 6: Full end-to-end chain"""
    print("\n" + "="*60)
    print("Test 6: Full end-to-end chain")
    print("="*60)
    
    from xuanji.memory import MemoryManager
    from xuanji.arbiter import ResourceArbiter
    from xuanji.agent_runner import AgentRunner, ToolRegistry
    from xuanji.arbiter import ResourceRequest, ResourceType, ResourcePriority
    
    router = await create_ollama_router()
    memory = MemoryManager()
    arbiter = ResourceArbiter()
    
    registry = ToolRegistry()
    registry.register(
        name='get_time',
        description='Get current time',
        params={},
        func=lambda: time.strftime('%Y-%m-%d %H:%M:%S'),
        category='system'
    )
    
    runner = AgentRunner(
        llm_router=router,
        tool_registry=registry,
        max_steps=5,
        model='qwen3.5:9b'  # explicitly set model
    )
    
    print("Components initialized")
    
    ctx = await memory.begin_task("Ask about time")
    print(f"Task context: {ctx.task_id}")
    
    req = ResourceRequest(
        agent_id=1,
        agent_name='test',
        resource_type=ResourceType.SCREEN,
        resource_name='screen',
        priority=ResourcePriority.P2_ENGINEERING
    )
    grant = arbiter.request(req)
    print(f"Resource request: {'OK' if grant else 'queued'}")
    
    result = await runner.run("Hello, please briefly introduce yourself")
    print(f"Task execution: success={result.success}")
    print(f"Steps: {result.total_steps}")
    print(f"Answer: {result.answer[:150]}")
    
    if grant:
        arbiter.release(lease_id=grant.lease_id)
    
    await memory.end_task(ctx, result=result.answer[:500])
    
    arbiter.stop()
    
    assert result.success, "End-to-end chain failed"
    print("PASS: Full end-to-end chain")
    return True


async def main():
    """Run all tests"""
    print("="*60)
    print("XuanJi End-to-End Test")
    print("="*60)
    
    tests = [
        ('Ollama direct', test_ollama_direct),
        ('AgentRunner ReAct', test_agent_runner),
        ('Message bus', test_message_bus),
        ('Resource arbiter', test_resource_arbiter),
        ('Memory', test_memory),
        ('Full chain', test_full_chain),
    ]
    
    results = []
    for name, test_func in tests:
        start = time.time()
        try:
            success = await test_func()
            elapsed = time.time() - start
            results.append((name, 'PASS', f'{elapsed:.1f}s'))
        except Exception as e:
            elapsed = time.time() - start
            results.append((name, f'FAIL: {e}', f'{elapsed:.1f}s'))
    
    print("\n" + "="*60)
    print("Summary")
    print("="*60)
    
    passed = sum(1 for _, s, _ in results if s == 'PASS')
    total = len(results)
    
    for name, status, duration in results:
        icon = 'OK' if status == 'PASS' else 'XX'
        print(f"  [{icon}] {status}  {name}  ({duration})")
    
    print(f"\nTotal: {passed}/{total} passed")
    
    if passed == total:
        print("SUCCESS: XuanJi full chain ignition!")
    else:
        print("WARNING: Some tests failed")
    
    return passed == total


if __name__ == '__main__':
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
