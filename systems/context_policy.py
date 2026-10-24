"""Deterministic, conservative context/tool routing for Bemo v1.28.

Only choose optional capabilities when the user's request indicates a need;
fall back to the Main Brain's one small handoff tool for ambiguity. This module
never accesses private data, Ollama or the filesystem.
"""
from __future__ import annotations

import re

RECALL = re.compile(r"\b(remember|recall|last time|earlier|previously|we discussed|we talked|the other day|yesterday|from before|what did (i|we) (say|decide)|where did we leave off|our (previous|earlier|old) conversation)\b", re.I)
COMPUTER = re.compile(r"\b(open|launch|start|boot|execute|close|click|type|paste|scroll|move|copy|delete|rename|create|save|edit|run|terminal|shell|screenshot|install|download|upload|browser|firefox|chrome|desktop|window|application|process|file|folder|directory|search my|find my)\b", re.I)
FILE_DISCOVERY = re.compile(r"\b(find|locate|look for|search|where is|which file|list)\b.*\b(files?|folders?|images?|pictures?|photos?|documents?|pdfs?|paths?|scripts?|models?|cad|stl|step)\b", re.I)
PROJECT = re.compile(r"\b(project|workspace|repository|source files?|codebase|git|commit|diff)\b", re.I)
READ_ONLY_PROJECT_TOOLS = {"list_project_files", "read_project_file"}
PROJECT_FILE_QUESTION = re.compile(
    r"\b(?:files?|notes?|readme|documents?|docs?|text|lists?|contents?|written|wrote|says?|saved|folders?|"
    r"project)\b|\b[\w-]+\.[a-z0-9]{1,5}\b", re.I)
TECHNICAL = re.compile(r"\b(engineering|mechanical|electrical|thermal|torque|shaft|bearing|gear|sprocket|voltage|current|watt|amps?|solar|battery|circuit|heat transfer|stress|strain|dimensions?|tolerance|beam|fem|cad|step file|stl|datasheet|ohms?)\b", re.I)
TECHNICAL_TASK = re.compile(r"\b(calculate|compute|size|dimension|model|simulate|derive|analy[sz]e|inspect|measure|solve|check|design|determine|evaluate|validate)\b", re.I)
CODING = re.compile(r"\b(refactor|debug|implement|patch|fix (this|the) (code|script|program|bug)|edit (the|my) code|write (a|the) (script|function|program)|modify (the|my) (source|code)|codebase|repository|unit tests?)\b", re.I)
# App controls by name (milestone 3 gui tool): menus, tabs, buttons, settings
# inside an app, typing into one, or closing it without saving.
GUI_TASK = re.compile(r"\b(?:click|press|menu|tab|button|check ?box|toggle|turn (?:on|off)|switch (?:on|off)|"
                      r"enable|disable|hidden files|preferences|type .{1,120}\b(?:in|into)\b|without saving|"
                      r"(?:in|on) (?:the )?(?:thunar|text editor|calculator|volume control|settings))\b", re.I)
# A part to make or change (make_cad_model). Live 2026-10-20: "make a 20mm cube with a 5mm hole through the
# middle" got only the math tools, so Engineering had no way to make the STL.
MAKE_3D = re.compile(r"\b(?:3d|stl|scad|openscad|printable|3d.print\w*|cube|block|box|bracket|cylinder|sphere|"
                     r"spacer|washer|enclosure|plate|hook|knob|holder|stand|mount|hole|lid|container|cup|bin|tray|"
                     r"divider|standoff|clip|clamp|sign|name ?tag|tag)\b", re.I)
IMAGE = re.compile(r"\b(image|photo|picture|screenshot|diagram|drawing|schematic|visual|see|look at)\b", re.I)

# Requests that must produce real Supervisor evidence before Bemo may say the
# program/app/window actually ran or opened. Capability questions are excluded.
_EXECUTION_VERB = re.compile(r"\b(run|launch|start|boot|execute|open)\b", re.I)
_EXECUTION_OBJECT = re.compile(r"\b(program|script|app|application|clock|window|file|tool|game|terminal|browser|it|that|this)\b|\.py\b", re.I)
_CAPABILITY_ONLY = re.compile(r"^\s*(?:can|could|are|would)\s+you\s+(?:ever\s+)?(?:run|launch|start|boot|execute|open)\s+(?:programs?|apps?|files?)\s*[?.!]*$", re.I)
_CODE_CONTEXT = re.compile(r"\b(python|script|program|code|coding|software|function|tkinter|repository|source file|animated clock)\b|\.py\b", re.I)
_CODE_CONTINUATION = re.compile(r"\b(make|change|modify|edit|update|add|remove|animate|animated|display|show|turn|convert|rewrite|fix)\b", re.I)
_CODE_REFERENT = re.compile(r"\b(it|that|this|program|script|code|clock|app|window|file)\b", re.I)

