"""Shared owner-name resolution logic, used by every script that needs to
turn an ESPN team object into a real person's name. Mirrors the same
resolution order as docs/app.js's resolveOwnerName -- keep both in sync."""


def last_names(owner_label):
    """Last names found in a resolved owner label, keyed lowercase.

    Handles co-owned teams ("Jetmir Asllani / Peter Mardjonovic" -> both
    surnames) and skips labels that aren't real names -- departed members
    fall back to raw ESPN handles like "Brezi6714", which have no surname
    and must never be matched against each other.
    """
    found = {}
    for part in (owner_label or "").split("/"):
        tokens = part.strip().split()
        if len(tokens) < 2:
            continue  # single token -> an ESPN handle, not a first/last name
        surname = tokens[-1]
        if any(ch.isdigit() for ch in surname):
            continue
        found[surname.lower()] = surname
    return found


def shared_last_name(label_a, label_b):
    """The surname two owners have in common, or None. Used to treat league
    members with the same last name as family."""
    a, b = last_names(label_a), last_names(label_b)
    for key in sorted(a):
        if key in b:
            return a[key]
    return None


def team_display_name(team):
    name = (team.get("name") or "").strip()
    if name:
        return name
    parts = [p for p in [team.get("location"), team.get("nickname")] if p]
    return " ".join(parts).strip() or f"Team {team.get('id')}"


def resolve_owner_name(team, season, owners):
    team_name = team_display_name(team)
    current = owners.get("currentTeamNames", {})
    if team_name in current:
        return current[team_name]

    overrides = owners.get("memberNameOverrides", {})
    for member_id in team.get("owners", []):
        if member_id in overrides:
            return overrides[member_id]

    member_ids = set(team.get("owners", []))
    for member in season.get("members", []):
        if member.get("id") in member_ids and member.get("displayName"):
            return member["displayName"]

    return f"{team_name} (unmapped)"
