"""FastAPI backend for LLM Council."""

from pathlib import Path
from fastapi import FastAPI, HTTPException, Depends, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Dict, Any
import secrets
import uuid
import json
import asyncio

from . import storage
from .council import run_full_council, generate_conversation_title, stage1_collect_responses, stage2_collect_rankings, stage3_synthesize_final, calculate_aggregate_rankings, parse_ranking_from_text
from .openrouter import query_model_stream
from .config import COUNCIL_MODELS, CHAIRMAN_MODEL
from .auth import AUTH_USER, AUTH_PASS, AUTH_ENABLED, create_session, validate_session, remove_session, require_auth

app = FastAPI(title="LLM Council API", dependencies=[Depends(require_auth)])

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
async def login(request: LoginRequest, response: Response):
    if (secrets.compare_digest(request.username, AUTH_USER) and
            secrets.compare_digest(request.password, AUTH_PASS)):
        token = create_session()
        response.set_cookie(
            key="session",
            value=token,
            httponly=True,
            max_age=86400,  # 24 hours
        )
        return {"status": "ok"}
    raise HTTPException(status_code=401, detail="Invalid credentials")


@app.get("/api/auth/status")
async def auth_status(request: Request):
    if not AUTH_ENABLED:
        return {"authenticated": True, "auth_enabled": False}
    token = request.cookies.get("session")
    return {"authenticated": bool(token and validate_session(token)), "auth_enabled": True}


