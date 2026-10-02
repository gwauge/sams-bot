from datetime import datetime

def format_start(match: dict) -> str:
    return f"{datetime.fromtimestamp(match['date'] / 1000):%a %d.%m. %H:%M}"


def format_score(match: dict, state: dict) -> str:
    team1, team2 = match["teamDescription1"], match["teamDescription2"]
    sets = state["setPoints"]
    set_scores = " ".join(
        f"{s['setScore']['team1']}:{s['setScore']['team2']}" for s in state["matchSets"]
    )
    status = " (finished)" if state.get("finished") else ""
    serving = team1 if state.get("serving") == "team1" else team2
    return (
        f"[{datetime.now():%H:%M:%S}] {team1} {sets['team1']}:{sets['team2']} {team2}"
        f" | sets: {set_scores} | serving: {serving}{status}"
    )
