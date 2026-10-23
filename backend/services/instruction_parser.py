"""Natural-language instructions → structured Playwright actions.

Primary path is Gemini; a deterministic regex parser handles the documented
numbered-instruction format as a fallback, so the pipeline still works
without an API key (and for simple scripts it's instant).
"""

import re

from core.storage import log_error
from models.flow import AuthConfig, ParsedAction
from services.gemini import extract_json, generate, has_gemini

PARSE_PROMPT = """You are controlling a web browser via Playwright automation.
Convert these natural language instructions into structured browser actions:
Instructions: {instructions}

Starting URL: {url}
Return ONLY a JSON array of actions. Each action has:

type: one of: navigate, click, type, press_key, wait, scroll, hover, screenshot
selector: CSS selector or text description of element (for click/type/hover)
For press_key, put the key name (e.g. Enter) in the text field.
For searches: emit a type action filling the search box, then press_key Enter.

Prefer text-based selectors like: text=Submit, role=button name=Login

Use CSS only if text selector won't work
text: text to type (for type actions only)
url: full URL (for navigate actions only)
duration: seconds (for wait actions only)
description: short human-readable label like "Click New Project button"

Do NOT emit login/sign-in steps — the user signs in by hand before automation
starts. Begin from the point where the product is already open and logged in.

Be specific with selectors. Prefer Playwright's text= and role= locators.
Return ONLY the JSON array. No markdown, no explanation."""

_VALID_TYPES = {"navigate", "click", "type", "press_key", "wait", "scroll", "hover", "screenshot"}

# Gemini is inconsistent about action verbs — map common synonyms onto the
# action types the recorder actually executes.
_TYPE_ALIASES = {
    "goto": "navigate", "go": "navigate", "open": "navigate", "visit": "navigate",
    "press": "click", "tap": "click", "select": "click", "submit": "click",
    "input": "type", "fill": "type", "enter": "type", "write": "type",
    "sleep": "wait", "pause": "wait", "delay": "wait",
    "keypress": "press_key", "press_enter": "press_key", "key": "press_key",
    "scroll_down": "scroll", "scrolldown": "scroll",
    "mouseover": "hover", "screen_shot": "screenshot", "capture": "screenshot",
}


def _normalize_type(raw_type: object) -> str | None:
    t = str(raw_type or "").strip().lower()
    if t in _VALID_TYPES:
        return t
    return _TYPE_ALIASES.get(t)


def _to_actions(raw: object) -> list[ParsedAction]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("Parser did not return a non-empty action array")
    actions = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action_type = _normalize_type(item.get("type"))
        if action_type is None:
            continue
        try:
            duration = float(item["duration"]) if item.get("duration") is not None else None
        except (TypeError, ValueError):
            duration = None
        actions.append(
            ParsedAction(
                type=action_type,
                selector=item.get("selector"),
                text=item.get("text"),
                url=item.get("url"),
                duration=duration,
                description=str(item.get("description") or action_type).strip()[:120],
            )
        )
    if not actions:
        raise ValueError("No valid actions in parser output")
    return actions


# ---- deterministic fallback parser ----

_LOGIN_RE = re.compile(
    r"log\s*in.*?(?:email|user(?:name)?)\s*:?\s*(\S+).*?password\s*:?\s*(\S+)", re.IGNORECASE
)
_NAVIGATE_RE = re.compile(r"(?:go to|navigate to|open|visit)\s+(https?://\S+|\S+\.\w{2,}\S*)", re.IGNORECASE)
# "open link" / "open the page" etc. — the starting URL is already navigated to.
_REDUNDANT_OPEN_RE = re.compile(r"^(?:open|go to|visit|load)(?:\s+the)?\s+(?:link|page|site|url|website)$", re.IGNORECASE)
# 'search "taj mahal"', 'search for X', or bare 'search taj mahal' —
# the query is everything after the verb, quotes stripped.
_SEARCH_RE = re.compile(r"search(?:\s+for)?\s+[\"“']?(.+?)[\"”']?$", re.IGNORECASE)
_CLICK_RE = re.compile(r"click(?:\s+(?:on|the))*\s+[\"“']?([^\"”']+?)[\"”']?(?:\s+(?:button|tab|link|menu|icon))?\s*$", re.IGNORECASE)
_TYPE_RE = re.compile(r"type\s+[\"“']?([^\"”']+)[\"”']?\s+(?:in|into)(?:\s+the)?\s+(.+?)(?:\s+field)?\s*$", re.IGNORECASE)
_WAIT_RE = re.compile(r"wait\s+([\d.]+)\s*(?:s|sec|second)", re.IGNORECASE)


