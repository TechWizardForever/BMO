#!/usr/bin/python3
"""Linux accessibility (AT-SPI) helper for BMO's GUI controller.

Runs under the *system* Python, which has pyatspi; BMO's venv does not.
Reads one JSON request on stdin, writes one JSON reply on stdout:

  {"cmd": "windows"}
  {"cmd": "snapshot", "pid": 1234, "limit": 400}
  {"cmd": "act", "pid": 1234, "path": "0/2/5", "expect": {"role": "push button", "name": "7"},
   "action": "click" | "press" | "set_text" | "append_text" | "set_value", "value": ...}

Controls are addressed by their child-index path from the application root.
'expect' guards against acting on a different control if the UI changed
since the snapshot. Nothing here decides whether an action is allowed; the
Supervisor (systems/computer.py + systems/gui_control.py) does that first.
"""
import json
import sys
import time

import pyatspi

SELECTABLE_ROLES = {"page tab", "table cell", "list item", "tree item", "icon"}


def _parent_selection(node):
    try:
        return node.parent.querySelection()
    except (NotImplementedError, AttributeError, Exception):
        return None


TEXT_ROLES = {"text", "entry", "label", "paragraph", "terminal", "document text", "editbar", "heading"}
STATES = {
    "checked": pyatspi.STATE_CHECKED, "selected": pyatspi.STATE_SELECTED,
    "focused": pyatspi.STATE_FOCUSED, "expanded": pyatspi.STATE_EXPANDED,
    "editable": pyatspi.STATE_EDITABLE, "enabled": pyatspi.STATE_SENSITIVE,
    "active": pyatspi.STATE_ACTIVE,
}


def _apps():
    desk = pyatspi.Registry.getDesktop(0)
    for i in range(desk.childCount):
        app = desk.getChildAtIndex(i)
        if app is not None:
            yield app


def _app_by_pid(pid):
    for app in _apps():
        try:
            if app.get_process_id() == pid:
                return app
        except Exception:
            continue
    return None


def _text(node, limit=200):
    try:
        t = node.queryText()
        return t.getText(0, min(limit, t.characterCount))
    except (NotImplementedError, Exception):
        return None


def _label(node):
    """A name for unnamed controls: labelled-by relation, then description."""
    try:
        for rel in node.getRelationSet():
            if rel.getRelationType() == pyatspi.RELATION_LABELLED_BY and rel.getNTargets():
                name = (rel.getTarget(0).name or "").strip() or (_text(rel.getTarget(0)) or "").strip()
                if name:
                    return name
    except Exception:
        pass
    return (node.description or "").strip() or None


def _describe(node, path):
    role = node.getRoleName()
    st = node.getState()
    item = {"path": path, "role": role, "name": (node.name or "").strip()}
    if not item["name"]:
        label = _label(node)
        if label:
            item["label"] = label
    item["states"] = sorted(k for k, v in STATES.items() if st.contains(v))
    try:
        act = node.queryAction()
        item["actions"] = [act.getName(i) for i in range(act.nActions)]
    except NotImplementedError:
        item["actions"] = []
    if role in SELECTABLE_ROLES and _parent_selection(node) is not None:
        item["actions"].append("select")  # GTK tabs/cells switch via the parent's Selection
    if role in TEXT_ROLES or "editable" in item["states"]:
        text = _text(node)
        if text is not None:
            item["text"] = text
    try:
        v = node.queryValue()
        item["value"] = {"current": v.currentValue, "min": v.minimumValue, "max": v.maximumValue}
    except NotImplementedError:
        pass
    try:
        e = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        item["bbox"] = [e.x, e.y, e.width, e.height]
    except Exception:
        pass
    return item


def _visible(node):
    st = node.getState()
    return st.contains(pyatspi.STATE_SHOWING) and st.contains(pyatspi.STATE_VISIBLE)