@app.post("/api/auth/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get("session")
    if token:
        remove_session(token)
    response.delete_cookie("session")
    return {"status": "ok"}


class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""
    pass


class SendMessageRequest(BaseModel):
    """Request to send a message in a conversation."""
    content: str


class FollowUpRequest(BaseModel):
    """Request for a follow-up message with a specific model."""
    content: str
    model: str


class ConversationMetadata(BaseModel):
    """Conversation metadata for list view."""
    id: str
    created_at: str
    title: str
    message_count: int


class Conversation(BaseModel):
    """Full conversation with all messages."""
    id: str
    created_at: str
    title: str
    messages: List[Dict[str, Any]]


FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@app.get("/api/conversations", response_model=List[ConversationMetadata])
async def list_conversations():
    """List all conversations (metadata only)."""
    return storage.list_conversations()


@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(request: CreateConversationRequest):
    """Create a new conversation."""
    conversation_id = str(uuid.uuid4())
    conversation = storage.create_conversation(conversation_id)
    return conversation


@app.get("/api/conversations/{conversation_id}", response_model=Conversation)
async def get_conversation(conversation_id: str):
    """Get a specific conversation with all its messages."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@app.post("/api/conversations/{conversation_id}/message")
async def send_message(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and run the 3-stage council process.
    Returns the complete response with all stages.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    # Add user message
    storage.add_user_message(conversation_id, request.content)

    # If this is the first message, generate a title
    if is_first_message:
        title = await generate_conversation_title(request.content)
        storage.update_conversation_title(conversation_id, title)

    # Run the 3-stage council process
    stage1_results, stage2_results, stage3_result, metadata = await run_full_council(
        request.content
    )

    # Add assistant message with all stages
    storage.add_assistant_message(
        conversation_id,
        stage1_results,
        stage2_results,
        stage3_result
    )

    # Return the complete response with metadata
    return {
        "stage1": stage1_results,
        "stage2": stage2_results,
        "stage3": stage3_result,
        "metadata": metadata
    }


@app.post("/api/conversations/{conversation_id}/message/stream")
async def send_message_stream(conversation_id: str, request: SendMessageRequest):
    """
    Send a message and stream the 3-stage council process.
    Returns Server-Sent Events as each stage completes.
    """
    # Check if conversation exists
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Check if this is the first message
    is_first_message = len(conversation["messages"]) == 0

    async def stream_models_parallel(stage, models, messages):
        """Stream multiple models in parallel, yielding per-model token events."""
        event_queue = asyncio.Queue()
        model_contents = {model: "" for model in models}

        async def consume_model(model):
            try:
                async for event in query_model_stream(model, messages):
                    await event_queue.put((model, event))
            except Exception as e:
                await event_queue.put((model, {"type": "error", "error": str(e)}))

        tasks = [asyncio.create_task(consume_model(m)) for m in models]
        done_count = 0

        while done_count < len(models):
            # Use a timeout to send keepalives
            try:
                model, event = await asyncio.wait_for(event_queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue

            if event["type"] == "token":
                model_contents[model] += event["content"]
                yield f"data: {json.dumps({'type': f'{stage}_model_token', 'model': model, 'token': event['content']})}\n\n"
            elif event["type"] == "done":
                model_contents[model] = event["content"]
                done_count += 1
                yield f"data: {json.dumps({'type': f'{stage}_model_complete', 'model': model, 'content': event['content']})}\n\n"
            elif event["type"] == "error":
                done_count += 1
                yield f"data: {json.dumps({'type': f'{stage}_model_error', 'model': model, 'error': event['error']})}\n\n"

        # Cancel any remaining tasks
        for t in tasks:
            t.cancel()

    # Build context from prior conversation history for follow-up council rounds
    prior_context = ""
    if not is_first_message:
        parts = []
        for msg in conversation["messages"]:
            if msg["role"] == "user" and not msg.get("followup"):
                parts.append(f"User's original question: {msg['content']}")
            elif msg["role"] == "assistant" and not msg.get("followup"):
                if msg.get("stage3") and msg["stage3"].get("response"):
                    parts.append(f"Council's previous synthesized answer:\n{msg['stage3']['response']}")
            elif msg.get("followup"):
                role_label = "User" if msg["role"] == "user" else f"Assistant ({msg.get('model', 'unknown')})"
                parts.append(f"{role_label}: {msg.get('content', '')}")
        if parts:
            prior_context = "\n\n".join(parts) + "\n\n---\n\nNew follow-up question: "

    async def event_generator():
        try:
            # Add user message
            storage.add_user_message(conversation_id, request.content)

            # Start title generation in parallel
            title_task = None
            if is_first_message:
                title_task = asyncio.create_task(generate_conversation_title(request.content))

            # Stage 1: Stream individual responses
            yield f"data: {json.dumps({'type': 'stage1_start', 'models': COUNCIL_MODELS})}\n\n"
            query_content = prior_context + request.content if prior_context else request.content
            messages = [{"role": "user", "content": query_content}]
            stage1_contents = {}
            async for event in stream_models_parallel("stage1", COUNCIL_MODELS, messages):
                if isinstance(event, str):
                    yield event
                    if event.startswith("data: "):
                        try:
                            d = json.loads(event[6:])
                            if d.get("type") == "stage1_model_complete":
                                stage1_contents[d["model"]] = d["content"]
                                # Save each model's response as it arrives
                                partial = [{"model": m, "response": stage1_contents[m]} for m in COUNCIL_MODELS if m in stage1_contents]
                                storage.upsert_assistant_message(conversation_id, stage1=partial)
                        except (json.JSONDecodeError, KeyError):
                            pass

            stage1_results = [
                {"model": m, "response": stage1_contents[m]}
                for m in COUNCIL_MODELS if m in stage1_contents
            ]
            # Save stage 1 incrementally
            storage.upsert_assistant_message(conversation_id, stage1=stage1_results)
            yield f"data: {json.dumps({'type': 'stage1_complete'})}\n\n"

            if not stage1_results:
                yield f"data: {json.dumps({'type': 'error', 'message': 'All models failed to respond'})}\n\n"
                return

            # Stage 2: Stream rankings
            labels = [chr(65 + i) for i in range(len(stage1_results))]
            label_to_model = {
                f"Response {label}": result['model']
                for label, result in zip(labels, stage1_results)
            }
            responses_text = "\n\n".join([
                f"Response {label}:\n{result['response']}"
                for label, result in zip(labels, stage1_results)
            ])
            ranking_prompt = f"""You are evaluating different responses to the following question:

Question: {query_content}

Here are the responses from different models (anonymized):

{responses_text}

Your task:
1. First, evaluate each response individually. For each response, explain what it does well and what it does poorly.
2. Then, at the very end of your response, provide a final ranking.

IMPORTANT: Your final ranking MUST be formatted EXACTLY as follows:
- Start with the line "FINAL RANKING:" (all caps, with colon)
- Then list the responses from best to worst as a numbered list
- Each line should be: number, period, space, then ONLY the response label (e.g., "1. Response A")
- Do not add any other text or explanations in the ranking section

Example of the correct format for your ENTIRE response:

Response A provides good detail on X but misses Y...
Response B is accurate but lacks depth on Z...
Response C offers the most comprehensive answer...

FINAL RANKING:
1. Response C
2. Response A
3. Response B

Now provide your evaluation and ranking:"""

            yield f"data: {json.dumps({'type': 'stage2_start', 'models': COUNCIL_MODELS})}\n\n"
            ranking_messages = [{"role": "user", "content": ranking_prompt}]
            stage2_contents = {}
            async for event in stream_models_parallel("stage2", COUNCIL_MODELS, ranking_messages):
                if isinstance(event, str):
                    yield event
                    if event.startswith("data: "):
                        try:
                            d = json.loads(event[6:])
                            if d.get("type") == "stage2_model_complete":
                                stage2_contents[d["model"]] = d["content"]
                        except (json.JSONDecodeError, KeyError):
                            pass

            stage2_results = []
            for m in COUNCIL_MODELS:
                if m in stage2_contents:
                    full_text = stage2_contents[m]
                    parsed = parse_ranking_from_text(full_text)
                    stage2_results.append({
                        "model": m,
                        "ranking": full_text,
                        "parsed_ranking": parsed,
                    })

            aggregate_rankings = calculate_aggregate_rankings(stage2_results, label_to_model)
            # Save stage 2 incrementally
            storage.upsert_assistant_message(conversation_id, stage2=stage2_results)
            yield f"data: {json.dumps({'type': 'stage2_complete', 'metadata': {'label_to_model': label_to_model, 'aggregate_rankings': aggregate_rankings}})}\n\n"

            # Stage 3: Stream chairman synthesis
            stage1_text = "\n\n".join([
                f"Model: {r['model']}\nResponse: {r['response']}" for r in stage1_results
            ])
            stage2_text = "\n\n".join([
                f"Model: {r['model']}\nRanking: {r['ranking']}" for r in stage2_results
            ])
            chairman_prompt = f"""You are the Chairman of an LLM Council. Multiple AI models have provided responses to a user's question, and then ranked each other's responses.

Original Question: {query_content}

STAGE 1 - Individual Responses:
{stage1_text}

STAGE 2 - Peer Rankings:
{stage2_text}

Your task as Chairman is to synthesize all of this information into a single, comprehensive, accurate answer to the user's original question. Consider:
- The individual responses and their insights
- The peer rankings and what they reveal about response quality
- Any patterns of agreement or disagreement

Provide a clear, well-reasoned final answer that represents the council's collective wisdom:"""

            yield f"data: {json.dumps({'type': 'stage3_start', 'model': CHAIRMAN_MODEL})}\n\n"
            stage3_content = ""
            async for event in query_model_stream(CHAIRMAN_MODEL, [{"role": "user", "content": chairman_prompt}]):
                if event["type"] == "token":
                    stage3_content += event["content"]
                    yield f"data: {json.dumps({'type': 'stage3_token', 'token': event['content']})}\n\n"
                elif event["type"] == "done":
                    stage3_content = event["content"]
                elif event["type"] == "error":
                    yield f"data: {json.dumps({'type': 'error', 'message': event['error']})}\n\n"

            stage3_result = {"model": CHAIRMAN_MODEL, "response": stage3_content}
            # Save stage 3 incrementally
            storage.upsert_assistant_message(conversation_id, stage3=stage3_result)
            yield f"data: {json.dumps({'type': 'stage3_complete', 'data': stage3_result})}\n\n"

            # Wait for title generation
            if title_task:
                title = await title_task
                storage.update_conversation_title(conversation_id, title)
                yield f"data: {json.dumps({'type': 'title_complete', 'data': {'title': title}})}\n\n"

            yield f"data: {json.dumps({'type': 'complete'})}\n\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.post("/api/conversations/{conversation_id}/followup/stream")
async def followup_stream(conversation_id: str, request: FollowUpRequest):
    """Stream a 1-on-1 follow-up with a specific model."""
    conversation = storage.get_conversation(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Build conversation history for the model
    # Include: original question, chairman synthesis, then follow-up messages
    chat_messages = []
    original_question = None
    chairman_answer = None

    for msg in conversation["messages"]:
        if msg["role"] == "user" and not msg.get("followup"):
            original_question = msg["content"]
        elif msg["role"] == "assistant" and not msg.get("followup"):
            if msg.get("stage3") and msg["stage3"].get("response"):
                chairman_answer = msg["stage3"]["response"]
        elif msg.get("followup"):
            if msg["role"] == "user":
                chat_messages.append({"role": "user", "content": msg["content"]})
            elif msg["role"] == "assistant":
                chat_messages.append({"role": "assistant", "content": msg["content"]})

    # Build the system context
    system_content = f"""You are continuing a conversation after an LLM Council deliberation.

The user originally asked: {original_question}

The council's synthesized answer was:
{chairman_answer}

Continue the conversation naturally, building on the council's answer. Be helpful and direct."""

    messages_for_model = [
        {"role": "system", "content": system_content},
        *chat_messages,
        {"role": "user", "content": request.content},
    ]

    # Save user follow-up message
    storage.add_followup_message(conversation_id, "user", request.content)

    async def event_generator():
        try:
            full_content = ""
            # Create placeholder assistant message
            storage.add_followup_message(conversation_id, "assistant", "", model=request.model)

            async for event in query_model_stream(request.model, messages_for_model):
                if event["type"] == "token":
                    full_content += event["content"]
                    yield f"data: {json.dumps({'type': 'followup_token', 'token': event['content']})}\n\n"
                elif event["type"] == "done":
                    full_content = event["content"]
                elif event["type"] == "error":
                    yield f"data: {json.dumps({'type': 'error', 'message': event['error']})}\n\n"

            # Save final content
            storage.update_followup_message(conversation_id, full_content)
            yield f"data: {json.dumps({'type': 'followup_complete', 'content': full_content, 'model': request.model})}\n\n"

        except Exception as e:
            import traceback
            traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# Serve frontend static files if built
if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="static")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        """Serve the frontend SPA for any non-API route."""
        return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
