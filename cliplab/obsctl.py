"""Changes OBS's sources from the app: which mic and which screen it records, and a webcam in a corner.

Everything goes through obs-websocket (obsws.py) to the OBS running on this PC, and only touches the
scene OBS is showing. ClipLab adds sources it needs ("Mic", "Display", "ClipLab Webcam") rather than
renaming or deleting yours. The webcam's spot on OBS's canvas is what lets the engine cut it out for the
Webcam on top layout.
"""
from .obsws import Obs, ObsError

MIC, DISPLAY, WEBCAM, GAME = "wasapi_input_capture", "monitor_capture", "dshow_input", "game_capture"
PROPERTY = {MIC: "device_id", DISPLAY: "monitor_id", WEBCAM: "video_device_id"}
NAMES = {MIC: "Mic", DISPLAY: "Display", WEBCAM: "ClipLab Webcam"}
PROBE = "ClipLab probe"


def kind_of(item):
    return item.get("unversionedInputKind") or item.get("inputKind") or ""


def current_scene(obs):
    data = obs.request("GetCurrentProgramScene")
    scene = data.get("sceneName") or data.get("currentProgramSceneName")
    return scene, obs.request("GetSceneItemList", {"sceneName": scene}).get("sceneItems", [])


def find(obs, kind, items):
    """The input ClipLab should use for this kind: ClipLab's own, else one in the scene; mics may be global."""
    in_scene = [i for i in items if kind_of(i) == kind]
    for i in in_scene:
        if i.get("sourceName") == NAMES[kind]:
            return i["sourceName"]
    if in_scene:
        return in_scene[0]["sourceName"]
    if kind == MIC:   # Settings > Audio's "Mic/Aux" lives outside scenes
        mics = [i for i in obs.request("GetInputList").get("inputs", []) if kind_of(i) == MIC]
        return mics[0]["inputName"] if mics else None
    return None


def options(obs, kind, scene, name):
    """The devices OBS offers for this kind of source. Without a source to ask, a hidden one is borrowed."""
    temp = name is None
    if temp:
        try:
            obs.request("RemoveInput", {"inputName": PROBE})   # left over from an interrupted probe
        except ObsError:
            pass
        obs.request("CreateInput", {"sceneName": scene, "inputName": PROBE, "inputKind": kind,
                                    "inputSettings": {}, "sceneItemEnabled": False})
        name = PROBE
    try:
        items = obs.request("GetInputPropertiesListPropertyItems",
                            {"inputName": name, "propertyName": PROPERTY[kind]}).get("propertyItems", [])
    finally:
        if temp:
            obs.request("RemoveInput", {"inputName": PROBE})
    return [{"id": str(i["itemValue"]), "name": i.get("itemName") or str(i["itemValue"])} for i in items
            if i.get("itemEnabled", True) and i.get("itemValue") not in (None, "")]


def setting(obs, name, kind):
    if not name:
        return None
    value = obs.request("GetInputSettings", {"inputName": name}).get("inputSettings", {}).get(PROPERTY[kind])
    return None if value is None else str(value)


def canvas(obs):
    v = obs.request("GetVideoSettings")
    return float(v["baseWidth"]), float(v["baseHeight"])


def placement(corner, size, width, height):
    """Where ClipLab puts the webcam: a 16:9 box size x the canvas width, flush in the corner. Pixels."""
    w = width * size
    h = w * 9 / 16
    x = width - w if corner.endswith("right") else 0.0
    y = height - h if corner.startswith("bottom") else 0.0
    return x, y, w, h


def fitted(kind, cw, ch, bw, bh):
    """The picture's size inside its bounding box, for each of OBS's bounds types (cw x ch = cropped source)."""
    if kind == "OBS_BOUNDS_STRETCH" or not (cw and ch):
        return bw, bh
    inner = min(bw / cw, bh / ch)
    scale = {"OBS_BOUNDS_SCALE_INNER": inner, "OBS_BOUNDS_SCALE_OUTER": max(bw / cw, bh / ch),
             "OBS_BOUNDS_SCALE_TO_WIDTH": bw / cw, "OBS_BOUNDS_SCALE_TO_HEIGHT": bh / ch,
             "OBS_BOUNDS_MAX_ONLY": min(1.0, inner)}.get(kind, inner)
    return cw * scale, ch * scale