def requires_verified_computer_action(query: str) -> bool:
    q = str(query or "")
    if _CAPABILITY_ONLY.search(q):
        return False
    return bool(_EXECUTION_VERB.search(q) and _EXECUTION_OBJECT.search(q))

def coding_continuation(query: str, recent_context: str = "") -> bool:
    q = str(query or "")
    ctx = str(recent_context or "")
    return bool(_CODE_CONTEXT.search(ctx) and _CODE_CONTINUATION.search(q) and _CODE_REFERENT.search(q))


def needs_archive_memory(query: str) -> bool:
    return bool(RECALL.search(query or ""))


def tool_names_for_request(query: str, profile: str, project_active: bool = False) -> set[str]:
    """Return only relevant tool names. Tools never disappear from implementation."""
    q = str(query or "")
    names: set[str] = set()
    # Normal speech and factual questions do not need routing/tool schemas.
    # High-confidence specialist requests are routed before inference;
    # ambiguous but plausible specialist tasks can still ask the Supervisor.
    if profile == "normal" and handoff_potential(q):
        names.add("request_capability_handoff")
    # On-demand local tools remain reachable for explicit files, screenshots,
    # attached documents, computer tasks and active projects. Generic factual
    # discussions (e.g. laser regulations) do not request a local tool broker.
    local_resource = re.search(
        r"\b(?:attached|local file|local document|on (?:my|the) computer|"
        r"read (?:this|the|my) (?:pdf|document|file|image|photo)|"
        r"my (?:pdf|document|screenshot|photo))\b|"
        r"(?:[/~][\w./ -]+\.(?:pdf|png|jpe?g|txt|csv|step|stl|py))\b",
        q, re.I,
    )
    if COMPUTER.search(q) or FILE_DISCOVERY.search(q) or local_resource or (project_active and PROJECT.search(q)):
        names.add("request_local_tools")
    if FILE_DISCOVERY.search(q):
        names.add("find_files")
    if COMPUTER.search(q):
        names.add("computer_control")
    if GUI_TASK.search(q):
        names.add("gui")
    if IMAGE.search(q) and (FILE_DISCOVERY.search(q) or COMPUTER.search(q) or project_active):
        names.add("select_image")
    if project_active and (PROJECT.search(q) or profile == "coding"):
        names.update({"get_project_facts", "search_project_assets", "set_project_fact", "supersede_project_fact", "select_project_image"})
        names.add("checkpoint_task")
    if project_active and profile != "coding" and PROJECT_FILE_QUESTION.search(q):
        # The other brains can list and read project files (2026-10-23); writing stays with Coding.
        names.update(READ_ONLY_PROJECT_TOOLS)
    if profile == "coding":
        # Coding still needs to access user files if no Git project is active;
        # only project-only editing functions require an active workspace.
        # The set stays identical every turn: tool schemas sit at the start of
        # the prompt, so changing them discarded Ollama's prompt cache (23 s
        # re-prefill in the v1.28.8 live test). Chat vs work is decided by
        # coding_work_request() instead.
        names.update({"computer_control", "find_files"})
        names.update({"list_project_files", "read_project_file", "search_project", "write_project_file", "replace_project_text", "show_project_diff", "run_project_check"})
    if profile == "engineering":
        names.update({"calculate_python", "solve_math", "convert_units"})
        if TECHNICAL.search(q):
            names.update({"mechanical_calculator", "electrical_calculator", "thermal_calculator"})
        if FILE_DISCOVERY.search(q) or re.search(r"\b(?:cad|stl|mesh|step file|pdf|spreadsheet)\b|\.(?:step|stp|stl|obj|csv|xlsx|pdf)\b", q, re.I):
            names.update({"inspect_cad", "inspect_engineering_file"})
        if MAKE_3D.search(q):
            names.update({"make_cad_part", "make_cad_model", "inspect_engineering_file"})
        if re.search(r"\b(plot|graph|chart)\b", q, re.I):
            names.add("plot_engineering_data")
        if re.search(r"\b(lookup|datasheet|reference|specification|research|search online)\b", q, re.I):
            names.update({"lookup_technical_reference", "technical_reference_library"})
        if re.search(r"\b(temperature sensor|instrument|data log|record measurements)\b", q, re.I):
            names.update({"instrument_telemetry", "data_logger"})
        if re.search(r"\b(cpu|ram|temperature|sensor|benchmark|hardware diagnostic)\b", q, re.I):
            names.add("hardware_diagnostics")
    if profile == "deep-vision":
        names.update({"crop_zoom_image", "ocr_image", "select_image"})
    return names


