"""Anthropic-backed implementations of the AI roles. AI reads/classifies; code does the math."""
from __future__ import annotations
import base64, json, os
from decimal import Decimal
from . import prompts
from .matching import Match

DEFAULT_MODEL = "claude-sonnet-5-5"


class AIError(RuntimeError):
    pass


def _tool_input(resp, name: str) -> dict:
    if getattr(resp, "stop_reason", None) == "max_tokens":
        # A truncated extraction silently drops lines => wrong audit. Fail loudly instead.
        raise AIError("model output truncated (max_tokens); refusing partial result")
    for b in resp.content:
        if getattr(b, "type", None) == "tool_use" and b.name == name:
            return dict(b.input)
    raise AIError(f"model did not call tool {name}")


def _doc_block(file_bytes: bytes, media_type: str) -> dict:
    block = "document" if media_type == "application/pdf" else "image"
    return {"type": block, "source": {"type": "base64", "media_type": media_type,
                                      "data": base64.b64encode(file_bytes).decode()}}


class AnthropicAI:
    def __init__(self, client=None, model: str | None = None):
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client
        self.model = model or os.environ.get("AUDITBITE_MODEL", DEFAULT_MODEL)
        self.usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}   # for cost-per-invoice (spec section 9)

    def _call(self, system, tool, content, max_tokens=4000):
        resp = self.client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system, tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
            messages=[{"role": "user", "content": content}])
        u = getattr(resp, "usage", None)
        self.usage["calls"] += 1
        self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
        self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
        return _tool_input(resp, tool["name"])

    # --- Extractor ---
    def extract_invoice(self, file_bytes: bytes, media_type: str) -> dict:
        return self._call(prompts.EXTRACT_SYSTEM_PROMPT, prompts.INVOICE_TOOL,
                          [_doc_block(file_bytes, media_type), {"type": "text", "text": "Extract this invoice."}], 8000)

    def extract_contract(self, file_bytes: bytes, media_type: str) -> dict:
        return self._call(prompts.CONTRACT_SYSTEM_PROMPT, prompts.CONTRACT_TOOL,
                          [_doc_block(file_bytes, media_type), {"type": "text", "text": "Extract the pricing terms."}], 8000)

    # --- AIMatcher ---
    def match(self, lines: list[dict], candidates: dict[int, list[dict]]) -> dict[int, Match]:
        payload = [{"line_no": l["line_no"], "description": l.get("description"), "unit": l.get("unit"),
                    "pack_description": l.get("pack_description"), "candidates": candidates[l["line_no"]]} for l in lines]
        data = self._call(prompts.MATCH_SYSTEM_PROMPT, prompts.MATCH_TOOL,
                          [{"type": "text", "text": "<lines>" + json.dumps(payload) + "</lines>"}])
        out: dict[int, Match] = {}
        for m in data.get("matches", []):
            note = (m.get("reasoning") or "") + ((" | " + m["conversion_note"]) if m.get("conversion_note") else "")
            out[int(m["line_no"])] = Match(m.get("candidate_id"), m["match_type"], Decimal(str(m["confidence"])), note,
                                           bool(m.get("unit_conversion_needed")))
        return out

    # --- Classifier (prompt 3.4) ---
    def triage(self, facts: dict) -> dict:
        return self._call(prompts.TRIAGE_SYSTEM_PROMPT, prompts.TRIAGE_TOOL,
                          [{"type": "text", "text": "<facts>" + json.dumps(facts, default=str) + "</facts>"}], 1000)

    # --- Letter writer (prompt 3.6) ---
    def write_letter(self, payload: dict, language: str) -> dict:
        return self._call(prompts.CLAIM_LETTER_SYSTEM_PROMPT.replace("{language}", language), prompts.LETTER_TOOL,
                          [{"type": "text", "text": "<claim>" + json.dumps(payload, default=str) + "</claim>"}], 1500)
