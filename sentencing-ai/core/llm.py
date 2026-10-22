"""
core/llm.py — EVERY CALL TO OPENAI GOES THROUGH HERE.

Key handling (the assignment brief: the app must not contain your own key)
  * The user types their key into the sidebar (api_key.py). The page reads it
    from st.session_state and passes it to LLM(...).
  * A fresh OpenAI client is made for every call and closed afterwards.
  * The key is never written to os.environ, a Streamlit cache, a file, the
    call log or an error message (and LLM's printed form hides it).

What this module offers
  * LLM.structured(...)  — one AI call that must answer in a Pydantic shape
                           (OpenAI Responses API + Structured Outputs).
  * LLM.embed(...)       — embeddings for search queries (not counted as AI calls).
  * CallRecord           — one row of the run log: step, model, tokens, seconds.

Robustness (so a marker's key works without code changes)
  * If a model rejects an optional setting (reasoning effort, safety id, output
    limit), the call is retried once without it (or with a lower limit).
  * If the chosen model is not available to the key, the next model in
    config.FALLBACK_MODELS is tried, and the run log says which was used.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import numpy as np
import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from core import config


class LLMError(Exception):
    """A failed call, explained in plain words. `record` is its log row."""

    def __init__(self, message: str, record: "CallRecord | None" = None):
        super().__init__(message)
        self.record = record


@dataclass
class CallRecord:
    step: str                    # e.g. "Facts extraction"
    role: str                    # extraction | analysis | synthesis | audit | embedding
    model: str                   # the model that answered
    effort: str | None
    kind: str = "ai"             # "ai" (counts towards the 5-call minimum) or "embedding"
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    seconds: float = 0.0
    ok: bool = True
    note: str = ""               # e.g. "retried without reasoning", a fallback model, or the error


class _ModelUnavailable(Exception):
    pass


@dataclass
class LLM:
    api_key: str = field(repr=False)                      # never printed
    models: dict = field(default_factory=lambda: dict(config.DEFAULT_MODELS))
    efforts: dict = field(default_factory=lambda: dict(config.DEFAULT_EFFORT))
    safety_id: str = ""          # an anonymous per-session id OpenAI recommends for end-user apps
    http_client: object = field(default=None, repr=False)  # tests pass a fake HTTP transport here

    # ── the client (made fresh for each call) ───────────────────────────────
    def _client(self) -> OpenAI:
        if not self.api_key:
            raise LLMError("No OpenAI API key: enter yours in the sidebar and press “Update API Key”.")
        kwargs = dict(api_key=self.api_key, timeout=config.REQUEST_TIMEOUT_SECONDS,
                      max_retries=config.SDK_MAX_RETRIES)
        if self.http_client is not None:
            kwargs["http_client"] = self.http_client
        return OpenAI(**kwargs)

    def _close(self, client) -> None:
        """Close a client we created (not a transport the tests passed in)."""
        if self.http_client is None and client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

    # ── one structured AI call ──────────────────────────────────────────────
    def structured(self, step: str, role: str, instructions: str, input_text: str,
                   schema: type[BaseModel]) -> tuple[BaseModel, CallRecord]:
        """Ask the model for an answer shaped like `schema`. Returns
        (parsed answer, log row). Raises LLMError (with .record) on failure."""
        chosen = self.models.get(role) or config.DEFAULT_MODELS[role]
        effort = self.efforts.get(role, config.DEFAULT_EFFORT.get(role))
        candidates = [chosen] + [m for m in config.FALLBACK_MODELS if m != chosen]
        record = CallRecord(step=step, role=role, model=chosen, effort=effort)
        started = time.time()
        notes = []
        for model in candidates:
            record.model = model
            try:
                response = self._call(role, model, effort, instructions, input_text, schema, notes)
                break
            except _ModelUnavailable:
                notes.append(f"{model} not available to this key")
                continue
            except LLMError as exc:
                record.ok, record.seconds = False, round(time.time() - started, 1)
                record.note = "; ".join(notes + [str(exc)])
                exc.record = record
                raise
        else:
            record.ok, record.seconds = False, round(time.time() - started, 1)
            record.note = "; ".join(notes)
            raise LLMError(f"None of the models tried is available to this API key ({', '.join(candidates)}). "
                           "Choose a model your key can use in the sidebar.", record)
        if model != chosen:
            notes.append(f"used {model} instead")
        record.note = "; ".join(notes)
        record.seconds = round(time.time() - started, 1)
        usage = getattr(response, "usage", None)
        if usage is not None:
            record.input_tokens = getattr(usage, "input_tokens", 0) or 0
            record.output_tokens = getattr(usage, "output_tokens", 0) or 0
            details = getattr(usage, "output_tokens_details", None)
            record.reasoning_tokens = (getattr(details, "reasoning_tokens", 0) or 0) if details else 0

        if getattr(response, "status", "completed") == "incomplete":
            reason = getattr(getattr(response, "incomplete_details", None), "reason", "") or "unknown"
            record.ok, record.note = False, f"answer incomplete ({reason})"
            raise LLMError(f"The {model} answer was cut short ({reason}). Try a lower reasoning effort "
                           f"or another model in the sidebar.", record)
        try:
            parsed = response.output_parsed
        except ValidationError:
            parsed = None
        if parsed is None:
            refusal = _refusal_text(response)
            record.ok = False
            record.note = "refused" if refusal else "no structured answer"
            raise LLMError(f"{model} did not return a usable answer" + (f": {refusal}" if refusal else "."), record)
        return parsed, record

    def _call(self, role, model, effort, instructions, input_text, schema, notes):
        """One request, with the retries for optional settings. Raises
        _ModelUnavailable if the key cannot use this model."""
        kwargs = dict(model=model, instructions=instructions, input=input_text, text_format=schema,
                      max_output_tokens=config.MAX_OUTPUT_TOKENS.get(role, 16000),
                      store=False)                      # do not keep the case on OpenAI's servers
        if effort and effort != "default":
            kwargs["reasoning"] = {"effort": effort}
        if self.safety_id:
            kwargs["safety_identifier"] = self.safety_id
        client = None
        try:
            client = self._client()
            for _attempt in range(4):
                try:
                    return client.responses.parse(**kwargs)
                except openai.BadRequestError as exc:
                    text = str(exc).lower()
                    if "reasoning" in kwargs and ("reasoning" in text or "effort" in text):
                        kwargs.pop("reasoning")
                        notes.append("retried without reasoning effort")
                    elif "safety_identifier" in kwargs and "safety_identifier" in text:
                        kwargs.pop("safety_identifier")
                        notes.append("retried without safety id")
                    elif "max_output_tokens" in text and kwargs["max_output_tokens"] > 4000:
                        kwargs["max_output_tokens"] //= 2
                        notes.append(f"retried with max_output_tokens={kwargs['max_output_tokens']}")
                    elif "model" in text and ("does not exist" in text or "not found" in text
                                              or "not supported" in text):
                        raise _ModelUnavailable() from None
                    else:
                        raise
            raise LLMError(f"OpenAI kept refusing the request for {model}.")
        except (_ModelUnavailable, LLMError):
            raise
        except (openai.NotFoundError, openai.PermissionDeniedError) as exc:
            # Only a refusal about the model moves on to the next model; any other 403/404
            # (e.g. a key without the Responses permission, or an unsupported region) is shown as it is.
            if _about_the_model(exc, model):
                raise _ModelUnavailable() from None
            raise LLMError(_explain(exc, model)) from None
        except Exception as exc:  # noqa: BLE001 — translated into a plain message
            raise LLMError(_explain(exc, model)) from None
        finally:
            self._close(client)

    # ── embeddings for search queries ───────────────────────────────────────
    def embed(self, step: str, texts: list[str]) -> tuple[np.ndarray, CallRecord]:
        record = CallRecord(step=step, role="embedding", model=config.EMBEDDING_MODEL, effort=None, kind="embedding")
        started = time.time()
        client = None
        try:
            client = self._client()
            resp = client.embeddings.create(model=config.EMBEDDING_MODEL, input=texts,
                                            dimensions=config.EMBEDDING_DIMENSIONS)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            record.ok, record.seconds = False, round(time.time() - started, 1)
            record.note = _explain(exc, config.EMBEDDING_MODEL, endpoint="the embeddings endpoint")
            raise LLMError(record.note, record) from None
        finally:
            self._close(client)
        record.seconds = round(time.time() - started, 1)
        record.input_tokens = getattr(getattr(resp, "usage", None), "prompt_tokens", 0) or 0
        return np.array([d.embedding for d in resp.data], dtype=np.float32), record


def _refusal_text(response) -> str:
    for item in getattr(response, "output", []) or []:
        for part in getattr(item, "content", []) or []:
            if getattr(part, "type", "") == "refusal":
                return getattr(part, "refusal", "") or "refused"
    return ""


def _safe_message(exc: Exception) -> str:
    """OpenAI's own message, shortened, with anything that looks like a key removed."""
    body = getattr(exc, "body", None)
    text = ""
    if isinstance(body, dict):                       # the error object: {"message": …} or {"error": {"message": …}}
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        text = str(inner.get("message") or "")
    text = re.sub(r"sk-[A-Za-z0-9_*\-]+", "sk-…", text or str(getattr(exc, "message", "") or exc))[:300].strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def _about_the_model(exc: Exception, model: str) -> bool:
    """True if a 403/404 says this key cannot use this model."""
    code = (getattr(exc, "code", None) or "").lower()
    text = str(getattr(exc, "message", "") or exc).lower()
    return (code in ("model_not_found", "model_not_available") or model.lower() in text
            or ("model" in text and any(w in text for w in ("does not exist", "not found", "do not have access",
                                                              "does not have access", "not available"))))


