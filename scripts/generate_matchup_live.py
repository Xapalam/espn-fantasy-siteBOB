#!/usr/bin/env python3
"""
Analyze every matchup in the current week and write docs/data/matchup_live.json
for the site's "Matchup Live" tab.

Per matchup this computes, from real data rather than guesswork:
  * current score and ESPN's own projected final + win probability
  * how many starters each side still has left to play
  * the single worst lineup mistake -- a benched player who already played,
    is eligible for a slot one of the starters occupies, and outscored them
  * the best and worst starter performances against their projections

...then hands those facts to Claude to call the matchup and roast whoever
deserves it. Predictions come from ESPN's numbers, not the model's
imagination; the model only narrates them.

To keep it from recycling the same bits week after week, recent lines are
stored in the output and fed back as a "don't reuse these" list.

Requires ESPN_SWID/ESPN_S2. ANTHROPIC_API_KEY is optional -- without it the
factual summary is written with no AI commentary.

Usage:
    ESPN_SWID='{...}' ESPN_S2='...' ANTHROPIC_API_KEY='sk-ant-...' \
        python3 scripts/generate_matchup_live.py --league-id 703243
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import anthropic

import fetch_espn_data as espn
from owner_resolution import resolve_owner_name, shared_last_name
from ai_tone import TONE_GUARDRAIL, looks_like_refusal

MODEL = "claude-haiku-4-5"
BENCH_SLOT = 20          # startable but benched
IR_SLOT = 21             # not startable, never a "mistake"
MIN_MISTAKE_MARGIN = 3.0  # ignore trivial "you left 0.4 points on the bench"
RECENT_LINES_KEPT = 12    # how much history to feed back as anti-repetition

SYSTEM_PROMPT = f"""You write the live matchup analysis for a private fantasy \
football league's website. {TONE_GUARDRAIL}

You'll be given one matchup: current scores, ESPN's projected finals and win \
probability, how many starters each side has left to play, the worst lineup \
mistake each manager made, and their best/worst performers.

Write 3-5 sentences that:
  - call who's winning and who's likely to lose, using the numbers given
  - roast the loser, and roast anyone whose lineup mistake cost them
  - name specific players and real numbers

Hard rules:
  - The win probability and projections are ESPN's. Use them. Do not invent \
your own numbers or stats.
  - If a side still has starters left to play, acknowledge it isn't over.
  - No corny sportscaster voice. No "folks", no "ladies and gentlemen", no \
puns on player names, no "ouch", no rhetorical questions to the reader. Write \
like a friend talking shit in a group chat, not a broadcaster.
  - If you're told the two managers are family, make it about the family \
rivalry -- bragging rights, who has to hear about this at every holiday, \
which one the family is embarrassed by this week.
  - You'll be shown lines already used in previous updates. Do not reuse \
those jokes, phrasings, or angles. Find a new one.

Respond with ONLY the analysis text, nothing else -- no preamble, no labels."""


def played(entry, season_year, period):
    """The player's actual stat block for this week, or None if not yet played."""
    player = (entry.get("playerPoolEntry") or {}).get("player") or {}
    return espn.find_stat_block(player.get("stats"), season_year, source_id=0, split_id=1, period_id=period)


def projection(entry, season_year, period):
    player = (entry.get("playerPoolEntry") or {}).get("player") or {}
    block = espn.find_stat_block(player.get("stats"), season_year, source_id=1, split_id=1, period_id=period)
    return block.get("appliedTotal") if block else None


def player_name(entry):
    return ((entry.get("playerPoolEntry") or {}).get("player") or {}).get("fullName")


def eligible_slots(entry):
    return set(((entry.get("playerPoolEntry") or {}).get("player") or {}).get("eligibleSlots") or [])


