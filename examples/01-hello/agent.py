"""
最简Agent示例 — Hello World

用法:
  cd examples/01-hello
  xuanji run
"""

from xuanji import AgentPlugin


class HelloAgent(AgentPlugin):
    name = "Hello"
    description = "最简单的Agent示例"
    
    async def on_message(self, msg, ctx):
        """收到消息就回复"""
        reply = await ctx.llm.chat([
            {"role": "user", "content": msg.content}
        ])
        await ctx.channels.reply(msg, reply)
    
    async def on_task(self, task, ctx):
        """收到任务就执行"""
        result = await ctx.llm.chat([
            {"role": "system", "content": "完成以下任务"},
            {"role": "user", "content": str(task)}
        ])
        return result