def snapshot(pid, limit=400):
    app = _app_by_pid(pid)
    if app is None:
        return {"ok": False, "error": f"no accessible application with pid {pid}"}
    out = []

    def walk(node, path, depth):
        if node is None or depth > 45 or len(out) >= limit:
            return
        try:
            if path and not _visible(node):
                return  # hidden subtrees (closed menus, other tabs) are not actionable
            item = _describe(node, path)
            useful = item["actions"] or "text" in item or "value" in item or item["role"] in ("frame", "dialog", "page tab")
            if path and useful and (item["name"] or item.get("label") or "text" in item or item["actions"]):
                out.append(item)
            for i in range(node.childCount):
                walk(node.getChildAtIndex(i), f"{path}/{i}" if path else str(i), depth + 1)
        except Exception:
            return

    for i in range(app.childCount):
        walk(app.getChildAtIndex(i), str(i), 1)
    return {"ok": True, "app": app.name, "pid": pid, "controls": out, "truncated": len(out) >= limit}


def _resolve(app, path):
    node = app
    for part in path.split("/"):
        node = node.getChildAtIndex(int(part))
        if node is None:
            return None
    return node


def act(pid, path, action, expect=None, value=None):
    app = _app_by_pid(pid)
    if app is None:
        return {"ok": False, "error": f"no accessible application with pid {pid}"}
    node = _resolve(app, path)
    if node is None:
        return {"ok": False, "error": "that control no longer exists"}
    before = _describe(node, path)
    for key in ("role", "name"):
        if expect and expect.get(key) is not None and before.get(key) != expect[key]:
            return {"ok": False, "error": f"the control changed since I looked ({key} is now {before.get(key)!r})"}
    if before["role"] == "password text":
        return {"ok": False, "error": "BMO never types into password fields"}
    if action == "set_text":
        try:
            node.queryEditableText().setTextContents(str(value))
        except NotImplementedError:
            return {"ok": False, "error": "that control does not accept text"}
    elif action == "append_text":
        # Adds at the end and keeps what is there (set_text replaces the whole field: a document would
        # be wiped). Live 2026-10-19: "open the text editor and type ..." needed this.
        try:
            text_iface = node.queryText()
            end = text_iface.characterCount
            node.queryEditableText().insertText(end, str(value), len(str(value)))
            appended = text_iface.getText(end, end + len(str(value))) == str(value)
        except NotImplementedError:
            return {"ok": False, "error": "that control does not accept text"}
    elif action == "set_value":
        try:
            node.queryValue().currentValue = float(value)
        except NotImplementedError:
            return {"ok": False, "error": "that control has no value to set"}
    elif action == "select":
        sel = _parent_selection(node)
        if sel is None or not sel.selectChild(node.getIndexInParent()):
            return {"ok": False, "error": "that item could not be selected"}
    else:
        names = before["actions"]
        if action not in names:
            return {"ok": False, "error": f"that control cannot {action} (it can: {', '.join(names) or 'nothing'})"}
        node.queryAction().doAction(names.index(action))
    time.sleep(0.4)  # let the toolkit update state before reading it back
    after_node = _resolve(app, path)
    after = _describe(after_node, path) if after_node is not None else None
    if action == "append_text" and after is not None:
        after["appended"] = appended
    return {"ok": True, "before": before, "after": after}


def windows():
    found = []
    for app in _apps():
        try:
            pid = app.get_process_id()
            for i in range(app.childCount):
                frame = app.getChildAtIndex(i)
                if frame is not None and frame.getRoleName() in ("frame", "dialog", "window") and _visible(frame):
                    found.append({"app": app.name, "pid": pid, "title": (frame.name or "").strip(),
                                  "role": frame.getRoleName(),
                                  "active": frame.getState().contains(pyatspi.STATE_ACTIVE)})
        except Exception:
            continue
    return {"ok": True, "windows": found}


def main():
    try:
        req = json.loads(sys.stdin.read() or "{}")
        cmd = req.get("cmd")
        if cmd == "windows":
            reply = windows()
        elif cmd == "snapshot":
            reply = snapshot(int(req["pid"]), int(req.get("limit", 400)))
        elif cmd == "act":
            reply = act(int(req["pid"]), str(req["path"]), str(req["action"]), req.get("expect"), req.get("value"))
        else:
            reply = {"ok": False, "error": f"unknown command {cmd!r}"}
    except Exception as exc:  # report, never hang the caller
        reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(reply))


if __name__ == "__main__":
    main()
