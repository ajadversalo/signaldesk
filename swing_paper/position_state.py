import json
from pathlib import Path

STATE_FILE = Path("position_state.json")

def load_state():

    if not STATE_FILE.exists():
        return {}

    with open(
        STATE_FILE,
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)

def save_state(state):

    with open(
        STATE_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            indent=4
        )