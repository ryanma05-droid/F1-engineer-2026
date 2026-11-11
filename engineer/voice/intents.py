"""What did the driver ask? Keyword rules over the recognised text.

Rules are tried in order and the first match wins, so more specific questions come first ("what
if I box now" before "when do I box"). Speech recognition writes what it heard, so the rules
allow for the usual mishearings and spellings ("tires", "tyre's", "box" heard as "boxx").

Drivers can be named ("where am I gaining on Lando?"): `driver_in` finds the car from the game's
surnames or a first name. Questions that match nothing are answered "Say again?" and logged with intent "unknown" in
data/ptt/questions.jsonl: that log is how new phrasings get added over time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Intent:
    name: str
    text: str                    # the normalised text it was matched on


# Drivers can be named by first name ("gaining on Lando?"); the game only sends surnames.
FIRST_NAMES = {
    "max": "verstappen", "yuki": "tsunoda", "lando": "norris", "oscar": "piastri",
    "charles": "leclerc", "lewis": "hamilton", "george": "russell", "kimi": "antonelli",
    "kimmy": "antonelli",                         # how speech recognition hears Kimi
    "andrea": "antonelli", "fernando": "alonso", "lance": "stroll", "pierre": "gasly",
    "franco": "colapinto", "jack": "doohan", "esteban": "ocon", "ollie": "bearman",
    "oliver": "bearman", "alex": "albon", "alexander": "albon", "carlos": "sainz",
    "liam": "lawson", "isack": "hadjar", "nico": "hulkenberg", "gabriel": "bortoleto",
    "valtteri": "bottas", "sergio": "perez", "checo": "perez", "arvid": "lindblad",
}
SURNAMES = set(FIRST_NAMES.values()) | {"kimi antonelli"}
_DRIVER = r"\b(" + "|".join(sorted(set(FIRST_NAMES) | SURNAMES, key=len, reverse=True)) + r")\b"
_NUM_WORDS = (r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy)[ -]?)?(?:one|two|three|four|five|six|"
              r"seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
              r"eighteen|nineteen|twenty|thirty|forty|fifty)")
_TURN = (r"\bturn (\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
         r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\b")

# Tracks as they're said ("strategy for Suzuka", "the race in Spain"): word -> game track id.
TRACK_WORDS = {
    "melbourne": 0, "australia": 0, "albert park": 0, "shanghai": 2, "china": 2,
    "bahrain": 3, "sakhir": 3, "barcelona": 4, "catalunya": 4, "spain": 4, "monaco": 5,
    "montreal": 6, "canada": 6, "silverstone": 7, "britain": 7, "british": 7,
    "hungaroring": 9, "hungary": 9, "budapest": 9, "spa": 10, "belgium": 10, "monza": 11,
    "italy": 11, "singapore": 12, "suzuka": 13, "japan": 13, "abu dhabi": 14, "yas marina": 14,
    "texas": 15, "austin": 15, "cota": 15, "brazil": 16, "interlagos": 16, "sao paulo": 16,
    "austria": 17, "red bull ring": 17, "spielberg": 17, "mexico": 19, "baku": 20,
    "azerbaijan": 20, "zandvoort": 26, "netherlands": 26, "dutch": 26, "imola": 27,
    "jeddah": 29, "saudi": 29, "miami": 30, "vegas": 31, "las vegas": 31, "qatar": 32,
    "losail": 32,
}
_TRACK = r"(" + "|".join(sorted(TRACK_WORDS, key=len, reverse=True)) + r")"

# (intent, patterns). A pattern is a regex searched in the normalised text. Order matters: the
# first intent with a matching pattern wins, so specific questions come before general ones.
RULES: list[tuple[str, list[str]]] = [
    # "Log that" / "log the following question: ..." goes to your feedback log (feedback.py).
    # First, so the question being logged isn't answered instead. Only at the start of what was
    # said, and "lock" (a common mishearing of "log") only before that/this/the following.
    ("log_question", [r"^(please |can you |could you )?(log|flag|note|mark)\b",
                      r"^(please |can you |could you )?lock (that|this|it|the following|the last|"
                      r"the previous|my|a|question)\b",
                      r"\b(log|add|put) (that|this|it) (to|in|on) (the |my )?(feedback|log|list)\b"]),
    # 13 Nov: strategy as a dialogue. Your preferences steer the plan (voice/dialogue.py reads
    # them); offers are answered yes / no; "why?", "are you sure?"; race pace.
    ("prefs_clear", [r"\bback to (your|the|our|original) (plan|strategy|call)\b",
                     r"\b(reset|clear|forget|scrap) (the |my |that |those )?(strategy|plan|"
                     r"preferences|requests?|that)\b", r"^(your call|you decide)\W*$"]),
    ("prefs", [r"\b(finish|end|last stint|final stint)\w* on (the )?(softs?|mediums?|hards?)\b",
               r"\b(max|maximum|limit|no more than|up to|don't go (past|over|above)|"
               r"(run|take) (them|it|these|the tyres) to)\b.*\b(\d+|" + _NUM_WORDS + r")\s*(%|percent)",
               r"\b(\d+|" + _NUM_WORDS + r")\s*(%|percent)\s*(max|maximum|limit|tops)\b",
               r"\bno more stops\b",
               r"\b(only|make it|do a|let's do|lets do|i want|go for|switch to|i'd like|change to)"
               r"\b.*\b(one|two|three|1|2|3)[ -]?stop", r"\b(one|two|three|1|2|3)[ -]?stop(per)? only\b",
               r"^(no|avoid( the)?|not the|skip( the)?) (softs?|mediums?|hards?)\b",
               r"\b(don't (want|give me|put me on)|avoid|without)( the)? (softs?|mediums?|hards?)\b",
               r"\b(go|going|stay out|run|stretch)(ing)? (a bit )?long(er)?\b",
               r"\bextend (the |this )?stint\b",
               r"\b(box|pit|stop|come in)(ing)? (early|earlier|short|sooner)\b", r"\bgo(ing)? short\b",
               r"\b(i want to|i'll|let's|lets|i'm going to|we'll|i'd like to|plan to) (box|pit|stop)"
               r"\w* (on |at |end of )?lap\b"]),
    ("accept", [r"^(yes|yeah|yep|yup|ok|okay|sure|go on|affirmative)\W*(please|mate)?\W*$",
                r"\b(do it|go for it|take it|let's do (it|that)|go with (it|that)|sounds good|"
                r"deal|let's go for it|yes please)\b"]),
    ("decline", [r"^(no|nope|nah|negative)\W*(thanks|mate)?\W*$",
                 r"\b(stick with|keep) (it|that)\W*$",
                 r"\b(stick with|keep) (the plan|our plan|the current( plan)?|what we have)\b",
                 r"\bno thanks\b"]),
    ("pace_save", [r"\bhow much (do|should) (i|we) (need to )?(save|manage)\b",
                   r"\bdo (i|we) need to (save|manage|look after)\b",
                   r"\bhow much (tyre )?(saving|management)\b"]),
    ("pace_push", [r"\b(can|could|should) (i|we) (push|attack|go faster|go flat out|send it)\b",
                   r"\bhow hard can (i|we) push\b"]),
    ("pace_mode", [r"\bi('m| am) (managing|saving|pushing|attacking|going to push|going to manage|"
                   r"going to save)\b", r"\b(push|manage|management) mode\b",
                   r"\bi('ll| will) (push|manage|save)\b"]),
    ("radio_check", [r"\bradio check\b", r"\bcan you hear me\b", r"\bdo you (read|copy)\b"]),
    # 29 Oct Imola: "Repeat that."
    ("repeat", [r"\brepeat\b", r"\bsay (that|it) again\b", r"\bcome again\b",
                r"\bwhat did you (just )?say\b", r"\bdidn't (catch|hear) that\b"]),
    # 11 Nov Miami: "Go chatty mode." The modes by name (Quiet is the app's name for terse).
    ("tempo_terse", [r"\b(quiet|terse) mode\b", r"\b(go|switch|change|put me)( back)? (to |in ?to )?(quiet|terse)\b"]),
    ("tempo_chatty", [r"\bchatty mode\b", r"\b(go|switch|change|put me)( back)? (to |in ?to )?chatty\b"]),
    ("quiet", [r"\b(be )?quiet\b", r"\bstop talking\b", r"\bshut up\b", r"\bsilence\b",
               r"\bleave me alone\b", r"\bno more (calls|talking)\b"]),
    ("detail_specific", [r"\bgive me (the )?(numbers|specifics|exact numbers)\b",
                         r"\bbe (more )?specific\b", r"\b(more|exact) (numbers|specifics)\b"]),
    ("detail_plain", [r"\bkeep it simple\b", r"\b(no|fewer|less) numbers\b", r"\bplain (english|words)\b"]),
    # The mode needs saying as a mode: "give me some coaching" / "coach me" ask for a tip now.
    ("tempo_coaching", [r"\bcoaching mode\b", r"\b(switch|go|change|put me) (in)?to coaching\b",
                        r"\bwork on my driving\b"]),
    # Before the race (car and tyre setup): "what tyres should I start on?", "strategy for
    # Suzuka", "plan for twenty laps". Any plain "what's the plan?" asked before the lights go
    # out gets the same answer (answers.py).
    ("prerace_plan", [r"\b(start|starting) (the race )?on\b", r"\bstart(ing)? (tyres?|compound)\b",
                      r"\bpre ?race\b",
                      r"\b(strategy|plan) for (the race|\d+ laps?|" + _TRACK + r")\b",
                      r"\b(strategy|plan)\b.*\b\d+ laps?\b", r"\b(strategy|plan) for\b.*\blaps?\b",
                      r"\btyres? for the race\b"]),
    ("tempo_chatty", [r"\b(talk|say) more\b", r"\btalk to me more\b",
                      r"\b(give me )?(some |more )?chatter\b", r"\bmore (chatty|talk|info|information|detail)\b",
                      r"\bbe (more )?chatty\b", r"\bchattier\b", r"\bmore often\b"]),
    ("tempo_terse", [r"\b(talk|say) less\b", r"\bless (chatty|talk|talking|info|detail)\b",
                     r"\bkeep it short\b", r"\bshort(er)? calls\b", r"\bless often\b",
                     r"\bessentials only\b", r"\btone it down\b"]),
    # "Talk to me about tyre wear" is a tyre question (29 Oct), not "talk to me".
    ("talk", [r"\btalk to me\b(?! about| more)", r"\bkeep me (posted|updated|informed)\b",
              r"\bback to normal\b", r"\bresume\b", r"\byou can talk\b"]),
    # Coaching: "how far off the fastest car?", "where's the time?"
    ("gap_fastest", [r"\b(how far|gap|how much)\b.*\b(off|to|from|behind) (the )?(fastest|quickest)\b",
                     r"\bwhere('s| is) the time\b"]),
    # 3 Nov: "Where am I going to come out?" (if I box now)
    ("rejoin", [r"\bwhere (am i|are we) (going to|gonna) come out\b",
                r"\bwhere (would|will|do|does|should) (i|we) come out\b",
                r"\bwhere (would|will|do) (i|we) rejoin\b", r"\bcome out (where|behind who)\b"]),
    # 2 Nov: "Where's my track position?" meaning once the stops shake out (logged: "give
    # accurate track position").
    ("true_position", [r"\btrack position\b",
                       r"\b(real|true|actual|accurate|virtual) (track )?position\b",
                       r"\bposition (after|once) .*\b(stops?|pit|pitted|stopped)\b",
                       r"\bwhere (will|would) (i|we) be (after|once)\b"]),
    # 2 Nov: "Any tips for managing the tyres? Where am I over exerting them?"
    ("tyre_tips", [r"\b(tips?|advice|help)\b.*\b(manag\w*|sav\w*|look after)\b.*\btyres?\b",
                   r"\bhow (do|can|should) (i|we) (manage|save|look after) (the |my |these )?tyres?\b",
                   r"\bover ?(exert|work|heat|us)\w*\b", r"\b(killing|cooking|hurting) (the |my )?tyres?\b"]),
    # Your rolling ideal lap (tools/ideal.py): before best_lap ("best possible lap").
    ("ideal_lap", [r"\bideal\b", r"\btheoretical\b", r"\bbest possible\b",
                   r"\b(potential|optimal|optimum) lap\b"]),
    # What the driver report says to work on (report/focus.py).
    ("report_focus", [r"\bwhat (should|do) (i|we) (work on|focus on|practi[cs]e)\b",
                      r"\bwhat('s| is) (my|the) focus\b",
                      r"\bwhat does (my|the) (driver )?report say\b",
                      r"\bwhere do (i|we) (usually|normally|always) lose\b"]),
    # 3 Nov: "Give me tips on how to speed up."
    ("speed_tips", [r"\b(tips?|advice)\b.*\b(speed up|faster|quicker|pace|lap time)\b",
                    r"\bhow (do|can|could) (i|we) (speed up|go faster|get faster|be quicker|"
                    r"find time|find pace)\b", r"\bwhere (can|could) (i|we) find time\b"]),
    # 29 Oct: "How does this current tyre hold up to the end of the race?", "Will I get over 45%?"
    # 2 Nov: "Are these tyres going to make it?"
    # 3 Nov (20-lap races): "What's the fastest lap right now?", "What is the overall fastest
    # lap? Not just mine." -> the session's fastest, not your own best.
    ("fastest_overall", [r"\bfastest lap\b.*\b(overall|of the race|on track|right now|any(one|body| driver)|"
                         r"not just mine|of everyone|in the race|in the session)\b",
                         r"\b(overall|session|race'?s?) fastest lap\b",
                         r"\bwho (has|'s got|set|holds|has got) (the )?fastest lap\b",
                         r"^what('s| is) the fastest lap\b"]),
    # "If I box at lap 10 for hards, how am I looking?", "What does the strategy look like if I
    # change for hards at lap seven?"
    ("what_if_lap", [r"\b(box|pit|stop|change|switch|go)\w*\b.*\blap (\d+|" + _NUM_WORDS + r")\b",
                     r"\blap (\d+|" + _NUM_WORDS + r")\b.*\b(box|pit|stop|change|switch)\w*\b",
                     r"\bhit lap (\d+|" + _NUM_WORDS + r")\b"]),
    # "Why not hards?", "Why wouldn't I just go for hards?", "Should I pit for hards instead of
    # softs?", "If I'm six seconds up, should I go hards?"
    # 9 Nov additions
    ("teammate", [r"\bteam ?mates?\b", r"\bother (car|driver) in (my|our) team\b"]),
    ("hype", [r"\bhype\b", r"\bpump (me|us) up\b", r"\bpep talk\b", r"\bmotivat\w*\b",
              r"\bgee (me|us) up\b", r"\bget (me|us) going\b"]),
    ("pit_stop_time", [r"\bhow long (did|was|were) (the |my |that |our )?(pit ?stop|stop|stationary)",
                       r"\b(pit ?stop|stop|stationary) time\b", r"\bhow (quick|fast|slow) was (the |my |that )?(pit ?stop|stop)\b"]),
    ("why_not", [r"\bwhy (not|wouldn't (i|we)|don't (i|we))\b.*\b(hards?|mediums?|softs?)\b",
                 r"\b(hards?|mediums?|softs?) instead\b", r"\binstead of (the )?(hards?|mediums?|softs?)\b",
                 r"\bshould (i|we) (go|switch|change|run|use|take|put on)( to| on| onto| for)?( the)? "
                 r"(hards?|mediums?|softs?)\b", r"\bwhat about (the )?(hards?|mediums?|softs?)\b",
                 # 9 Nov (Suzuka): all three got "Say again" or the plan without the hards
                 r"\b(what do you think|thoughts|how about)( about| of| on)? (the )?(hards?|mediums?|softs?)\b",
                 r"\bhow much (slower|quicker|faster|longer) (would|will|are|is) (the )?(hards?|mediums?|softs?)\b",
                 r"\b(run|do|give me) (a |the )?(strategy )?(sim\w*|simulation)\b(?!.*\bnow\b)"
                 r".*\b(hards?|mediums?|softs?)\b"]),
    # "Will hards make it to the end?" (a new set of them, not the fitted ones); "What will they
    # be by the end?", "How will they be at the end of the second stint?" (the plan's stints)
    ("why", [r"^why\b(?!.*\b(hards?|mediums?|softs?)\b)", r"\bwhy (is that|that|this|the plan|are we|the change|"
             r"did (we|you|it) change)\b", r"\bexplain (that|the plan|the strategy|why)\b"]),
    ("sure", [r"^(are you|you) sure\W*$", r"\bare you sure (about )?(that|this|the plan|it)\b",
              r"\bhow (sure|confident) are you\b", r"\bis (that|it|this|the plan) safe\b",
              r"\bhow risky\b", r"\byou sure (you )?like this (strategy|plan)\b"]),
    ("compound_to_end", [r"\b(hards?|mediums?|softs?)\b.*\b(make it|last|hold up|get) (to )?the (end|finish|flag)\b",
                         r"\bwill (the )?(hards?|mediums?|softs?) (last|make it|hold)\b"]),
    ("stints_end", [r"\b(what|how) will (they|these|the tyres?|my tyres?) (be|look)\b",
                    r"\bend of the (first |second |next |last |final |third )?stint\b"]),
    # "Has Albon pitted?" (also heard "As Albon pitted?")
    # 12 Nov (from the real radio): penalties, and what a rival is doing.
    ("penalties", [r"\bpenalt(y|ies)\b", r"\b(track limits?|warnings?)\b.*\b(how many|do i have|count)\b",
                   r"\bhow many (track limits?|warnings|strikes)\b", r"\bany warnings\b"]),
    # 11 Nov Miami: "Is this an undercut opportunity?", "Undercut Kimi?" (heard "Lie undercut,
    # Kimmy").
    ("undercut", [r"\bunder ?cut\b"]),
    ("rival", [r"\bhow('s| is| are)\b.*" + _DRIVER + r".*\b(looking|doing|going|getting on)\b",
               r"\bhow('s| is)\b.*\b(car|guy) (ahead|in front|behind)\b.*\b(looking|doing|going)\b",
               r"\bwhat tyres? (is|are|has|have) " + _DRIVER + r"\b",
               r"\bwhat (is|are) " + _DRIVER + r" on\b",
               r"\bwhat tyres? (is|are) the (car|guy) (ahead|in front|behind)\b",
               r"\b(intel|info|anything) on " + _DRIVER + r"\b",
               r"\bhow old are (his|her|their|" + _DRIVER[3:-3] + r"'s) tyres\b",
               # 11 Nov Miami: "Do you think Ocon will pit?", "How am I looking to Kimi?",
               # "What about this battle with Kimmy?", "Progress to Kimmy." all got "Say again".
               _DRIVER + r".*\b(will|gonna|going to|planning to|need to|have to|due to)\b.*"
               r"\b(pit|box|stop)", r"\b(will|is|does)\b " + _DRIVER + r".*\b(pit|box|stop)",
               r"\bhow (am i|are we)\b.*\b(looking|doing|going)\b.*\b(to|at|against|on|with|"
               r"versus|vs|compared to)\b.*" + _DRIVER,
               r"\b(battle|fight|scrap|progress|dice)\b.*" + _DRIVER,
               r"\bwhat about " + _DRIVER + r"\b"]),
    ("driver_pitted", [r"\b(has|did|is|have) " + _DRIVER + r" (pitted|pit|boxed|box|stopped|been in)\b",
                       r"\b" + _DRIVER + r" (pitted|boxed|stopped)\b"]),
    # "Can I catch the leader?", "Will I catch Norris?"
    ("catch", [r"\b(can|could|will|would) (i|we) catch\b", r"\bcatch (the leader|him|her|them)\b"]),
    # "How did sector one look relative to Lewis?", "What was the sector two differential to my
    # best?"
    ("sector", [r"\bsector (one|two|three|1|2|3)\b"]),
    # "How was that last couple turns?"
    ("last_corners", [r"\b(last|those|these|final) (couple|two|few|2|3)( of)? (turns|corners)\b",
                      r"\b(last|final) (turns|corners)\b"]),
    # "You say off or up?"
    ("clarify", [r"\b(off|up) or (off|up|down)\b", r"\b(did you|you) say (off|up|down|quicker|slower|"
                 r"gaining|losing)\b", r"\bwas that (off|up|quicker|slower)\b"]),
    ("tyres_to_end", [r"\b(hold up|last|make it|get) (to|until|till) the (end|finish|flag)\b",
                      r"\bmake the (end|finish|flag)\b", r"\bget over \d+",
                      r"\bwill (these|the|my) tyres? (last|make it|hold)\b",
                      r"\b(are|is) (these|the|my) tyres? (going to|gonna) (last|make it|hold)\b",
                      # 9 Nov: "How's this tyre? We're looking for the rest of the race."
                      r"\btyres?\b.*\b(rest of the race|the distance)\b",
                      r"\b(rest of the race|the distance)\b.*\btyres?\b"]),
    # 29 Oct: "How are we looking right now?"
    ("status", [r"\bhow (are|is) (we|it|things|everything) (looking|going)\b",
                r"\bhow('s| is) (it|everything) (looking|going)\b", r"\bstatus( update)?\b",
                r"\bgive me an? update\b", r"\bsitrep\b", r"\bwhere are we at\b"]),
    # 29 Oct Imola: "Are we boxing now?"
    ("boxing_now", [r"\bare (we|you|i) (boxing|pitting|stopping|coming in)\b",
                    r"\b(am i|are we) (boxing|pitting) this lap\b", r"\bis it this lap\b"]),
    # When/which lap to stop is the plan, not "what if I box now" (28 Oct Melbourne: "when
    # should I pit?", "which lap should I pit?", "where should I pit?").
    ("plan", [r"\b(when|which lap|what lap)\b.*\b(pit|box|stop)\b",
              # "When am I pitting?" (heard "What am I pitting?", Brazil 7 Nov)
              r"\b(when|what) (am|are) (i|we) (pitting|boxing|stopping)\b",
              r"\bwhen\b.*\b(pitting|boxing|stopping)\b",
              r"\bwhere should (i|we) (pit|box|stop)\b",
              r"\b(do|will) (i|we) (have|need) to (stop|pit|box)( again)?\b",
              r"\b(what|which) tyres? should (i|we) (pit|box|stop|go) (for|on|onto)\b",
              r"\b(what|which) tyres? should (i|we) be on\b",
              r"\bwe('re| are) on (the )?(hards|mediums|softs|new tyres|fresh tyres)\b",
              r"\bpit strategy\b"]),
    # "What if I go a lap later?", "two laps earlier?", "what if I extend?"
    ("box_shift", [r"\b(a|one|two|three|\d) laps? (later|earlier|longer|sooner)\b",
                   r"\bwhat if i (go|box|pit|stop) (later|earlier|sooner)\b",
                   r"\bwhat if (i|we) extend\b", r"\b(box|pit|stop) (later|earlier)\b"]),
    ("box_now", [r"\b(you )?sure (we|i) (shouldn't|should not|don't|do not) (pit|box|stop)\b",
                 r"^box$", r"\bwhat happens if (i|we) (pit|box|stop)\b",
                 r"\b(run|do) (the |a )?(simulation|sim|numbers)\b.*\b(hards?|mediums?|softs?|now)\b",
                 r"\b(pit|box|stop) for (the )?(hards?|mediums?|softs?)\b",r"\bwhat if i (box|pit|stop)\b", r"\b(box|pit|stop)(ping)? (now|this lap)\b",
                 r"\bshould i (box|pit|stop)\b", r"\bif i (box|pit|stop) now\b"]),
    # 2 Nov: "How are the tyres looking? Relative to the strategy?" is about the tyres.
    ("tyres", [r"^how (are|is) (the|my|these) tyres?\b"]),
    # 2 Nov: "Be definitive with the strategy. What should I do?"
    ("plan", [r"\bplan\b", r"\bstrategy\b", r"\bbe definitive\b", r"\bwhat should (i|we) do\b", r"\bwhen (do|should|will) (i|we) (box|pit|stop)\b",
              r"\bwhen('s| is) (the|my|our) (stop|pit ?stop|box)\b", r"\bhow many stops\b",
              r"\bpit window\b", r"\bwhich tyres? next\b", r"\bstrat\b"]),
    # A named driver: "where am I gaining on Lando?", "gap to Norris?" (27 Oct race)
    ("gap_driver", [_DRIVER + r".*\b(gap|how far|interval)\b",
                    r"\b(gap|how far|interval)\b.*" + _DRIVER]),
    # 9 Nov: "What was his last lap relative to mine?" after an answer naming Verstappen.
    ("compare_driver", [r"\b(his|her|their) (last )?(lap|laps|times?|pace)\b.*\b(mine|me|my)\b",
                        r"\b(relative|compared) to (mine|me|my)\b.*\b(his|her|their)\b"]),
    ("compare_driver", [_DRIVER + r".*\b(gain|gaining|losing|lose|quicker|faster|slower|pace|"
                        r"compare|catching|time|speed|winning|beating)\b",
                        r"\b(gain|gaining|losing|lose|quicker|faster|slower|pace|compare|"
                        r"catching|time|speed|versus|vs|against|winning|beating)\b.*" + _DRIVER]),
    # A corner: "how was that exit?" (heard as "how does that exit", 27 Oct race), "turn four?"
    ("corner", [r"\bhow (was|did|does|is|'s) (that|the|my) (exit|corner|turn|entry|braking|"
                r"apex|chicane|hairpin|line)\b",
                r"\b(that|the last) (exit|corner|entry|braking zone|chicane|hairpin)\b",
                r"\bhow (was|did|does|is) " + _TURN[2:], _TURN]),
    ("gaining_time", [r"\bwhere (am i|i'm|are we) (gaining|quicker|faster|good)\b",
                      r"\bwhere('s| is) my (strength|best)\b", r"\bgaining time\b"]),
    # 28-29 Oct: "Is there a corner that I'm losing a lot of speed?", "Am I losing speed in one
    # particular corner?", "How am I doing through some of these corners?", "Give me some coaching."
    ("losing_time", [r"\bwhere (am i|i'm|are we) (losing|slow)\b", r"\blosing time\b",
                     r"\blosing (a lot of |much )?speed\b", r"\bparticular corner\b",
                     r"\bhow am i doing (through|in|on) (some of )?(these|the|my) corners\b",
                     r"\b(give me (some )?|any )coaching\b", r"\bcoach me\b",
                     r"\bwhich corner\b", r"\bwhere can i (gain|improve|find time|be quicker)\b",
                     r"\bwhere('s| is) the time\b", r"\bimprove (my )?pace\b"]),
    ("damage", [r"\bdamage\b", r"\bwing\b", r"\bis the car (ok|okay|alright|all right)\b"]),
    # 29 Oct: "How many laps have I done on these tyres?"
    ("tyre_age", [r"\bhow many laps\b.*\b(on|have) (these|this|the|my) tyres?\b",
                  r"\bhow old are (the|these|my) tyres\b", r"\btyre age\b"]),
    # 29 Oct: "Who's pitted in front of me?"
    ("who_pitted", [r"\bwho('s| has|s)? (pitted|stopped|boxed)\b",
                    r"\bhas (the car (ahead|in front|behind)|anyone|anybody) (pitted|stopped)\b"]),
    ("tyres", [r"\btyres?\b", r"\bwear\b", r"\brubber\b", r"\bgrip\b",
               r"\bfronts\b", r"\brears\b"]),
    ("best_lap", [r"\b(best|fastest|quickest) lap\b", r"\bpersonal best\b"]),
    ("gap_leader", [r"\b(gap|how far|interval)( back)? (to|behind|from|off) (the )?lead(er)?\b",
                    r"\bwho('s| is) (leading|in the lead|p ?one)\b"]),
    ("gap_behind", [r"\bgap behind\b", r"\bbehind( me)?\b", r"\bcar behind\b",
                    r"\bwho('s| is) behind\b"]),
    ("gap_ahead", [r"\bgap( ahead| in front| to the car)?\b", r"\bahead\b", r"\bin front\b",
                   r"\bwho('s| is) (ahead|in front)\b", r"\binterval\b"]),
    ("lap", [r"\bhow was (that|the|my) lap\b", r"\blast lap\b", r"\blap time\b",
             r"\bhow('s| is) (my|the) pace\b", r"\bpace\b", r"\bdelta\b"]),
    ("fuel", [r"\bfuel\b", r"\bgas\b", r"\blift and coast\b"]),
    ("weather", [r"\bweather\b", r"\brain(ing)?\b", r"\btrack temp", r"\btemperature\b"]),
    ("laps_left", [r"\bhow many laps\b", r"\blaps (left|to go|remaining)\b", r"\bwhat lap\b"]),
    ("position", [r"\bposition\b", r"\bwhat place\b", r"\bp ?what\b",
                  r"\bwhere am i( running| placed| in the race)?$"]),
]

_FIXES = [
    (r"\btire", "tyre"), (r"\btyre's\b", "tyres"), (r"\bboxx\b", "box"), (r"\bpitt\b", "pit"),
    (r"\bfor the car\b", "to the car"), (r"\bp1\b", "p one"),
    (r"\bhearts?\b", "hards"), (r"\breal hards\b", "will hards"),   # 3 Nov mishearings
]


def normalise(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    for pat, rep in _FIXES:
        t = re.sub(pat, rep, t)
    return re.sub(r"\s+", " ", t).strip()


def match(text: str) -> Intent:
    t = normalise(text)
    for name, patterns in RULES:
        if any(re.search(p, t) for p in patterns):
            return Intent(name, t)
    return Intent("unknown", t)


_LOG_TRIGGER = re.compile(
    r"^\W*(?:(?:please|can you|could you)\s+)?(?:log|lock|flag|note|mark|add|put)(?:\s+down)?\b"
    r"(?:\s+(?:the\s+following|the\s+last|the\s+previous|this|that|it|my|last|previous|a|the))?"
    r"(?:\s+(?:question|one|request|call|for\s+later))?"
    r"(?:\s+(?:to|in|on)\s+(?:the\s+|my\s+)?(?:feedback|log|list)(?:\s+log)?)?"
    r"(?:\s+please)?\s*[:,.;!?\-\u2013\u2014]*\s*", re.I)


def log_text(text: str) -> str:
    """What to log from "log the following question: when should I pit?" (-> "when should I
    pit?", as heard). "" when nothing but the command was said ("log that"): the question is
    then the one asked just before (or the next one)."""
    rest = _LOG_TRIGGER.sub("", text, count=1).strip()
    rest = re.sub(r"^(please|thanks|thank you)\W*$", "", rest, flags=re.I).strip()
    return rest if len(re.findall(r"[a-z0-9']+", rest, re.I)) >= 3 else ""


def lap_in(text: str) -> int | None:
    """'box at lap 10' / 'change at lap seven' -> the lap number said."""
    t = normalise(text)
    m = re.search(r"\blap (\d+)\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\blap (?:(" + "|".join(_TENS_WORDS) + r")[ -]?)?(" + "|".join(_NUMBER_WORDS)
                  + r")?\b", t)
    if m and (m.group(1) or m.group(2)):
        return _TENS_WORDS.get(m.group(1) or "", 0) + _NUMBER_WORDS.get(m.group(2) or "", 0) or None
    return None


def sector_in(text: str) -> int | None:
    m = re.search(r"\bsector (one|two|three|1|2|3)\b", normalise(text))
    return {"one": 1, "two": 2, "three": 3}.get(m.group(1), None) or int(m.group(1)) if m else None


def track_in(text: str) -> int | None:
    """'strategy for Suzuka' -> 13 (the game's track id)."""
    m = re.search(r"\b" + _TRACK + r"\b", normalise(text))
    return TRACK_WORDS[m.group(1)] if m else None


_NUMBER_WORDS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS_WORDS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
               "eighty": 80}


def laps_in(text: str) -> int | None:
    """'plan for 20 laps' / 'twenty two laps' -> the race length said, if any."""
    t = normalise(text)
    m = re.search(r"\b(\d+) laps?\b", t)
    if m:
        n = int(m.group(1))
        return n if 1 <= n <= 99 else None
    m = re.search(r"\b(?:(" + "|".join(_TENS_WORDS) + r")[ -]?)?(" + "|".join(_NUMBER_WORDS)
                  + r")? laps?\b", t)
    if m and (m.group(1) or m.group(2)):
        n = _TENS_WORDS.get(m.group(1) or "", 0) + _NUMBER_WORDS.get(m.group(2) or "", 0)
        return n or None
    return None


def driver_in(text: str, names: list[str | None]) -> int | None:
    """The car index of a driver named in the text: a surname as the game sends it, or a first
    name from FIRST_NAMES. None if no driver on the grid is named."""
    t = normalise(text)
    words = set(t.split())
    wanted = {FIRST_NAMES[w] for w in words if w in FIRST_NAMES} | words
    for i, n in enumerate(names):
        if n and any(part.lower() in wanted for part in n.split()):
            return i
    return None


_NUMBERS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}


def turn_in(text: str) -> int | None:
    """'how was turn four' -> 4."""
    m = re.search(_TURN, normalise(text))
    if not m:
        return None
    w = m.group(1)
    return int(w) if w.isdigit() else _NUMBERS[w]
