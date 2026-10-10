
import os
import hmac
import json
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Prevent HTTP logs from exposing sensitive request URLs.
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI(title="SmartReply Backend")

VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "")
PAGE_ID = os.getenv("PAGE_ID", "")
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "")

REPLIES_FILE = Path(__file__).parent / "replies.json"


def load_replies():
    try:
        with open(REPLIES_FILE, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not load replies.json")
        raise RuntimeError("Please check replies.json")


REPLIES = load_replies()


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
        and challenge is not None
    ):
        logger.info("Webhook verification successful")
        return PlainTextResponse(challenge)

    raise HTTPException(status_code=403, detail="Verification failed")


async def get_client_name(sender_id: str):
    if not all([PAGE_ACCESS_TOKEN, GRAPH_API_VERSION]):
        return "Unknown"

    url = (
        f"https://graph.facebook.com/"
        f"{GRAPH_API_VERSION}/{sender_id}"
    )

    headers = {
        "Authorization": f"Bearer {PAGE_ACCESS_TOKEN}"
    }

    params = {"fields": "first_name,last_name"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                url,
                headers=headers,
                params=params,
            )

        if response.is_success:
            data = response.json()
            name = " ".join(
                filter(
                    None,
                    [data.get("first_name"), data.get("last_name")],
                )
            )
            return name or "Unknown"

        logger.warning(
            "Could not fetch client profile: HTTP %s",
            response.status_code,
        )

    except httpx.HTTPError:
        logger.warning("Client profile request failed")

    return "Unknown"




def get_auto_reply(message: str, client_name: str = "") -> str:
    text = " ".join(message.lower().strip().split())

    greetings = REPLIES.get("greetings", {})
    if text in greetings:
        reply = greetings[text]
        name = client_name.strip()

        if name and name != "Unknown":
            return reply.replace("{name}", name)

        return (
            reply.replace(", {name}", "")
            .replace("{name}, ", "")
            .replace("{name}", "")
        ).replace("  ", " ").strip()

    keywords = REPLIES.get("keywords", {})
    responses = REPLIES.get("responses", {})

    for response_key, keyword_list in keywords.items():
        for keyword in keyword_list:
            if keyword.lower() in text:
                return responses.get(response_key, REPLIES["default"])

    return REPLIES.get("default", "ধন্যবাদ! 😊")



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
                "Messenger API returned HTTP %s: %s",
                response.status_code,
                response.text[:500],
            )
            response.raise_for_status()

        logger.info("Reply sent successfully")


@app.post("/webhook")
async def receive_message(request: Request):
    try:
        data = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if data.get("object") != "page":
        raise HTTPException(
            status_code=404,
            detail="Unsupported webhook object",
        )

    for entry in data.get("entry", []):
        for event in entry.get("messaging", []):

            message_data = event.get("message", {})

            # IMPORTANT:
            # Ignore messages sent by the Page itself.
            # This prevents the bot from replying to its own replies.
            if message_data.get("is_echo"):
                logger.info("Ignoring Page echo event")
                continue

            sender_id = event.get("sender", {}).get("id")

            # Extra protection if the Page ID appears as sender.
            if sender_id and sender_id == PAGE_ID:
                logger.info("Ignoring message from the Page")
                continue

            message_text = message_data.get("text", "").strip()

            # Ignore non-text events, such as attachments.
            if not sender_id or not message_text:
                continue

            client_name = await get_client_name(sender_id)

            logger.info(
                "Customer: %s | Message: %s",
                client_name,
                message_text[:500],
            )

            reply = get_auto_reply(message_text, client_name)

            try:
                await send_reply(sender_id, reply)
            except httpx.HTTPError:
                logger.exception("Failed to send Messenger reply")

    return {"status": "ok"}