def _explain(exc: Exception, model: str, endpoint: str = "the Responses API") -> str:
    """Turn an OpenAI library error into a sentence a user can act on.
    (Never includes the key.)"""
    if isinstance(exc, openai.AuthenticationError):
        return "OpenAI rejected the API key. Check it in the sidebar."
    if isinstance(exc, (openai.NotFoundError, openai.PermissionDeniedError)) and _about_the_model(exc, model):
        return (f"The model “{model}” is not available to this API key."
                + (" Choose another model in the sidebar." if endpoint == "the Responses API" else ""))
    if isinstance(exc, openai.PermissionDeniedError):
        return ("OpenAI refused the request (permission denied): "
                f"{_safe_message(exc)} Check that the key's project may use {endpoint} and the model {model}.")
    if isinstance(exc, openai.NotFoundError):
        return f"OpenAI could not find what was requested: {_safe_message(exc)}"
    if isinstance(exc, openai.RateLimitError):
        return ("OpenAI rate limit or quota reached for this key. Wait a minute, check the account's credit, or "
                "untick “Run the four analyses at the same time” in the sidebar.")
    if isinstance(exc, openai.APITimeoutError):
        return f"{model} took too long to answer. Try again, or lower the reasoning effort."
    if isinstance(exc, openai.APIConnectionError):
        return "Could not reach OpenAI. Check the connection and try again."
    if isinstance(exc, openai.BadRequestError):
        return f"OpenAI refused the request for {model}: {_safe_message(exc)}"
    if isinstance(exc, ValidationError):
        return f"{model} returned an answer in the wrong shape."
    return f"Unexpected error calling {model}: {type(exc).__name__}"