def analyze_side(side, season_year, period):
    """Facts about one team's week: score, what's left, mistakes, extremes."""
    entries = ((side.get("rosterForCurrentScoringPeriod") or {}).get("entries") or [])

    starters, bench = [], []
    for e in entries:
        slot = e.get("lineupSlotId")
        if slot == IR_SLOT:
            continue
        block = played(e, season_year, period)
        rec = {
            "name": player_name(e),
            "slot": slot,
            "actual": block.get("appliedTotal") if block else None,
            "projected": projection(e, season_year, period),
            "eligible": eligible_slots(e),
        }
        (bench if slot == BENCH_SLOT else starters).append(rec)

    yet_to_play = [s["name"] for s in starters if s["actual"] is None]

    # Worst lineup mistake: a benched player who already played, could have
    # filled a slot a starter occupied, and beat that starter by a real margin.
    worst_mistake = None
    for b in bench:
        if b["actual"] is None:
            continue
        for s in starters:
            if s["actual"] is None:
                continue  # can't judge a starter who hasn't played yet
            if s["slot"] not in b["eligible"]:
                continue
            margin = b["actual"] - s["actual"]
            if margin < MIN_MISTAKE_MARGIN:
                continue
            if worst_mistake is None or margin > worst_mistake["margin"]:
                worst_mistake = {
                    "benched": b["name"], "benchedPoints": round(b["actual"], 1),
                    "started": s["name"], "startedPoints": round(s["actual"], 1),
                    "margin": round(margin, 1),
                }

    scored = [s for s in starters if s["actual"] is not None and s["projected"] is not None]
    best = max(scored, key=lambda s: s["actual"] - s["projected"], default=None)
    worst = min(scored, key=lambda s: s["actual"] - s["projected"], default=None)

    def extreme(rec):
        if not rec:
            return None
        return {
            "name": rec["name"],
            "actual": round(rec["actual"], 1),
            "projected": round(rec["projected"], 1),
            "delta": round(rec["actual"] - rec["projected"], 1),
        }

    return {
        "score": round(espn.live_total_points(side), 1),
        "projectedFinal": round(side.get("totalProjectedPointsLive") or side.get("totalProjectedPoints") or 0.0, 1),
        "winProbability": side.get("winProbability"),
        "yetToPlay": yet_to_play,
        "worstMistake": worst_mistake,
        "bestStarter": extreme(best),
        "worstStarter": extreme(worst),
    }


def describe_side(name, facts):
    lines = [
        f"{name}: {facts['score']} pts now, ESPN projects {facts['projectedFinal']} final"
        + (f", win probability {round(facts['winProbability'] * 100)}%" if facts["winProbability"] is not None else "")
    ]
    if facts["yetToPlay"]:
        lines.append(f"  still to play: {', '.join(facts['yetToPlay'])}")
    else:
        lines.append("  all starters have played")
    if facts["bestStarter"] and facts["bestStarter"]["delta"] > 0:
        b = facts["bestStarter"]
        lines.append(f"  best: {b['name']} {b['actual']} pts (projected {b['projected']})")
    if facts["worstStarter"] and facts["worstStarter"]["delta"] < 0:
        w = facts["worstStarter"]
        lines.append(f"  worst: {w['name']} {w['actual']} pts (projected {w['projected']})")
    if facts["worstMistake"]:
        m = facts["worstMistake"]
        lines.append(f"  lineup mistake: benched {m['benched']} ({m['benchedPoints']}) "
                     f"while starting {m['started']} ({m['startedPoints']}) -- {m['margin']} pts lost")
    else:
        lines.append("  no meaningful lineup mistake")
    return "\n".join(lines)


