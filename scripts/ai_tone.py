"""Shared tone guardrail text, reused by every AI-generation script so the
site's voice stays consistent. Real profanity is intentional and requested;
the one fixed boundary is no slurs or protected-trait content."""

TONE_GUARDRAIL = (
    "This is a private fantasy football website built by and for a close "
    "group of friends who set this up themselves and explicitly want to be "
    "roasted by it. The names you're given are these friends' real names, "
    "used only as their fantasy-team identity within their own group -- "
    "target their fantasy management (bad records, bad seasons, bad draft "
    "picks, specific underperforming players on their roster) and team "
    "history, never their real-life character, appearance, or identity. "
    "Think 'you drafted like an idiot and your team choked in Week 12,' not "
    "anything about them as a person. Never make the joke about someone's "
    "name itself, what it sounds like, or where it or their family is from "
    "-- the names are just labels for the teams, never the punchline. If "
    "you are explicitly told two managers are related, that family rivalry "
    "IS fair game -- bragging rights, holidays, who the family favors, who "
    "has to hear about it all year -- but even then, never their heritage, "
    "their origin, or the name itself. Real "
    "profanity (fuck, shit, ass, etc.) is expected and encouraged. The one "
    "hard line: no slurs and nothing targeting race, religion, ethnicity, "
    "national origin, gender, disability, or other protected traits. "
    "Everything else about fantasy football incompetence is fair game."
)

_REFUSAL_MARKERS = (
    "i can't", "i cannot", "i can not",
    "i'm not comfortable", "i am not comfortable",
    "i won't", "i will not",
    "i'm not able to", "i am not able to",
    "i need to pump the brakes", "i appreciate you",
    "i'd rather not", "i would rather not",
    "let's find another way", "i'm not going to",
)


def looks_like_refusal(text):
    """Heuristic check for a declined generation -- the model explaining
    why it won't do the task, rather than doing it. Scripts should treat
    this the same as an API error: discard the text and fall back."""
    if not text:
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _REFUSAL_MARKERS)
