"""python -m cliplab [run | engine | app | stop | config]

  run     (default) engine + app, restarted if either exits
  engine  just the engine, in the foreground
  app     just the web app, in the foreground
  stop    stop a running `run`
  config  print where ClipLab keeps its files and the settings in effect
"""
import json
import sys

from . import engine, launcher, server
from .settings import CONFIG, HOME, check_folders, load_config


def main(argv):
    cmd = argv[0] if argv else "run"
    if cmd == "run":
        launcher.main()
    elif cmd == "engine":
        engine.main()
    elif cmd == "app":
        server.main()
    elif cmd == "stop":
        print("Stopped." if launcher.stop() else "ClipLab wasn't running.")
    elif cmd == "config":
        cfg = load_config()
        print(f"Home:   {HOME}\nConfig: {CONFIG} ({'found' if CONFIG.exists() else 'not created yet; using defaults'})")
        problem = check_folders(cfg)
        if problem:
            print("Problem: " + problem)
        print(json.dumps(cfg, indent=2))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
