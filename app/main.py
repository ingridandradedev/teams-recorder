import uuid
import threading
import json
from typing import Dict
from fastapi import FastAPI, Query
from fastapi.responses import StreamingResponse
from app.recorder import gravar_reuniao, gravar_reuniao_stream
import asyncio
import concurrent.futures

app = FastAPI(title="Teams Recorder API")

executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

# mapa de flags de parada
STOP_EVENTS: Dict[str, threading.Event] = {}

@app.get("/gravar", response_class=StreamingResponse)
async def iniciar_gravacao(url: str = Query(..., description="URL da reunião do Teams")):
    recording_id = str(uuid.uuid4())
    stop_event = threading.Event()
    STOP_EVENTS[recording_id] = stop_event

    def event_generator():
        # cada yield é um SSE: data: {...}\n\n
        for msg in gravar_reuniao_stream(url, stop_event):
            payload = {**msg, "recording_id": recording_id}
            yield f"data: {json.dumps(payload)}\n\n"
        STOP_EVENTS.pop(recording_id, None)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/stop/{recording_id}")
def stop_gravacao(recording_id: str):
    ev = STOP_EVENTS.get(recording_id)
    if not ev:
        return {"status": "not_found"}
    ev.set()
    return {"status": "stopping"}

@app.get("/health")
def health():
    return {"status": "ok"}
