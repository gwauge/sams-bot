from dataclasses import dataclass, field


@dataclass
class Match:
    id: str
    team1: str
    team2: str
    teamDescription1: str
    teamDescription2: str
    date: int
    matchSeries: str

    @classmethod
    def from_dict(cls, data: dict) -> "Match":
        return cls(
            id=data["id"],
            team1=data["team1"],
            team2=data["team2"],
            teamDescription1=data["teamDescription1"],
            teamDescription2=data["teamDescription2"],
            date=data["date"],
            matchSeries=data["matchSeries"],
        )
