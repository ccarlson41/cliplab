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


def item_rect(t, width, height):
    """The part of the canvas a scene item's picture covers, as [x, y, w, h] fractions (ignores rotation)."""
    sw, sh = float(t.get("sourceWidth") or 0), float(t.get("sourceHeight") or 0)
    if t.get("boundsType", "OBS_BOUNDS_NONE") != "OBS_BOUNDS_NONE":
        bw, bh = float(t["boundsWidth"]), float(t["boundsHeight"])
        w, h = bw, bh
        if t["boundsType"] == "OBS_BOUNDS_SCALE_INNER" and sw and sh:   # letterboxed inside its box
            scale = min(bw / sw, bh / sh)
            w, h = sw * scale, sh * scale
        x, y = anchor(t["positionX"], bw, t.get("alignment", 5)), anchor(t["positionY"], bh, t.get("alignment", 5), 4)
        x += (bw - w) * share(t.get("boundsAlignment", 0))
        y += (bh - h) * share(t.get("boundsAlignment", 0), 4)
    else:
        w, h = float(t["width"]), float(t["height"])
        x, y = anchor(t["positionX"], w, t.get("alignment", 5)), anchor(t["positionY"], h, t.get("alignment", 5), 4)
    return [x / width, y / height, w / width, h / height]


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


def apply(changes, obs_factory=Obs):
    """Change OBS: {"mic": device id, "display": monitor id, "webcam": {"device": id or "", "corner", "size"}}.

    Returns the webcam's new rect (fractions of the canvas) when the webcam was changed, else "unchanged"."""
    result = "unchanged"
    with obs_factory() as obs:
        scene, items = current_scene(obs)
        width, height = canvas(obs)
        if changes.get("mic"):
            name = find(obs, MIC, items)
            if name:
                obs.request("SetInputSettings", {"inputName": name, "inputSettings": {"device_id": changes["mic"]}})
            else:
                obs.request("CreateInput", {"sceneName": scene, "inputName": NAMES[MIC], "inputKind": MIC,
                                            "inputSettings": {"device_id": changes["mic"]}})
        if changes.get("display"):
            name = find(obs, DISPLAY, items)
            if name:
                obs.request("SetInputSettings", {"inputName": name, "inputSettings": {"monitor_id": changes["display"]}})
            else:   # new screen capture goes at the back, so a game capture stays on top of it
                made = obs.request("CreateInput", {"sceneName": scene, "inputName": NAMES[DISPLAY], "inputKind": DISPLAY,
                                                   "inputSettings": {"monitor_id": changes["display"]}})
                obs.request("SetSceneItemIndex", {"sceneName": scene, "sceneItemId": made["sceneItemId"],
                                                  "sceneItemIndex": 0})
                obs.request("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": made["sceneItemId"],
                                                      "sceneItemTransform": {"positionX": 0, "positionY": 0,
                                                                             "alignment": 5, "boundsAlignment": 0,
                                                                             "boundsType": "OBS_BOUNDS_SCALE_INNER",
                                                                             "boundsWidth": width,
                                                                             "boundsHeight": height}})
        if "webcam" in changes:
            cam = changes["webcam"]
            name = find(obs, WEBCAM, items)
            sid = item_id(items, name)
            if not cam.get("device"):
                if sid is not None:
                    obs.request("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": sid, "sceneItemEnabled": False})
                return None
            if sid is None:
                name = NAMES[WEBCAM]
                sid = obs.request("CreateInput", {"sceneName": scene, "inputName": name, "inputKind": WEBCAM,
                                                  "inputSettings": {"video_device_id": cam["device"]}})["sceneItemId"]
            else:
                obs.request("SetInputSettings", {"inputName": name, "inputSettings": {"video_device_id": cam["device"]}})
            count = len(obs.request("GetSceneItemList", {"sceneName": scene}).get("sceneItems", []))
            obs.request("SetSceneItemIndex", {"sceneName": scene, "sceneItemId": sid, "sceneItemIndex": count - 1})
            x, y, w, h = placement(cam["corner"], cam["size"], width, height)
            obs.request("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": sid, "sceneItemTransform": {
                "positionX": x, "positionY": y, "alignment": 5, "boundsAlignment": 0, "rotation": 0,
                "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsWidth": w, "boundsHeight": h}})
            obs.request("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": sid, "sceneItemEnabled": True})
            result = [x / width, y / height, w / width, h / height]
    return result
