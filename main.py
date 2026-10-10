
import os
import hmac
import logging

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="SmartReply Backend")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
PAGE_ID = os.getenv("PAGE_ID", "")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "")


@app.get("/")
async def home():
    return {"status": "SmartReply backend is running"}


@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params

    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if (
        mode == "subscribe"
        and token
        and VERIFY_TOKEN
        and hmac.compare_digest(token, VERIFY_TOKEN)
        and challenge
    ):
        logger.info("Webhook verification successful")
        return PlainTextResponse(challenge)

    raise HTTPException(status_code=403, detail="Verification failed")


async def get_client_name(sender_id: str):
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{sender_id}"
    params = {
        "fields": "first_name,last_name",
        "access_token": PAGE_ACCESS_TOKEN,
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, params=params)

    if response.is_success:
        data = response.json()
        return " ".join(
            filter(None, [data.get("first_name"), data.get("last_name")])
        )

    logger.warning("Could not fetch client profile: %s", response.text[:300])
    return "Unknown"


# ========================

async def send_reply(recipient_id: str, message: str):
    if not all([PAGE_ID, PAGE_ACCESS_TOKEN, GRAPH_API_VERSION]):
        logger.error("Missing Messenger API configuration")
        return

    url = (
        f"https://graph.facebook.com/"
        f"{GRAPH_API_VERSION}/{PAGE_ID}/messages"
    )

    headers = {
        "Authorization": f"Bearer {PAGE_ACCESS_TOKEN}"
    }

    payload = {
        "recipient": {"id": recipient_id},
        "messaging_type": "RESPONSE",
        "message": {"text": message},
    }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            url,
            headers=headers,
            json=payload,
        )

        if response.is_error:
            logger.error(
                "Messenger API error: %s",
                response.text[:1000],
            )
            response.raise_for_status()

        logger.info("Reply sent successfully")


@app.post("/webhook")
async def receive_message(request: Request):
    data = await request.json()

    if data.get("object") != "page":
        raise HTTPException(
            status_code=404,
            detail="Unsupported webhook object",
        )

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):
            message_data = event.get("message", {})

            # Ignore replies sent by the Page itself.
            if message_data.get("is_echo"):
                continue

            sender_id = event.get("sender", {}).get("id")
            message_text = message_data.get("text", "").strip()
            
            # if message_text:
            #     logger.info("Client message from %s: %s", sender_id, message_text)


            if not sender_id or not message_text:
                continue

            client_name = await get_client_name(sender_id)

            logger.info(
                "\033[92mClient:\033[0m \033[96m%s\033[0m "
                "\033[92m| Message:\033[0m \033[93m%s\033[0m",
                client_name,
                message_text,
            )
            
               
                        
            
            
            # Ignore non-text events for this initial version.
            if not sender_id or not message_text:
                continue

            text_lower = message_text.lower()
            

            if text_lower in ["hi", "hello", "hey"]:
                reply = "Hello! Welcome to Free Star. How can we help you?"
            elif "price" in text_lower:
                reply = "Thanks for your interest! Please tell us which product you want to know about."
            else:
                reply = "Thank you for messaging Free Star. We will get back to you soon."

            try:
                await send_reply(sender_id, reply)
            except httpx.HTTPError:
                logger.exception("Failed to send Messenger reply")

    return {"status": "ok"}
