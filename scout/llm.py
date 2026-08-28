"""Thin Responses-API client, modeled on gps-grok-leadfinder/scout/llm.py."""
from __future__ import annotations

import json
import re
import time
import urllib.error
from urllib.request import Request, urlopen

import config

RETRY_CODES = {429, 500, 502, 503, 504}
BACKOFF = (10, 30)


def call(model, prompt, tools=(), text_format=None, with_usage=False):
    body = {
        "model": model,
        "input": prompt,
        "tools": list(tools),
        "store": False,
    }
    if text_format == "json_object":
        body["text"] = {"format": {"type": "json_object"}}
    headers = {
        "Authorization": f"Bearer {config.API_KEY}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode()

    for attempt in range(len(BACKOFF) + 1):
        request = Request(config.BASE_URL.rstrip("/") + "/responses", data, headers)
        try:
            with urlopen(request, timeout=600) as response:
                response = json.load(response)
            text = _output_text(response)
            return (text, response.get("usage", {})) if with_usage else text
        except urllib.error.HTTPError as e:
            if e.code not in RETRY_CODES or attempt == len(BACKOFF):
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == len(BACKOFF):
                raise
        time.sleep(BACKOFF[attempt])


def _output_text(response):
    return next(
        (
            part["text"]
            for item in response.get("output", [])
            if item.get("type") == "message"
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        ),
        "",
    )


def parse_json(text):
    """Grok wraps JSON in prose/fences and citation markers."""
    text = re.sub(r"<<ccr:[^>]+>>", "", text)
    match = re.search(r"\[.*\]|\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None
