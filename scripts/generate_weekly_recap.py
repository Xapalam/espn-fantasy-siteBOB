#!/usr/bin/env python3
"""
Generate two things for the current season and write them to
docs/data/weekly_recap.json:

  - lastWeek: an explicit, vulgar recap of every matchup in the most
    recently completed week
  - thisWeek: an explicit, trash-talking preview of every matchup in the
    upcoming (not yet played) week

Always produces output -- if ANTHROPIC_API_KEY isn't set (or a given
matchup's call fails), that matchup just gets a plain factual line instead
of an AI one, so the "This Week" tab always has something real to show.

Usage:
    ANTHROPIC_API_KEY='sk-ant-...' python3 scripts/generate_weekly_recap.py
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import anthropic

from owner_resolution import resolve_owner_name, shared_last_name
from ai_tone import TONE_GUARDRAIL, looks_like_refusal

MODEL = "claude-haiku-4-5"

RECAP_SYSTEM_PROMPT = f"""You write short, explicit, vulgar recaps of \
individual fantasy football matchups for a private league's website. \
{TONE_GUARDRAIL}

You will be given one matchup: both teams, their scores, and the margin. \
Write a 2-4 sentence recap that roasts the loser specifically, using the \
real score and margin. Make it filthy and funny, not just filthy.

Respond with ONLY the recap text, nothing else -- no preamble, no labels."""

PREVIEW_SYSTEM_PROMPT = f"""You write short, explicit, trash-talking \
previews for upcoming fantasy football matchups on a private league's \
website. {TONE_GUARDRAIL}

You will be given two teams facing off this week, with their season \
records so far. Write a 2-3 sentence hype/trash-talk preview -- this game \
hasn't happened yet, so don't invent a score or a result, just talk shit \
about who they are and why they're going to choke.

If both records are 0-0 (preseason or week 1, nothing played yet), that's \
completely normal -- do NOT decline or say there's nothing to roast. \
Instead go after their draft-day decisions, name/reputation within the \
league, or just pure hype trash talk about who's going to embarrass \
themselves this week. There is always something to roast; never refuse \
because the record is empty.