def item_rect(t, width, height):
    """The part of the canvas a scene item's picture covers, as [x, y, w, h] fractions (ignores rotation).

    OBS reports crop separately from the size (width/height don't have it taken off), and crops before it
    scales or fits the picture into a bounding box. The result is clipped to the canvas."""
    sw, sh = float(t.get("sourceWidth") or 0), float(t.get("sourceHeight") or 0)
    cw = max(0.0, sw - float(t.get("cropLeft") or 0) - float(t.get("cropRight") or 0))
    ch = max(0.0, sh - float(t.get("cropTop") or 0) - float(t.get("cropBottom") or 0))
    align, kind = t.get("alignment", 5), t.get("boundsType", "OBS_BOUNDS_NONE")
    if kind == "OBS_BOUNDS_NONE":
        if cw and ch:
            w, h = cw * abs(float(t.get("scaleX", 1))), ch * abs(float(t.get("scaleY", 1)))
        else:   # OBS hasn't sized the source yet
            w, h = float(t["width"]), float(t["height"])
        x, y = anchor(t["positionX"], w, align), anchor(t["positionY"], h, align, 4)
        box = (x, y, x + w, y + h)
    else:
        bw, bh = float(t["boundsWidth"]), float(t["boundsHeight"])
        w, h = fitted(kind, cw, ch, bw, bh)
        bx, by = anchor(t["positionX"], bw, align), anchor(t["positionY"], bh, align, 4)
        x = bx + (bw - w) * share(t.get("boundsAlignment", 0))
        y = by + (bh - h) * share(t.get("boundsAlignment", 0), 4)
        box = (x, y, x + w, y + h)
        if t.get("cropToBounds"):   # a picture bigger than its box (Scale to outer) is cut to the box
            box = (max(x, bx), max(y, by), min(x + w, bx + bw), min(y + h, by + bh))
    x0, y0 = max(0.0, box[0]), max(0.0, box[1])   # a webcam hanging off the canvas edge: keep the part on it
    x1, y1 = min(width, box[2]), min(height, box[3])
    return [x0 / width, y0 / height, max(0.0, x1 - x0) / width, max(0.0, y1 - y0) / height]


def share(alignment, low_bit=1):
    """How far along an edge OBS's alignment flags put the anchor: 0 (left/top), 1 (right/bottom) or 0.5."""
    return 0.0 if alignment & low_bit else 1.0 if alignment & (low_bit * 2) else 0.5


def anchor(position, size, alignment, low_bit=1):
    return float(position) - size * share(alignment, low_bit)


def item_id(items, name):
    return next((i["sceneItemId"] for i in items if i.get("sourceName") == name), None)


def webcam_state(obs, scene, items, width, height):
    name = find(obs, WEBCAM, items)
    item = next((i for i in items if i.get("sourceName") == name), None)
    if not item:
        return name, False, None
    t = obs.request("GetSceneItemTransform", {"sceneName": scene, "sceneItemId": item["sceneItemId"]})
    return name, bool(item.get("sceneItemEnabled")), item_rect(t["sceneItemTransform"], width, height)


# ---------- what the app calls ----------

def describe(find_webcams=False, obs_factory=Obs):
    """OBS's current mic, screen and webcam, with the choices for each. Raises ObsError if OBS can't be reached."""
    with obs_factory() as obs:
        scene, items = current_scene(obs)
        width, height = canvas(obs)
        out = {"scene": scene, "game_capture": any(kind_of(i) == GAME for i in items)}
        for key, kind in (("mic", MIC), ("display", DISPLAY)):
            name = find(obs, kind, items)
            out[key] = {"source": name, "current": setting(obs, name, kind), "options": options(obs, kind, scene, name)}
        name, on, rect = webcam_state(obs, scene, items, width, height)
        out["webcam"] = {"source": name, "on": on, "rect": rect, "current": setting(obs, name, WEBCAM),
                         "options": options(obs, WEBCAM, scene, name) if name or find_webcams else None}
        return out


def live_webcam_rect(obs_factory=Obs):
    """Where the webcam is in OBS right now (None if it's hidden or gone). Raises ObsError if OBS is unreachable."""
    with obs_factory(timeout=1.5) as obs:
        scene, items = current_scene(obs)
        width, height = canvas(obs)
        _, on, rect = webcam_state(obs, scene, items, width, height)
        return rect if on else None


CORNER_ALIGN = {"top-left": 5, "top-right": 6, "bottom-left": 9, "bottom-right": 10}   # OBS alignment flags


def inputs(obs):
    return obs.request("GetInputList").get("inputs", [])