# "can you program a Python game that is like a snake game?" went to Main, which claimed to have
# written it (live 2026-10-19). A request to write a program goes to Coding (the owner still says yes).
PROGRAM_REQUEST = re.compile(
    r"\b(?:write|make|build|create|program|code|develop|generate)\b(?:\s+\w+){0,6}?\s+"
    r"(?:(?:a|an|the|me|some)\s+)?(?:[\w+#-]+\s+){0,3}"
    r"(?:python|javascript|typescript|rust|java|c\+\+|bash|program|script|game(?!\s+(?:plan|night|day|show))|app|function)\b",
    re.I)


def direct_specialist_route(query: str, profile: str, images_selected: bool = False, recent_context: str = "") -> str | None:
    """Route only high-confidence specialist requests, otherwise ask Main Brain.

    Explicit delegation and unmistakable technical tasks are routed in Python,
    before spending one full Main Brain inference turn to ask for a handoff.
    """
    if profile != "normal":
        return None
    q = str(query or "")
    if images_selected:
        return "DEEP_TECHNICAL_VISION"  # the one vision brain since 2026-10-24
    if re.search(r"\b(?:ask|use|switch to|delegate to|hand (?:this|it) to|have)\s+(?:the\s+)?(?:engineering|engineer|deep reasoning)\s+(?:model|brain|specialist)?\b", q, re.I):
        return "ENGINEERING"
    if re.search(r"\b(?:ask|use|switch to|delegate to|hand (?:this|it) to|have)\s+(?:the\s+)?(?:coding|coder|heavy coding)\s+(?:model|brain|specialist)?\b", q, re.I):
        return "HEAVY_CODING"
    if re.search(r"\b(?:ask|use|switch to|delegate to|hand (?:this|it) to|have)\s+(?:the\s+)?(?:deep technical vision|fast vision|vision)\s+(?:model|brain|specialist)?\b", q, re.I):
        return "DEEP_TECHNICAL_VISION"
    if CODING.search(q) and re.search(r"\b(code|python|script|program|repository|source|bug|project|function|test)\b", q, re.I):
        return "HEAVY_CODING"
    if PROGRAM_REQUEST.search(q) and not re.search(r"\b(?:story|poem|song|lyrics|essay|letter|joke|plan)\b", q, re.I):
        return "HEAVY_CODING"
    if coding_continuation(q, recent_context):
        return "HEAVY_CODING"
    if (TECHNICAL_TASK.search(q) and TECHNICAL.search(q)) or re.search(r"\b(check|diagnose|inspect)\b.*\b(cpu|hardware|temperature|ram)\b", q, re.I):
        return "ENGINEERING"
    return None


def compact_history(messages: list[dict], *, max_messages: int = 32, char_budget: int = 7500, batch: int = 8) -> int:
    """Trim whole old message batches only when needed, not every turn.

    Caller mutates the list and its parallel ID list together using returned
    count. Full raw history stays persisted in SQLite.
    """
    removed = 0
    while len(messages) > max_messages or sum(len(str(m.get('content', ''))) for m in messages) > char_budget:
        if len(messages) <= 2:
            break
        drop = min(max(2, batch), len(messages) - 2)
        del messages[:drop]
        removed += drop
    return removed