Respond with ONLY the preview text, nothing else -- no preamble, no labels."""


def family_note(name_a, name_b):
    family = shared_last_name(name_a, name_b)
    if not family:
        return ""
    return (f"\nThese two are family -- both are {family}s. Family bragging rights "
            f"are on the line, so make the roast about that.")


def recap_prompt(winner, loser, winner_score, loser_score):
    return (
        f"Winner: {winner}, scored {winner_score:.1f}\n"
        f"Loser: {loser}, scored {loser_score:.1f}\n"
        f"Margin: {winner_score - loser_score:.1f} points"
        + family_note(winner, loser)
    )


def preview_prompt(name_a, record_a, name_b, record_b):
    return (
        f"Team A: {name_a}, currently {record_a}\n"
        f"Team B: {name_b}, currently {record_b}"
        + family_note(name_a, name_b)
    )


def team_record_str(team):
    w, l, t = team.get("wins") or 0, team.get("losses") or 0, team.get("ties") or 0
    return f"{w}-{l}" + (f"-{t}" if t else "")


def find_last_completed_week(season):
    """Most recent matchupPeriodId with at least one decided (non-bye,
    non-placeholder) game. Returns (period, games) or (None, [])."""
    matchups = season.get("matchups", [])
    periods = sorted({m["matchupPeriodId"] for m in matchups}, reverse=True)
    for period in periods:
        games = [
            m for m in matchups
            if m["matchupPeriodId"] == period
            and m["home"]["totalPoints"] is not None
            and m["away"]["totalPoints"] is not None
            and not (m["home"]["totalPoints"] == 0 and m["away"]["totalPoints"] == 0)
        ]
        if games:
            return period, games
    return None, []


def find_upcoming_week(season, after_period):
    """The next matchupPeriodId after the last completed one (or 1, if
    nothing's been completed yet), skipping bye placeholders."""
    target = (after_period + 1) if after_period is not None else 1
    games = [
        m for m in season.get("matchups", [])
        if m["matchupPeriodId"] == target
        and m["home"]["teamId"] is not None
        and m["away"]["teamId"] is not None
    ]
    return (target, games) if games else (None, [])


def build_recap(client, m, teams_by_id, season, owners):
    home_name = resolve_owner_name(teams_by_id[m["home"]["teamId"]], season, owners)
    away_name = resolve_owner_name(teams_by_id[m["away"]["teamId"]], season, owners)
    home_score, away_score = m["home"]["totalPoints"], m["away"]["totalPoints"]

    if home_score >= away_score:
        winner, loser, winner_score, loser_score = home_name, away_name, home_score, away_score
    else:
        winner, loser, winner_score, loser_score = away_name, home_name, away_score, home_score

    recap = f"{winner} beat {loser} {winner_score:.1f}-{loser_score:.1f}."
    if client:
        try:
            response = client.messages.create(
                model=MODEL, max_tokens=300, system=RECAP_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": recap_prompt(winner, loser, winner_score, loser_score)}],
            )
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "refusal" or looks_like_refusal(text):
                print(f"  {winner} vs {loser}: model declined, using fallback recap", file=sys.stderr)
            elif text:
                recap = text
        except anthropic.APIStatusError as e:
            print(f"  {winner} vs {loser}: API error ({e.status_code}), using fallback recap", file=sys.stderr)
        except anthropic.APIConnectionError as e:
            print(f"  {winner} vs {loser}: connection error ({e}), using fallback recap", file=sys.stderr)

    print(f"  [recap] {winner} def. {loser} {winner_score:.1f}-{loser_score:.1f}")
    return {
        "homeOwner": home_name, "awayOwner": away_name,
        "homeScore": home_score, "awayScore": away_score,
        "winner": winner, "loser": loser,
        "recap": recap,
    }


def build_preview(client, m, teams_by_id, season, owners):
    home_team = teams_by_id[m["home"]["teamId"]]
    away_team = teams_by_id[m["away"]["teamId"]]
    home_name = resolve_owner_name(home_team, season, owners)
    away_name = resolve_owner_name(away_team, season, owners)
    home_record = team_record_str(home_team)
    away_record = team_record_str(away_team)

    preview = f"{home_name} ({home_record}) vs {away_name} ({away_record})."
    if client:
        try:
            response = client.messages.create(
                model=MODEL, max_tokens=300, system=PREVIEW_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": preview_prompt(home_name, home_record, away_name, away_record)}],
            )
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if response.stop_reason == "refusal" or looks_like_refusal(text):
                print(f"  {home_name} vs {away_name}: model declined, using fallback preview", file=sys.stderr)
            elif text:
                preview = text
        except anthropic.APIStatusError as e:
            print(f"  {home_name} vs {away_name}: API error ({e.status_code}), using fallback preview", file=sys.stderr)
        except anthropic.APIConnectionError as e:
            print(f"  {home_name} vs {away_name}: connection error ({e}), using fallback preview", file=sys.stderr)

    print(f"  [preview] {home_name} vs {away_name}")
    return {
        "homeOwner": home_name, "awayOwner": away_name,
        "homeRecord": home_record, "awayRecord": away_record,
        "preview": preview,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "docs" / "data"))
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    owners = json.loads((data_dir / "owners.json").read_text(encoding="utf-8"))
    meta = json.loads((data_dir / "league_meta.json").read_text(encoding="utf-8"))

    if not meta.get("years"):
        print("No seasons found, skipping weekly recap.")
        sys.exit(0)

    latest_year = max(meta["years"])
    season = json.loads((data_dir / "seasons" / f"season_{latest_year}.json").read_text(encoding="utf-8"))
    teams_by_id = {t["id"]: t for t in season["teams"]}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key) if api_key else None
    if not client:
        print("ANTHROPIC_API_KEY not set -- writing factual text only (no AI text).")

    completed_period, completed_games = find_last_completed_week(season)
    last_week = None
    if completed_period is not None:
        last_week = {
            "matchupPeriodId": completed_period,
            "matchups": [build_recap(client, m, teams_by_id, season, owners) for m in completed_games],
        }
    else:
        print(f"No completed matchups yet in {latest_year}.")

    upcoming_period, upcoming_games = find_upcoming_week(season, completed_period)
    this_week = None
    if upcoming_period is not None:
        this_week = {
            "matchupPeriodId": upcoming_period,
            "matchups": [build_preview(client, m, teams_by_id, season, owners) for m in upcoming_games],
        }
    else:
        print(f"No upcoming matchups found in {latest_year}.")

    if last_week is None and this_week is None:
        print("Nothing to write, skipping.")
        sys.exit(0)

    output = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "seasonId": latest_year,
        "lastWeek": last_week,
        "thisWeek": this_week,
    }
    (data_dir / "weekly_recap.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nDone. Wrote weekly recap for {latest_year}.")


if __name__ == "__main__":
    main()
