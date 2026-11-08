from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/webhook/samsara")
async def samsara_webhook(request: Request) -> dict:
    # HMAC imzo tekshiruvi, alert parsing, dedup, routing va yuborish — 2-bosqichda qo'shiladi.
    await request.body()
    return {"ok": True, "stage": "pending_stage_2"}
