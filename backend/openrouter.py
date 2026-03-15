"""OpenRouter API client for making LLM requests."""

import json
import time
import httpx
from typing import List, Dict, Any, Optional, AsyncIterator
from .config import OPENROUTER_API_KEY, OPENROUTER_API_URL


async def query_model(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0
) -> Optional[Dict[str, Any]]:
    """
    Query a single model via OpenRouter API.

    Args:
        model: OpenRouter model identifier (e.g., "openai/gpt-4o")
        messages: List of message dicts with 'role' and 'content'
        timeout: Request timeout in seconds

    Returns:
        Response dict with 'content' and optional 'reasoning_details', or None if failed
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": messages,
    }

    start = time.time()
    print(f"[OpenRouter] >>> Sending request to {model}...")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                OPENROUTER_API_URL,
                headers=headers,
                json=payload
            )
            elapsed = time.time() - start
            print(f"[OpenRouter] <<< {model} responded: HTTP {response.status_code} ({elapsed:.1f}s)")

            if response.status_code != 200:
                print(f"[OpenRouter] !!! {model} error body: {response.text[:500]}")

            response.raise_for_status()

            data = response.json()
            message = data['choices'][0]['message']
            content = message.get('content', '')
            print(f"[OpenRouter] --- {model} content length: {len(content)} chars")

            return {
                'content': content,
                'reasoning_details': message.get('reasoning_details')
            }

    except Exception as e:
        elapsed = time.time() - start
        print(f"[OpenRouter] !!! Error querying {model} after {elapsed:.1f}s: {e}")
        return None


async def query_model_stream(
    model: str,
    messages: List[Dict[str, str]],
    timeout: float = 120.0,
    token_timeout: float = 15.0
) -> AsyncIterator[Dict[str, Any]]:
    """
    Query a model with streaming enabled. Yields events:
      {'type': 'token', 'content': '...'} for each token
      {'type': 'done', 'content': '...'} with full content when complete
      {'type': 'error', 'error': '...'} on failure

    token_timeout: max seconds to wait between tokens (or for first token).
    """
    import asyncio

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
    }

    start = time.time()
    print(f"[OpenRouter] >>> Streaming request to {model}...")
    full_content = ""

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST", OPENROUTER_API_URL, headers=headers, json=payload
            ) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    print(f"[OpenRouter] !!! {model} error: HTTP {response.status_code} {body[:500]}")
                    yield {"type": "error", "error": f"HTTP {response.status_code}"}
                    return

                line_iter = response.aiter_lines().__aiter__()
                while True:
                    try:
                        line = await asyncio.wait_for(line_iter.__anext__(), timeout=token_timeout)
                    except asyncio.TimeoutError:
                        elapsed = time.time() - start
                        print(f"[OpenRouter] !!! {model} token timeout after {elapsed:.1f}s (no data for {token_timeout}s)")
                        if full_content:
                            yield {"type": "done", "content": full_content}
                        else:
                            yield {"type": "error", "error": f"No response within {token_timeout}s"}
                        return
                    except StopAsyncIteration:
                        break

                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        delta = data.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content", "")
                        if token:
                            full_content += token
                            yield {"type": "token", "content": token}
                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue

        elapsed = time.time() - start
        print(f"[OpenRouter] <<< {model} stream complete ({elapsed:.1f}s, {len(full_content)} chars)")
        yield {"type": "done", "content": full_content}

    except Exception as e:
        elapsed = time.time() - start
        print(f"[OpenRouter] !!! Error streaming {model} after {elapsed:.1f}s: {e}")
        if full_content:
            yield {"type": "done", "content": full_content}
        else:
            yield {"type": "error", "error": str(e)}


async def query_models_parallel(
    models: List[str],
    messages: List[Dict[str, str]]
) -> Dict[str, Optional[Dict[str, Any]]]:
    """
    Query multiple models in parallel.

    Args:
        models: List of OpenRouter model identifiers
        messages: List of message dicts to send to each model

    Returns:
        Dict mapping model identifier to response dict (or None if failed)
    """
    import asyncio

    # Create tasks for all models
    tasks = [query_model(model, messages) for model in models]

    # Wait for all to complete
    responses = await asyncio.gather(*tasks)

    # Map models to their responses
    return {model: response for model, response in zip(models, responses)}
