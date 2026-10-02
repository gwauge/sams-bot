from dataclasses import dataclass, field
from typing import Literal

from sams_bot.format import format_start
from sams_bot.match import Match


GenderType = Literal["male", "female"]

def parse_gender(gender_str: str) -> GenderType:
    gender_str = gender_str.lower()
    if gender_str in ("male", "m"):
        return "male"
    elif gender_str in ("female", "f"):
        return "female"
    else:
        raise ValueError(f"Invalid gender string: {gender_str}")


@dataclass
class Team:
    id: str  # uuid
    name: str
    code: str
    logo: str  # file url
    gender: GenderType

    matches: list[Match] = field(default_factory=list)

    @classmethod
    def from_dict(
        cls,
        data: dict,
        gender: GenderType,
        league_id: str,
    ) -> "Team":
        return cls(
            id=data["id"],
            name=data["name"],
            code=data["clubCode"],
            logo=data["logoImage200"],
            gender=gender,
        )

    @classmethod
    def from_id(cls, team_id: str, snapshot: dict) -> "Team":
        for s in snapshot["matchSeries"].values():
            if s["class"] == "League":
                for team in s["teams"]:
                    if team["id"] == team_id:
                        return cls.from_dict(team, parse_gender(s["gender"]), s["id"])

        raise ValueError(f"Team with id {team_id} not found in snapshot")


if __name__ == "__main__":
    import argparse
    import re
    from sams_bot.sams import fetch_snapshot

    parser = argparse.ArgumentParser(description="Fetch and print SAMS data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    name_parser = subparsers.add_parser("name", help="Fetch all teams that match a given name")
    name_parser.add_argument("name", help="team name to search for")

    games_parser = subparsers.add_parser("games", help="Fetch all matches for a given team id")
    games_parser.add_argument("team_id", help="team id to fetch matches for")

    args = parser.parse_args()

    snapshot = fetch_snapshot()
    if args.command == "name":

        for s in snapshot["matchSeries"].values():
            if s["class"] == "League":
                for team in s["teams"]:
                    if re.search(args.name, team["name"], re.IGNORECASE):
                        team_obj = Team.from_dict(team, parse_gender(s["gender"]), s["id"])
                        print(f"[{team_obj.gender[0].upper()}] {team_obj.name} ({team_obj.code}) - {team_obj.id}")

    if args.command == "games":
        team_obj = Team.from_id(args.team_id, snapshot)
        print(f"Matches for {team_obj.name} ({team_obj.code}):")

        for matchDay in snapshot["matchDays"]:
            for match in matchDay["matches"]:
                if match["team1"] == team_obj.id or match["team2"] == team_obj.id:
                    print(f"\t{match['id']} | {match['teamDescription1']} vs {match['teamDescription2']} | {format_start(Match.from_dict(match))}")