def parse_fallback(instructions: str, url: str, auth: AuthConfig) -> list[ParsedAction]:
    """Parses the documented numbered format without an LLM."""
    actions: list[ParsedAction] = [
        ParsedAction(type="navigate", url=url, description=f"Open {url}")
    ]
    for raw_line in instructions.splitlines():
        line = re.sub(r"^\s*\d+[.)]\s*", "", raw_line).strip()
        if not line:
            continue

        if _REDUNDANT_OPEN_RE.match(line):
            continue  # already covered by the initial navigate action
        if m := _NAVIGATE_RE.search(line):
            target = m.group(1)
            if not target.startswith("http"):
                target = f"https://{target}"
            if target.rstrip("/") != url.rstrip("/"):
                actions.append(ParsedAction(type="navigate", url=target, description=f"Go to {target}"))
            continue
        if m := _LOGIN_RE.search(line):
            username, password = m.group(1), m.group(2)
            actions += [
                ParsedAction(type="type", selector="input[type=email], input[name*=user i], input[type=text]",
                             text=username, description="Enter username/email"),
                ParsedAction(type="type", selector="input[type=password]",
                             text=password, description="Enter password"),
                ParsedAction(type="click", selector="button[type=submit]",
                             description="Submit login"),
                ParsedAction(type="wait", duration=2.0, description="Wait for login to complete"),
            ]
            continue
        if m := _SEARCH_RE.search(line):
            query = m.group(1).strip()
            actions += [
                ParsedAction(type="type",
                             selector="input[type=search], [name=q], [role=searchbox]",
                             text=query, description=f"Search for “{query}”"),
                ParsedAction(type="press_key", text="Enter", description="Press Enter to search"),
            ]
            continue
        if m := _WAIT_RE.search(line):
            actions.append(ParsedAction(type="wait", duration=float(m.group(1)), description=f"Wait {m.group(1)}s"))
            continue
        if re.match(r"scroll", line, re.IGNORECASE):
            actions.append(ParsedAction(type="scroll", description="Scroll down"))
            continue
        if m := _TYPE_RE.search(line):
            actions.append(ParsedAction(type="type", selector=m.group(2).strip(), text=m.group(1).strip(),
                                        description=f"Type “{m.group(1).strip()}”"))
            continue
        if m := _CLICK_RE.search(line):
            target = m.group(1).strip()
            actions.append(ParsedAction(type="click", selector=target, description=f"Click “{target}”"))
            continue
        # Unrecognized line → best effort: treat the whole line as a click target description
        actions.append(ParsedAction(type="click", selector=line, description=f"Click “{line[:50]}”"))
    return actions


async def parse(instructions: str, url: str, auth: AuthConfig, flow_id: str = "global") -> list[ParsedAction]:
    if has_gemini():
        try:
            raw = extract_json(await generate(PARSE_PROMPT.format(instructions=instructions, url=url)))
            actions = _to_actions(raw)
            # Make sure the session starts somewhere
            if actions[0].type != "navigate":
                actions.insert(0, ParsedAction(type="navigate", url=url, description=f"Open {url}"))
            return actions
        except Exception as exc:
            log_error(flow_id, "instruction_parser: Gemini parse failed, using fallback parser", exc)
    return parse_fallback(instructions, url, auth)