def build_prompt(home_name, home_facts, away_name, away_facts, recent_lines, family_name=None):
    parts = [describe_side(home_name, home_facts), describe_side(away_name, away_facts)]
    if family_name:
        parts.append(f"These two are family -- both are {family_name}s. Family bragging "
                     f"rights are on the line, so make the roast about that.")
    if recent_lines:
        parts.append("Lines already used in previous updates -- do not reuse these jokes or angles:\n"
                     + "\n".join(f"- {line}" for line in recent_lines))
    return "\n\n".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--league-id", required=True, type=int)
    parser.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "docs" / "data"))
    args = parser.parse_args()

    swid, espn_s2 = os.environ.get("ESPN_SWID"), os.environ.get("ESPN_S2")
    if not swid or not espn_s2:
        print("ESPN_SWID/ESPN_S2 not set -- skipping matchup live.")
        sys.exit(0)

    data_dir = Path(args.data_dir)
    owners = json.loads((data_dir / "owners.json").read_text(encoding="utf-8"))
    meta = json.loads((data_dir / "league_meta.json").read_text(encoding="utf-8"))
    if not meta.get("years"):
        print("No seasons found, skipping matchup live.")
        sys.exit(0)
    latest_year = max(meta["years"])

    session = espn.build_session(swid, espn_s2)
    raw = espn.fetch_season(session, args.league_id, latest_year, views=["mTeam", "mMatchupScore", "mBoxscore"])
    if raw is None:
        print(f"Could not fetch live data for {latest_year}, skipping.")
        sys.exit(0)

    period = espn.current_matchup_period(raw)
    if period is None:
        print(f"Could not determine the current week in {latest_year}, skipping.")
        sys.exit(0)

    teams_by_id = {t["id"]: t for t in raw.get("teams", [])}
    games = [
        m for m in raw.get("schedule", [])
        if m.get("matchupPeriodId") == period
        and (m.get("home") or {}).get("teamId") is not None
        and (m.get("away") or {}).get("teamId") is not None
    ]
    if not games:
        print(f"No matchups found for week {period}, skipping.")
        sys.exit(0)

    out_path = data_dir / "matchup_live.json"
    recent_lines = []
    if out_path.exists():
        try:
            recent_lines = json.loads(out_path.read_text(encoding="utf-8")).get("recentLines", [])
        except (json.JSONDecodeError, TypeError):
            pass

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    client = anthropic.Anthropic(api_key=api_key) if api_key else None
    if not client:
        print("ANTHROPIC_API_KEY not set -- writing factual summaries only (no AI text).")

    results, new_lines = [], []
    for m in games:
        home, away = m["home"], m["away"]
        home_name = resolve_owner_name(teams_by_id[home["teamId"]], raw, owners)
        away_name = resolve_owner_name(teams_by_id[away["teamId"]], raw, owners)
        home_facts = analyze_side(home, latest_year, period)
        away_facts = analyze_side(away, latest_year, period)
        family_name = shared_last_name(home_name, away_name)

        leader, trailer = ((home_name, away_name) if home_facts["score"] >= away_facts["score"]
                           else (away_name, home_name))
        margin = abs(home_facts["score"] - away_facts["score"])
        analysis = f"{leader} leads {trailer} by {round(margin, 1)}."

        if client:
            try:
                response = client.messages.create(
                    model=MODEL, max_tokens=500, system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": build_prompt(
                        home_name, home_facts, away_name, away_facts, recent_lines, family_name)}],
                )
                text = "".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "refusal" or looks_like_refusal(text):
                    print(f"  {home_name} vs {away_name}: model declined, using factual summary", file=sys.stderr)
                elif text:
                    analysis = text
                    new_lines.append(text)
            except anthropic.APIStatusError as e:
                print(f"  {home_name} vs {away_name}: API error ({e.status_code}), using factual summary", file=sys.stderr)
            except anthropic.APIConnectionError as e:
                print(f"  {home_name} vs {away_name}: connection error ({e}), using factual summary", file=sys.stderr)

        results.append({
            "homeOwner": home_name, "awayOwner": away_name,
            "home": home_facts, "away": away_facts,
            "familyName": family_name,
            "analysis": analysis,
        })
        print(f"  {home_name} {home_facts['score']} - {away_facts['score']} {away_name}"
              f" (yet to play: {len(home_facts['yetToPlay'])}/{len(away_facts['yetToPlay'])})")

    output = {
        "generatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "seasonId": latest_year,
        "matchupPeriodId": period,
        "matchups": results,
        "recentLines": (new_lines + recent_lines)[:RECENT_LINES_KEPT],
    }
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"\nDone. Wrote matchup live analysis for {latest_year} week {period}.")


if __name__ == "__main__":
    main()
