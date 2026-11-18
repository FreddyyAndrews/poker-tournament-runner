"""A calling station: never folds, never raises. Something simple to play against."""


def decide(game_state: dict) -> dict:
    if game_state.get("can_check"):
        return {"action": "check"}
    return {"action": "call"}