# A model's own explanation is never evidence for routing. Only the original
# user request and actual selected assets can authorize a model switch.
_HARDWARE_TASK = re.compile(r"\b(diagnos(?:e|is|tic)|benchmark|check|test|inspect|measure|troubleshoot)\b.*\b(cpu|gpu|ram|sensor|hardware|computer|pc|temperature|throttl\w*)\b", re.I)
_DESIGN_TASK = re.compile(r"\b(design|build|calculate|compute|size|dimension|solve|analy[sz]e|debug|troubleshoot|optimi[sz]e|simulate|derive|verify|inspect|measure|model|evaluate|diagnos\w*)\b", re.I)
_ENGINEERING_DOMAIN = re.compile(r"\b(electrical|mechanical|thermal|structural|torque|bearing|shaft|gear|sprocket|transmission|jackshaft|converter|motor|battery|solar|inverter|circuit|voltage|amperage|watts?|rpm|temperature|stress|beam|cad|stl|step file|engineering|physics|fluid|load|pressure|current)\b", re.I)
_CODING_TASK = re.compile(r"\b(write|create|modify|implement|refactor|fix|debug|review|test|patch|optimi[sz]e|develop|build|edit)\b.*\b(code|python|script|program|function|repository|software|module|source|bug|app|tests?)\b", re.I)
_CODING_TASK_BACK = re.compile(r"\b(code|python|script|program|function|repository|software|module|source|bug|app)\b.*\b(write|modify|implement|refactor|fix|debug|review|test|patch|optimi[sz]e|develop|build|edit)\b", re.I)
_EXPLICIT_SPECIALIST = {
    "ENGINEERING": re.compile(r"\b(?:ask|use|switch to|delegate to|hand (?:this|it) to|have)\s+(?:the\s+)?(?:engineering|engineer|deep reasoning)\s*(?:model|brain|specialist)?\b", re.I),
    "HEAVY_CODING": re.compile(r"\b(?:ask|use|switch to|delegate to|hand (?:this|it) to|have)\s+(?:the\s+)?(?:coding|coder|heavy coding)\s*(?:model|brain|specialist)?\b", re.I),
    "DEEP_TECHNICAL_VISION": re.compile(r"\b(?:ask|use|switch to|delegate to|hand (?:this|it) to|have)\s+(?:the\s+)?(?:deep technical vision|deep vision|fast vision|vision)\s*(?:model|brain|specialist)?\b", re.I),
}

def needs_engineering(query: str) -> bool:
    q = str(query or '')
    return bool(_EXPLICIT_SPECIALIST['ENGINEERING'].search(q) or _HARDWARE_TASK.search(q) or (_DESIGN_TASK.search(q) and _ENGINEERING_DOMAIN.search(q)))

def needs_coding(query: str, recent_context: str = "") -> bool:
    q = str(query or '')
    return bool(
        _EXPLICIT_SPECIALIST['HEAVY_CODING'].search(q)
        or _CODING_TASK.search(q)
        or _CODING_TASK_BACK.search(q)
        or (CODING.search(q) and re.search(r"\b(code|python|script|program|source|repository|bug|function)\b", q, re.I))
        or coding_continuation(q, recent_context)
    )

def coding_work_request(query: str) -> bool:
    """True when a Coding-profile turn asks for real work rather than conversation."""
    q = str(query or '')
    return bool(
        needs_coding(q) or COMPUTER.search(q) or FILE_DISCOVERY.search(q) or PROJECT.search(q)
        or re.search(r"\b(write|make|build|generate|show me)\b.*\b(code|script|program|function|class|app|game)\b", q, re.I)
        # A misheard verb still asks for a program: live 2026-10-19 "Bright a Python Program for a
        # Snake Game." was answered as chat ("I've created a complete Snake game"), with no file.
        or re.search(r"^(?!\W*(?:where|what|did|was|is|are|does|do|why|how|can|could)\b)"
                     r"\W*\w+\s+(?:a|an)\s+(?:[\w+#-]+\s+){0,3}(?:program|script|game|app)\b", q, re.I)
    )


def handoff_potential(query: str) -> bool:
    q = str(query or '')
    return bool(needs_engineering(q) or needs_coding(q) or any(regex.search(q) for regex in _EXPLICIT_SPECIALIST.values()) or (IMAGE.search(q) and re.search(r"\b(analy[sz]e|inspect|describe|read|examine)\b", q, re.I)))

def authorize_route(query: str, route: str, *, current_profile: str = 'normal', images_selected: bool = False, hops: int = 0, recent_context: str = '') -> tuple[bool, str]:
    """Single gate for ALL LLM-initiated handoffs (tool and ROUTE text).

    Ignore model-generated reasons; they are untrusted suggestions. A separate
    deterministic command may explicitly switch profiles outside this path.
    """
    q = str(query or '')
    label = str(route or '').strip().upper()
    if current_profile != 'normal' or hops >= 1:
        return False, 'Specialists may not chain additional model switches during the same user turn.'
    if label == 'ENGINEERING':
        return (True, 'engineering task') if needs_engineering(q) else (False, 'User did not request engineering analysis or a calculation.')
    if label == 'HEAVY_CODING':
        return (True, 'coding task') if needs_coding(q, recent_context) else (False, 'User did not request software editing or substantial coding.')
    if label in {'FAST_VISION', 'DEEP_TECHNICAL_VISION'}:
        if not images_selected:
            return False, 'No image is attached or selected for visual analysis.'
        return True, 'actual image is selected'
    return False, f'Unknown or unsupported specialist route: {label}'