def add_to_scene(obs, scene, kind, settings, reuse=None):
    """Put an input of this kind into the scene and return (name, scene item id).

    Input names are global in OBS, so ClipLab's own "Mic"/"Display"/"ClipLab Webcam" may already exist in
    another scene: then that input is added here instead of making a second one (which OBS refuses).
    reuse names an existing input to add instead (e.g. your own webcam source)."""
    known = {i["inputName"]: kind_of(i) for i in inputs(obs)}
    name = reuse or NAMES[kind]
    if known.get(name) == kind:
        if not reuse:
            obs.request("SetInputSettings", {"inputName": name, "inputSettings": settings})
        return name, obs.request("CreateSceneItem", {"sceneName": scene, "sourceName": name})["sceneItemId"]
    n = 2
    while name in known:   # the name belongs to some other kind of source: pick a free one
        name, n = f"{NAMES[kind]} {n}", n + 1
    made = obs.request("CreateInput", {"sceneName": scene, "inputName": name, "inputKind": kind, "inputSettings": settings})
    return name, made["sceneItemId"]


def capturing(obs, kind, value):
    """The name of an input anywhere in OBS already capturing this device, or None."""
    for i in inputs(obs):
        if kind_of(i) == kind and setting(obs, i["inputName"], kind) == value:
            return i["inputName"]
    return None


def apply(changes, obs_factory=Obs):
    """Change OBS: {"mic": device id, "display": monitor id, "webcam": {"device": id or "", "corner", "size"}}.

    Returns (rect, note): rect is the webcam's new spot (fractions of the canvas) when the webcam was changed,
    None when it was turned off, else "unchanged"; note is something worth telling you, or ""."""
    result, note = "unchanged", ""
    with obs_factory() as obs:
        scene, items = current_scene(obs)
        width, height = canvas(obs)
        if changes.get("mic"):
            name = find(obs, MIC, items)
            if name:
                obs.request("SetInputSettings", {"inputName": name, "inputSettings": {"device_id": changes["mic"]}})
            else:
                add_to_scene(obs, scene, MIC, {"device_id": changes["mic"]})
        if changes.get("display"):
            name = find(obs, DISPLAY, items)
            if name:
                obs.request("SetInputSettings", {"inputName": name, "inputSettings": {"monitor_id": changes["display"]}})
            else:   # a new screen capture goes at the back, so a game capture stays on top of it
                _, sid = add_to_scene(obs, scene, DISPLAY, {"monitor_id": changes["display"]})
                obs.request("SetSceneItemIndex", {"sceneName": scene, "sceneItemId": sid, "sceneItemIndex": 0})
                obs.request("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": sid, "sceneItemTransform": {
                    "positionX": 0, "positionY": 0, "alignment": 5, "boundsAlignment": 0,
                    "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": width, "boundsHeight": height}})
        if "webcam" in changes:
            cam = changes["webcam"]
            name = find(obs, WEBCAM, items)
            sid = item_id(items, name)
            if not cam.get("device"):
                if sid is not None:
                    obs.request("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": sid, "sceneItemEnabled": False})
                return None, ""
            if sid is not None:
                obs.request("SetInputSettings", {"inputName": name, "inputSettings": {"video_device_id": cam["device"]}})
            else:
                # Windows usually shows a camera in only one capture at a time, so use yours if you have one
                mine = capturing(obs, WEBCAM, cam["device"])
                name, sid = add_to_scene(obs, scene, WEBCAM, {"video_device_id": cam["device"]}, reuse=mine)
                if mine and mine != NAMES[WEBCAM]:
                    note = (f'ClipLab used your webcam source "{mine}". If it also shows inside another scene or '
                            "group in this one, hide that copy.")
            count = len(obs.request("GetSceneItemList", {"sceneName": scene}).get("sceneItems", []))
            obs.request("SetSceneItemIndex", {"sceneName": scene, "sceneItemId": sid, "sceneItemIndex": count - 1})
            x, y, w, h = placement(cam["corner"], cam["size"], width, height)
            obs.request("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": sid, "sceneItemTransform": {
                "positionX": x, "positionY": y, "alignment": 5, "rotation": 0,
                "boundsAlignment": CORNER_ALIGN[cam["corner"]],   # flush in the corner, even for a 4:3 camera
                "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": w, "boundsHeight": h}})
            obs.request("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": sid, "sceneItemEnabled": True})
            placed = obs.request("GetSceneItemTransform", {"sceneName": scene, "sceneItemId": sid})["sceneItemTransform"]
            result = item_rect(placed, width, height)   # the picture itself, once OBS knows the camera's shape
    return result, note
