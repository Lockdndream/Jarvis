"""Quick smoke test of the real LLM provider against OpenRouter."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.supervisor.llm import LLMProvider, validate_free_only_model, ModelNotAllowedError


async def main():
    model = os.environ.get("JARVIS_LLM_MODEL", "")
    print(f"Model: {model}")
    try:
        validate_free_only_model(model)
        print("Free-only validation: PASSED")
    except ModelNotAllowedError as e:
        print(f"Free-only validation: FAILED - {e}")
        return

    provider = LLMProvider()
    if not provider.is_configured:
        print("Provider is not configured (no API key)")
        return

    print("Testing basic chat completion...")
    result = await provider.chat_completion(
        messages=[{"role": "user", "content": "Say hello in one word."}],
        tools=[{
            "type": "function",
            "function": {
                "name": "say_hello",
                "description": "Say hello",
                "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            },
        }],
        max_tokens=50,
        temperature=0.0,
    )

    print(f"Response content: {result.get('content', '(none)')[:200]}")
    tool_calls = result.get("tool_calls")
    if tool_calls:
        print(f"Tool calls: {len(tool_calls)}")
        for tc in tool_calls:
            args = tc["function"]["arguments"]
            print(f"  - {tc['function']['name']}({args})")
    else:
        print("Tool calls: (none)")

    if not tool_calls:
        print("\nTesting with explicit tool call request...")
        result2 = await provider.chat_completion(
            messages=[{"role": "user", "content": "Use the say_hello tool to greet Alice."}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "say_hello",
                    "description": "Say hello to someone",
                    "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                },
            }],
            max_tokens=100,
            temperature=0.0,
        )
        print(f"Response content: {result2.get('content', '(none)')[:200]}")
        tool_calls2 = result2.get("tool_calls")
        if tool_calls2:
            print(f"Tool calls: {len(tool_calls2)}")
            for tc in tool_calls2:
                args = tc["function"]["arguments"]
                print(f"  - {tc['function']['name']}({args})")
        else:
            print("Tool calls: (none)")
            print("\nNOTE: openrouter/free may not support tool calling.")
            print("This is expected for some free models.")


if __name__ == "__main__":
    asyncio.run(main())
