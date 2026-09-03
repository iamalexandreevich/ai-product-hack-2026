import json

from fastapi import FastAPI, Request

app = FastAPI()


@app.post("/v1/chat/completions")
async def completions(request: Request) -> dict:
    body = await request.json()
    user = body["messages"][-1]["content"]
    if "lodahs" in user:
        out = {"decision": "D", "risk": "supply_chain", "reason": "lodahs looks like a typo of lodash", "suggest": "npm install lodash"}
    else:
        out = {"decision": "A", "risk": "none", "reason": "", "suggest": ""}
    return {"id": "fake", "choices": [{"message": {"role": "assistant", "content": json.dumps(out)}}]}
