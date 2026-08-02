"""
Qontext — a quipu-inspired conversational memory.

Instead of resending a whole transcript, keep the facts as *knots*: short,
self-contained, third-person statements, and send only the ones relevant to
the current prompt. Small models answer better from the dense pack than from
the full transcript, because attention is scarcer than context.

    from qontext_memory import QontextMemory

    mem = QontextMemory.load("qontext.qx")      # or QontextMemory()
    mem.observe("user", "People call me Marta and I work as a nurse.")
    mem.observe("assistant", "Nice to meet you, Marta.")

    facts = mem.pack("What does the user do for work?", budget=300)
    # -> 'the user works as a nurse'
    mem.save("qontext.qx")

API: observe · add · pack · entries · forget · clear · explain · stats ·
serialize / deserialize · save / load.

Standard library only, single file, no side effects on import. Thread-safe.
Python 3.8+.

Design rules, each paid for with a failed run:
  1. A knot must name its subject — "I" is unanswerable once cut from the cord.
  2. The payload is the point — never trim the name, day, place or number out.
  3. Fewer, better knots — density comes from selection, not truncation.

Vocabulary tables (MARKERS, OPENERS, STOP, SYNONYMS) are the tuning surface.
All of them are normalised through _stem() at import, because the matching
code stems the text it compares against — an unstemmed table entry is a dead
entry that silently never fires.
"""

import json
import math
import os
import pathlib
import re
import tempfile
import threading
import time

__version__ = "1.0.0"
__all__ = ["QontextMemory", "QuipuMemory", "extract"]

# ---------------------------------------------------------------- tunables

DEFAULT_BUDGET = 300        # characters of pack() output
DEFAULT_MAX_ENTRIES = 500   # hard ceiling on stored knots
MAX_ENTRY_CHARS = 120       # a knot longer than this is not a knot
MIN_SENTENCE = 8            # shorter than this carries nothing
SUPERSEDE_SIMILARITY = 0.6  # word overlap above which a knot restates another
RELEVANCE_FLOOR = 0.5       # keep knots scoring this fraction of the best match
FLOOR_MAX_KNOTS = 200       # above this the floor is dropped (see pack)
LENGTH_NORM = 0.5           # how strongly to prefer shorter knots (0 = off)
# Importance decides what survives eviction, not what gets retrieved. Letting
# it sway ranking was measured: at 0.5 it bought one fact on a roleplay log
# and cost three on the stress conversation, because an important knot is not
# the same thing as a relevant one. Left as a knob, off by default.
IMPORTANCE_RANK = 0.0       # how strongly importance sways ranking (0 = off)
# Share of the pack filled by importance instead of by the query. Zero by
# default: in assistant chat the query is informative and reserving space
# costs recall. In roleplay it is the opposite — measured on real logs, the
# facts a reply drew on were carried 5.4% of the time at 1200 characters with
# no reserve, and 9.3% with half the pack reserved. Set 0.5 for roleplay.
# A woven association counts this much of a real word match. The weave hook
# (qontext_weave.py) is wired but unproven: on 30k tokens of personal text it
# made retrieval slightly worse, and only became harmless when filtered so
# hard that almost nothing came through. It needs orders of magnitude more
# text before it earns its place. Pass weave= only if you have that.
ASSOCIATION_WEIGHT = 0.5
PACK_RESERVE = 0.0
REFERENCE_LENGTH = 40       # knots under this length are not penalised
# When the query names its subject ("where does the user track tasks"), a knot
# about somebody else is not a weak match — it answers a different question.
# Measured on a long conversation seeded with facts about other people, four
# knots tied exactly and recency handed the pack to the decoys. This damps
# knots that mention no first-party subject at all — including those where a
# chained possessive moves the subject elsewhere: "the user's neighbour's dog"
# is the neighbour's dog, however many times it says "user".
SUBJECT_FOCUS = 0.4         # how much to damp knots about a different subject

# Hidden index terms: words from the turn a knot came from, used for matching
# and never shown to the model.
#
# Measured on real logs: of the facts a reply needed and retrieval could not
# reach, 44.5% had a bridging word in the very turn that produced the knot.
# Extraction discarded it. Three semantic bridges rescue ~7% of the same
# population, so this is a six-fold larger opportunity.
#
# It does not trade against design rules 2 and 3, because index terms are not
# display text: the pack stays short while the knot becomes reachable through
# words nobody has to read. Storage grows, the prompt does not.
#
# 0 disables the mechanism entirely.
INDEX_TERMS = 0             # hidden terms kept per knot
INDEX_WEIGHT = 0.35         # their score relative to a word in the knot itself
_CHAINED_POSSESSIVE = re.compile(r"\bthe (?:user|team)'s \w+'s\b")

# --------------------------------------------------------------------------
# stemming (defined first: the tables below are normalised through it)
# --------------------------------------------------------------------------


def _stem(w):
    """Suffix stripper with floors, so short words survive intact.

    Without the floors 'sing' stems to 's', 'noted' to 'not' and 'using' to
    'us' — degenerate stems that collide with words appearing in every other
    sentence, which silently turns the whole marker table into a yes-machine.
    Correct English stemming is not the goal; stability is. The same function
    normalises both the tables and the text they are matched against, so
    'based' -> 'based' is fine as long as it is always 'based'.
    """
    for suf in ("ing", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[: -len(suf)]
            if w[-1] == w[-2]:      # formatt -> format, programm -> program
                w = w[:-1]
            return w
    if w.endswith("s") and len(w) >= 4 and not w.endswith(("ss", "us")):
        w = w[:-1]
    return w


def _stems(*groups):
    """Flatten space-separated word groups into a set of stems."""
    out = set()
    for g in groups:
        for w in g.split():
            out.add(_stem(w))
    return out


# --------------------------------------------------------------------------
# MARKERS — sentences that carry facts tend to contain one of these signals.
# Grouped by the kind of fact they signal; groups are merged into one set.
# --------------------------------------------------------------------------

MARKERS = _stems(
    # identity, naming, personal detail
    """name named names call called calls nickname nicknamed alias address
       addressed known goes signed signature initials pronoun pronouns
       birthday born birth age""",
    # location and residence
    """live lives living based from located situated reside residing resides
       moved moving relocated hometown neighbourhood neighborhood city town
       village country region timezone tz utc gmt cet cest eet est pst cst""",
    # occupation, employer, study
    """work works working job jobs role roles title position profession
       occupation career employed employer employ company firm business
       startup agency freelance freelancing contractor consultant intern
       student studies studying study teach teaches teaching""",
    # people around them
    """team teams colleague colleagues coworker manager managers boss lead
       leads leader supervisor mentor report reports client clients customer
       vendor supplier competitor competitors stakeholder department""",
    # languages, skills, what they build
    """code codes coding program programs programming script scripts
       scripting write writes written writing build builds building built
       develop developing developer stack framework frameworks library
       language languages fluent speak speaks spoken learn learns learning
       skill skills experience""",
    # tools, systems, infrastructure
    """use uses using editor ide terminal shell tool tools toolchain
       database databases db sql store stores storage cache queue host
       hosted hosting deploy deploys deployed deployment server servers
       cloud environment staging production prod sandbox repo repository
       branch pipeline ci cd runner laptop desktop hardware gpu cpu vram ram
       memory disk driver model models version runs run running platform
       service api endpoint""",
    # process, tracking, admin
    """track tracks tracking log logs logged ticket tickets issue issues
       board backlog docs documentation wiki notes design files rotation
       oncall on-call incident incidents postmortem invoice invoices billing
       budget budgets cost costs price pricing paid pay payment spend
       expense approval approve sign-off""",
    # time, schedule, commitments
    # (deliberately no bare time nouns — 'day', 'hour', 'morning', 'today'
    #  appear in pure chatter far more often than in facts; real dates and
    #  weekdays are caught by the DAYS/MONTHS/digit payload test instead)
    """meeting meetings meet standup stand-up sync syncs retro retrospective
       kickoff demo demos review session appointment schedule scheduled
       reschedule calendar slot due deadline deadlines ship ships shipping
       shipped launch launches release releases deliver delivery submit
       submitted weekly daily monthly quarterly biweekly fortnightly
       annually recurring o'clock availability available unavailable
       holiday vacation trip trips flight flights flying travel travelling
       conference hotel visiting visit""",
    # projects and goals
    """project projects repo codename codenamed product feature features
       milestone milestones sprint epic goal goals objective objectives
       target targets aim aims plan plans planning priority priorities
       roadmap scope deliverable outcome thesis dissertation research""",
    # preferences, communication style
    """prefer prefers preferred preference preferences like likes dislike
       dislikes hate hates rather please answer answers answering respond
       responds response reply replies format formats formatted bullet
       bullets list lists style styles tone voice brief briefly short
       shorter concise terse verbose detail details detailed explanation
       explanations summary summarise summarize emoji emojis jargon formal
       informal casual polite blunt direct units metric imperial celsius
       fahrenheit currency euro euros dollar dollars pound always never
       avoid avoids skip skips keep keeps stop""",
    # constraints, health, accessibility, diet
    """allergic allergy allergies intolerant intolerance diet dietary
       vegetarian vegan pescatarian halal kosher gluten lactose dairy nut
       nuts medication condition chronic disability accessibility dyslexia
       dyslexic adhd autistic autism neurodivergent overwhelmed sensory
       wheelchair blind deaf impaired limit limited constraint constrained
       unable capacity""",
    # relationships and pets
    """wife husband partner spouse girlfriend boyfriend fiance son daughter
       kid kids child children baby brother sister sibling siblings mother
       father mom mum dad parents grandmother grandfather family relative
       friend friends roommate housemate dog dogs cat cats rabbit hamster
       pet pets horse bird""",
    # possessions, transport, place
    """car cars drive drives driving bike bicycle scooter commute apartment
       rent rents renting phone laptop""",
    # contact and accounts
    """email e-mail mail phone number slack discord telegram whatsapp
       contact handle username account login profile url link site website
       domain channel""",
    # explicit memory signals ('note', 'heads up', 'important' are openers,
    # not markers — they introduce a fact rather than being one)
    """remember remembers forget forgot""",
    # hobbies and interests (nouns only — 'play' and 'train' as verbs fire on
    # chatter about football and commutes; the real hobby facts carry a name)
    """hobby hobbies band gig gigs music instrument guitar bass drums piano
       sport sports gym workout climbing swimming cycling""",
)

# Payload signals: a bare number word carries a fact the digit test misses
# ("there are seven of us").
NUMBER_WORDS = {
    # 'one' is omitted: it is a pronoun far more often than a count
    "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty",
    "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "thousand",
    "million", "dozen",
}

DAYS = {"monday", "tuesday", "wednesday", "thursday", "friday",
        "saturday", "sunday", "weekday", "weekend",
        "mon", "tue", "tues", "wed", "thu", "thur", "thurs", "fri",
        "sat", "sun"}

MONTHS = {"january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december",
          "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
          "oct", "nov", "dec"}

# --------------------------------------------------------------------------
# OPENERS — filler that adds no meaning. Stripped from the front of a
# sentence, repeatedly ("Oh, by the way, also: ...").
# --------------------------------------------------------------------------

OPENERS = re.compile(
    r"^(?:"
    # interjections and greetings
    r"oh[,.!]? |ah[,.!]? |ugh[,.!]? |hmm+[,.!]? |uh[,.!]? |um[,.!]? |"
    r"well[,.!]? |right[,.!]? |ok(?:ay)?[,.!]? |alright[,.!]? |yeah[,.!]? |"
    r"yep[,.!]? |sure[,.!]? |look[,.!]? |listen[,.!]? |hey(?: there)?[,.!]? |"
    r"hi(?: there)?[,.!]? |hello[,.!]? |good (?:morning|afternoon|evening)[,.!]? |"
    r"morning[,.!]? |afternoon[,.!]? |evening[,.!]? |"
    # discourse glue
    r"also[,.:]? |and also[,.:]? |plus[,.:]? |anyway[,.:]? |anyhow[,.:]? |"
    r"besides[,.:]? |meanwhile[,.:]? |then[,.:]? |next[,.:]? |"
    r"by the way[,.:]? |btw[,.:]? |incidentally[,.:]? |"
    r"first(?:ly)?[,.:]? |second(?:ly)?[,.:]? |third(?:ly)?[,.:]? |"
    r"finally[,.:]? |lastly[,.:]? |one more thing[,.:]? |last thing[,.:]? |"
    r"another thing[,.:]? |one thing[,.:]? |"
    # framing and hedges
    r"honestly[,.:]? |actually[,.:]? |basically[,.:]? |obviously[,.:]? |"
    r"clearly[,.:]? |frankly[,.:]? |seriously[,.:]? |weirdly[,.:]? |"
    r"funnily enough[,.:]? |to be (?:clear|fair|honest)[,.:]? |"
    r"for what it'?s worth[,.:]? |fwiw[,.:]? |just so you know[,.:]? |"
    r"in case (?:it matters|you (?:need|were) wondering)[,.:]? |"
    r"if it helps[,.:]? |while i think of it[,.:]? |since we'?re here[,.:]? |"
    r"speaking of which[,.:]? |on that note[,.:]? |off topic[,.:]? |"
    r"side note[,.:]? |small thing[,.:]? |quick (?:one|note|intro)[^:.]*[,.:]? |"
    r"some background[,.:]? |for context[,.:]? |background[,.:]? |context[,.:]? |"
    # attention grabbers
    r"important(?:ly)?[,.:]? |also important[,.:]? |very important[,.:]? |"
    r"heads up[,.:]? |fyi[,.:]? |psa[,.:]? |nb[,.:]? |"
    r"reminder[,.:]? |update[,.:]? |worth noting[,.:]? |"
    r"before i forget[,.:]? |don'?t let me forget[,.:]? |"
    r"as i (?:said|mentioned)[,.:]? |like i said[,.:]? |"
    r"just fyi[,.:]? |just a heads up[,.:]? |"
    # politeness and softeners
    r"sorry[,.:]? |thanks[,.:]? |thank you[,.:]? |please note[,.:]? |"
    r"so[,.:]? |but[,.:]? |and[,.:]? |now[,.:]? "
    r")+",
    re.IGNORECASE)

# --------------------------------------------------------------------------
# STOP — words that carry no retrieval signal. Never add a word that a
# SYNONYM group depends on (from, call, work, code, write, ...).
# --------------------------------------------------------------------------

STOP = {
    # articles, pronouns, determiners
    "the", "a", "an", "this", "that", "these", "those", "there", "here",
    "i", "my", "mine", "me", "we", "our", "ours", "us", "you", "your",
    "yours", "he", "she", "it", "its", "they", "them", "their", "theirs",
    "him", "her", "his", "who", "whom", "whose", "which", "what", "when",
    "where", "why", "how", "user", "someone", "something", "anything",
    "everything", "nothing", "thing", "things", "stuff",
    # to be / to have / modals
    "is", "are", "was", "were", "be", "been", "being", "am",
    "has", "have", "had", "having", "do", "does", "did", "doing", "done",
    "will", "would", "shall", "should", "can", "could", "may", "might",
    "must", "let", "lets",
    # prepositions and conjunctions (NOT 'from': it is a location synonym)
    "to", "of", "and", "or", "but", "if", "as", "at", "by", "in", "on",
    "into", "onto", "over", "under", "with", "without", "about", "for",
    "than", "then", "so", "because", "since", "while", "during", "after",
    "before", "up", "down", "out", "off", "again", "still", "yet", "too",
    "also", "not", "no", "nor", "both", "each", "either", "neither",
    # vague quantity / degree
    "some", "any", "all", "most", "many", "much", "more", "less", "least",
    "few", "lot", "lots", "bit", "quite", "very", "really", "pretty",
    "just", "only", "even", "actually", "basically", "literally", "kind",
    "sort", "sorta", "kinda",
    # conversational filler
    "please", "thanks", "thank", "ok", "okay", "yes", "yeah", "yep", "no",
    "nope", "well", "oh", "ah", "hi", "hey", "hello", "sure", "right",
    "anyway", "though", "however", "maybe", "perhaps", "probably", "guess",
    "think", "know", "mean", "say", "said", "tell", "told", "get", "got",
    "getting", "go", "going", "gone", "come", "came", "give", "gave",
    "put", "look", "looking", "seem", "seems", "way",
    # contraction and possessive fragments: the word tokenizer splits
    # "user's" into ["user", "s"], and a bare "s" otherwise scores a match
    # against every third-person entry in the memory
    "s", "t", "d", "m", "re", "ve", "ll",
    "don", "doesn", "didn", "isn", "aren", "wasn", "won", "wouldn", "couldn",
    "shouldn", "haven", "hasn", "hadn", "ain",
}

# --------------------------------------------------------------------------
# SYNONYMS — question wording -> the words a statement would use instead.
# Keys and values are stemmed at import (see _norm_synonyms).
# --------------------------------------------------------------------------

_RAW_SYNONYMS = {
    # identity
    "name": "call called named names address addressed nickname known goes refer signed",
    "called": "name named call address known",
    "nickname": "name called call known goes",
    "birthday": "born birth birthday date",
    "age": "old born age years",
    # location
    "live": "living based from located situated reside resides home hometown city moved",
    "location": "live living based from located situated city town country",
    "city": "live living based situated located town",
    "country": "live living based from located",
    "from": "live living based hometown",
    "timezone": "tz utc gmt cet cest est pst zone timezone based hours",
    # occupation
    # No specific occupations here. "teacher" and "engineer" were once in this
    # group, from tuning against a conversation whose user was a teacher — and
    # the effect was that any knot about *anyone* with that job outranked the
    # answer. An occupation is payload, not a relation word.
    "job": "work works working role title position profession occupation career "
           "employed employer company freelance",
    "role": "job work works title position responsibility",
    "work": "job role employed employer company profession",
    "employer": "work works employed company firm business agency startup",
    "company": "employer work works employed firm business startup agency",
    "profession": "job work role occupation career",
    "study": "student studies studying thesis research university course",
    # skills and languages
    "language": "code coding program script scripts write written build built stack "
                "fluent speak spoken",
    "programming": "code coding program script build built write written stack",
    "code": "program script language build write written stack",
    "speak": "fluent language languages spoken speaks",
    # tools and systems
    "editor": "ide editor terminal environment tool",
    "database": "store stores storage db sql data table persistence",
    "deploy": "host hosted hosting deployment ship release server cloud runs",
    "ci": "pipeline build builds runner actions tests automation",
    "hosting": "host hosted deploy deployment cloud server",
    "tool": "tools use uses using software app",
    "model": "run runs running local llm weights gguf inference",
    "hardware": "machine laptop gpu cpu vram ram memory disk card",
    "vram": "gpu memory ram card graphics machine",
    # work objects
    "repo": "repository codename codenamed named called project product",
    "project": "repo repository codename codenamed named called product",
    "issue": "issues ticket tickets track tracking board bug bugs backlog",
    "task": "tasks track tracking board ticket issues backlog",
    "doc": "docs documentation wiki notes",
    "design": "designs files mockups wireframes",
    # people
    "team": "colleagues colleague coworkers people engineers members size",
    "manager": "boss lead leader supervisor reports approves",
    "supervisor": "professor advisor manager mentor supervisor",
    "client": "customer account contract clients",
    "competitor": "competitors rival rivals compete undercut",
    # schedule
    "meeting": "call calls standup sync retro kickoff demo session appointment schedule",
    "standup": "meeting sync daily morning",
    "demo": "meeting presentation show sprint",
    "retro": "retrospective meeting review",
    "kickoff": "meeting call start starts",
    "deadline": "due ship ships deliver delivery hard date submit submitted",
    "due": "deadline submit submitted pay paid before deliver date",
    "ship": "release launch deliver deploy beta date",
    "schedule": "calendar time slot meeting scheduled",
    "available": "availability free busy offline away hours",
    # money
    "budget": "cost costs price euro euros dollar spend money funding",
    "invoice": "invoices bill billing pay paid submitted finance",
    # style and preferences
    "format": "bullet bullets list style brief short concise answer respond reply prose",
    "formatted": "bullet bullets list format style brief short concise answer",
    "written": "bullet brief format explanation keep concise short style prose",
    "answer": "reply respond response format bullet style",
    "explanation": "explain explanations brief short detail concise",
    "tone": "style formal informal casual polite blunt voice",
    "unit": "units metric imperial celsius fahrenheit km kg",
    "preference": "prefer prefers like likes rather always never avoid",
    "emoji": "emojis emoji noisy avoid skip",
    # constraints and health
    "allergic": "allergy allergies intolerant reaction avoid",
    "allergy": "allergic allergies intolerant reaction",
    "diet": "dietary vegetarian vegan eat eats food allergic pescatarian",
    "dietary": "diet vegetarian vegan eat eats food allergic",
    "constraint": "limit limited cannot capacity constrained",
    # personal life
    "goal": "aim aims objective priority priorities plan plans finish achieve focus target",
    "hobby": "hobbies play plays playing band music sport read reads games",
    "pet": "dog cat rabbit hamster animal named called",
    "dog": "pet puppy named called",
    "cat": "pet kitten named called",
    "child": "children kid kids son daughter baby",
    "daughter": "child children kid kids girl",
    "son": "child children kid kids boy",
    "sibling": "brother sister siblings",
    "family": "wife husband partner spouse children kids parents",
    "travel": "trip trips flight flying fly conference visit visiting hotel",
    "hotel": "stay staying accommodation room booked",
    "contact": "email mail phone number slack handle username reach",
}


def _norm_synonyms(raw):
    out = {}
    for key, words in raw.items():
        out.setdefault(_stem(key), set()).update(_stems(words))
    return out


SYNONYMS = _norm_synonyms(_RAW_SYNONYMS)

# A high-precision subset of MARKERS, used only when deciding whether the
# tail of a sentence is worth keeping. The full marker list is deliberately
# greedy — good for "is this a fact at all", useless for "is this tail worth
# 40 characters", where nearly every clause would qualify and trimming would
# stop happening at all.
CORE_MARKERS = _stems(
    """name named call called codename codenamed live lives living based
       situated work works working job role employer company code coding
       program script write writes written build builds language deploy
       deploys database store stores repo repository project meeting
       standup demo retro kickoff deadline due ship ships deliver invoice
       budget prefer prefers preference answer answers respond reply format
       bullet style brief concise allergic allergy vegetarian vegan diet
       manager supervisor client competitor timezone goal thesis remember""")

# Verbs that need an -s when "I" becomes "the user".
_AGREE = (
    "work live code write prefer reply track build run use store deploy "
    "train play drive read speak teach study learn like love hate need "
    "want keep ship manage handle own rent commute travel fly sleep eat "
    "watch review test maintain wear bring"
).split()
_AGREE_RE = re.compile(r"\bthe (user|team) (%s)\b" % "|".join(_AGREE))


# --------------------------------------------------------------------------
# Topic frames — used only to decide whether one knot corrects another.
#
# The frame of a knot is what is left after removing its payload (the names,
# dates and numbers that a correction changes) and its filler. "the user's
# manager is Priya" and "the user's manager is Tomas now" both reduce to
# {manager}; the second corrects the first.
#
# The safety property is that everything not recognised stays in the frame as
# itself. "dog" and "cat", "daughter" and "brother", "manager" and
# "supervisor", "report" and "invoice" are different words, so they produce
# different frames and can never collapse into each other. Only two knots
# saying the *same thing about the same thing* reduce to the same frame.
# --------------------------------------------------------------------------

# Words that never define what a knot is about. Correction markers ("instead",
# "anymore"), vague time nouns, and generic change verbs — "the demo moved to
# Tuesday" is still about the demo.
FRAME_DROP = _stems(
    """instead anymore longer now nowadays currently lately recently today
       tomorrow tonight yesterday moment present time times day days week
       weeks month months year years hour hours morning afternoon evening
       night mostly usually always sometimes often rarely ever never
       actually really honestly basically apparently""",
    """move moved moving moves change changed changing changes switch
       switched switching swap swapped update updated updating correction
       correct sorry scratch ignore""",
    """thing things stuff bit lot lots kind sort""",
)

# The only merges allowed: different words for genuinely the same relation.
# Deliberately tiny. Every entry here is a chance to collapse two facts, so a
# word goes in only when saying it the other way means the same thing.
_TOPIC_GROUPS = {
    "TOPIC:name": "name named names call called calls nickname nicknamed",
    "TOPIC:location": "live lives living based situated located reside "
                      "resides residing hometown",
    "TOPIC:due": "due deadline deadlines",
    # Points on one scale, not different subjects: "I prefer long
    # explanations" and "I prefer short explanations" are the same preference
    # stated twice. Safe because the thing being described — explanations,
    # meetings, walks — stays in the frame and keeps them apart.
    "TOPIC:scale": "short shorter brief briefly concise terse long longer "
                   "lengthy verbose detailed",
}
TOPIC_VOCAB = {}
for _topic, _words_ in _TOPIC_GROUPS.items():
    for _stem_ in _stems(_words_):
        TOPIC_VOCAB[_stem_] = _topic


# --------------------------------------------------------------------------
# IMPORTANCE — how much it would cost to forget a knot.
#
# Nobody rates these by hand and no model is called: the weight is computed
# from signals the memory already has. What kind of fact it is (an identity
# outranks a piece of scenery), whether it carries a concrete payload,
# whether the speaker flagged it, and how often it has been restated or
# retrieved since.
#
# Scored 1-5, the same scale the .qx weave format uses, where 5 means losing
# it would break continuity.
# --------------------------------------------------------------------------

WEIGHT_5 = _stems(
    # who someone is, and who they are to each other: the facts every later
    # turn depends on
    """name named call called nickname alias born birthday age
       wife husband partner spouse fiance married daughter son child children
       brother sister sibling mother father parents family cousin
       allergic allergy intolerant vegetarian vegan diet dietary medication
       disability accessibility autistic adhd dyslexic""")

WEIGHT_4 = _stems(
    # standing instructions and stable circumstances
    """prefer prefers preference like likes dislike hate rather always never
       avoid format bullet style tone brief concise verbose units metric
       live lives living based situated located reside hometown timezone
       job role work works employer company profession studies student
       manager supervisor boss client competitor team colleague
       dog cat pet rabbit""")

WEIGHT_3 = _stems(
    # commitments: true until a date passes, then they are history
    """meeting standup demo retro kickoff sync deadline due ship ships
       deliver submit invoice budget appointment schedule scheduled
       flight trip travel conference hotel visit holiday""")

WEIGHT_2 = _stems(
    # the working furniture — replaceable, but worth knowing
    """repo repository project codename product editor database deploy
       server tool tools stack laptop phone car gpu cpu vram
       book band music gym hobby""")

# The user saying "remember this" outranks any category guess.
FLAGGED = _stems("""remember forget important critical crucial promise
                    promised must essential vital""")

IMPORTANCE_TIERS = ((WEIGHT_5, 5.0), (WEIGHT_4, 4.0),
                    (WEIGHT_3, 3.0), (WEIGHT_2, 2.0))


def _importance(text, reinforcements=0, hits=0):
    """1-5. What it would cost to forget this knot."""
    stems = {_stem(w) for w in re.findall(r"[\w-]+", text.lower())}
    weight = 1.0
    for vocabulary, tier in IMPORTANCE_TIERS:
        if stems & vocabulary:
            weight = tier
            break
    if not _has_payload(text):
        weight -= 0.5          # a category word with nothing concrete in it
    if stems & FLAGGED:
        weight += 1.0          # said to be worth remembering
    # restated or retrieved facts earn their place; the curve is flat on
    # purpose, so a fact mentioned twice does not outrank an identity
    weight += min(1.0, 0.4 * math.log1p(reinforcements + hits))
    return max(1.0, min(5.0, weight))


def _frame(text):
    """The topic frame of a knot: what it is about, minus what can change."""
    frame = set()
    # Same tokenizer as _words(): "user's" must split into "user" + "s" so
    # both fall to STOP. Keeping the apostrophe produces the token "user'",
    # which is in every third-person knot and would make every frame match
    # every other frame.
    words = re.findall(r"[\w-]+", text)
    for i, word in enumerate(words):
        low = word.lower().strip("-")
        if not low or low in STOP:
            continue
        if any(ch.isdigit() for ch in word):
            # A number is usually payload — "at 10:00", "due March 3rd" — and
            # a correction changes it. But a number hanging off a noun is an
            # identifier, not a value: "team 5" and "team 7" are different
            # teams, "flight KL1234" and "flight KL5678" different flights.
            # Those stay in the frame, bound to the noun they identify.
            prev = words[i - 1].lower() if i else ""
            if (prev and prev not in STOP and prev not in DAYS
                    and prev not in MONTHS
                    and not any(ch.isdigit() for ch in prev)):
                frame.add("ID:%s#%s" % (_stem(prev), low))
            elif not i:
                frame.add("ID:#%s" % low)           # sentence-initial number
            continue
        if low in DAYS or low in MONTHS or low in NUMBER_WORDS:
            continue
        if i > 0 and word[:1].isupper():            # payload: Marta, Antwerp
            continue
        stem = _stem(low)
        if stem in FRAME_DROP:
            continue
        if "-" in low and stem not in MARKERS:      # payload: heron-nest
            continue
        frame.add(TOPIC_VOCAB.get(stem, stem))
    return frozenset(frame)


def _identifiers(frame):
    """The identifier tokens of a frame ("team 5", "flight KL1234")."""
    if not frame:
        return frozenset()
    return frozenset(t for t in frame if t.startswith("ID:"))


# Sentence boundaries: ., !, ?, : and newlines — but never inside a quotation.
# Prose puts sentence punctuation inside quotes constantly ("Go home. Now,"
# she said), and splitting there produces knots that begin or end mid-quote
# with the payload on the other side of the cut. Measured on roleplay logs,
# 17% of all knots were broken this way.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?:])\s+|\n+")
_QUOTES = {'"': '"', "\u201c": "\u201d", "\u2018": "\u2019", "'": "'"}


def _quote_depth(text):
    """Where each character sits relative to quotation marks."""
    depth, inside, opener = [], False, None
    for char in text:
        if not inside and char in ('"', "\u201c", "\u2018"):
            inside, opener = True, char
            depth.append(1)
            continue
        if inside and char == _QUOTES.get(opener, '"'):
            inside = False
            depth.append(1)
            continue
        depth.append(1 if inside else 0)
    return depth


def _split_sentences(text):
    """Sentence split that treats a quotation as one unbreakable unit."""
    depth = _quote_depth(text)
    out, start = [], 0
    for match in _SENTENCE_SPLIT.finditer(text):
        cut = match.start()
        if cut < len(depth) and depth[cut]:
            continue                     # the punctuation is inside a quote
        piece = text[start:match.start()].strip()
        if piece:
            out.append(piece)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def _coerce(value):
    """Anything -> str, without raising. Deployment reality: callers pass
    None, ints and bytes, and a memory layer must not take the process down
    over it."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def _words(text):
    return [_stem(w) for w in re.findall(r"[\w-]+", _coerce(text).lower())
            if w not in STOP]


def _has_payload(fragment):
    """True if the fragment carries a concrete payload on its own."""
    words = set(re.findall(r"[\w'-]+", fragment.lower()))
    if words & DAYS or words & MONTHS or words & NUMBER_WORDS:
        return True
    if re.search(r"\d", fragment):
        return True
    if re.search(r"\w-\w", fragment):                    # heron-nest
        return True
    if re.search(r"(?<!^)(?<=[a-z,'\"] )[A-Z]{2,}\b", fragment):  # CET, VPN
        return True
    return bool(re.search(r"(?<!^)(?<=[a-z,'\"] )[A-Z][a-z]+", fragment))


def _is_fact(sentence, markers=MARKERS):
    if _has_payload(sentence):
        return True
    return bool({_stem(w) for w in re.findall(r"[\w-]+", sentence.lower())}
                & markers)


def _trim(sentence):
    """Drop trailing chatter after a comma or ' and ', but never drop the
    payload: only trim if what remains still carries it (or the original
    never had one, e.g. pure preference sentences)."""
    had_payload = _has_payload(sentence)
    depth = _quote_depth(sentence)
    for sep in (",", " and ", " while ", " though ", " but "):
        idx = sentence.find(sep)
        while idx != -1:
            if idx < len(depth) and depth[idx]:
                idx = sentence.find(sep, idx + 1)   # separator is inside a quote
                continue
            left = sentence[:idx].strip()
            rest = sentence[idx + len(sep):].strip()
            if len(left) >= 12 and (_has_payload(left) or not had_payload) \
                    and _is_fact(left) \
                    and not _is_fact(rest, CORE_MARKERS):
                return left
            idx = sentence.find(sep, idx + 1)
    return sentence


def _clauses(sentence):
    """Split into clauses at separators outside any quotation."""
    depth = _quote_depth(sentence)
    parts, start = [], 0
    for match in re.finditer(r",\s+|;\s+|\s+(?:and|but|so|then|while|though)\s+",
                             sentence):
        if match.start() < len(depth) and depth[match.start()]:
            continue
        piece = sentence[start:match.start()].strip()
        if piece:
            parts.append(piece)
        start = match.end()
    tail = sentence[start:].strip()
    if tail:
        parts.append(tail)
    return parts or [sentence]


def _fit(sentence, limit):
    """Bring a sentence under `limit` without cutting the payload out.

    Truncating at the limit is what design rule 2 forbids: prose sentences
    run long, and the name or the date is as likely to be at the end as the
    beginning. So instead of slicing, pick the clause span that carries the
    payload and fits. Only if nothing fits does it fall back to cutting, and
    then at a word boundary.
    """
    if len(sentence) <= limit:
        return sentence
    clauses = _clauses(sentence)
    best = None
    for start in range(len(clauses)):
        span = ""
        for end in range(start, len(clauses)):
            span = (span + ", " + clauses[end]).lstrip(", ") if span else clauses[end]
            if len(span) > limit:
                break
            if _has_payload(span) and (best is None or len(span) > len(best)):
                best = span
    if best:
        return best
    for clause in sorted(clauses, key=len, reverse=True):
        if len(clause) <= limit:
            return clause
    cut = sentence[:limit]
    return cut[:cut.rfind(" ")] if " " in cut else cut


def _attribute(knot, subject):
    """A bare quoted line is not a fact until someone is saying it."""
    stripped = knot.strip()
    if not stripped[:1] in ('"', "\u201c"):
        return knot
    if stripped.count('"') + stripped.count("\u201c") < 1:
        return knot
    return "%s says %s" % (subject, stripped)


def _third_person(sentence, subject="the user"):
    """'I work as a nurse' -> 'the user works as a nurse' so the model can
    connect pack entries to questions about the person concerned.

    With `subject="Carmine"` the same sentence becomes 'Carmine works as a
    nurse' — which is what makes a knot answerable once it is cut from the
    cord it was tied on.
    """
    possessive = subject + "'s"
    s = " " + sentence + " "
    s = re.sub(r"\bI'm\b", subject + " is", s)
    s = re.sub(r"\bI've\b", subject + " has", s)
    s = re.sub(r"\bI'll\b", subject + " will", s)
    # Irregular verbs, which the -s rule below cannot fix: "the user am not
    # allergic" and "the user have a dog" are what you get without these.
    s = re.sub(r"\bI am\b", subject + " is", s)
    s = re.sub(r"\bI have\b", subject + " has", s)
    s = re.sub(r"\bI do\b", subject + " does", s)
    s = re.sub(r"\bI go\b", subject + " goes", s)
    s = re.sub(r"\bwe are\b", "the team is", s, flags=re.IGNORECASE)
    s = re.sub(r"\bwe have\b", "the team has", s, flags=re.IGNORECASE)
    s = re.sub(r"\bI\b", subject, s)
    s = re.sub(r"\bmy\b", possessive, s, flags=re.IGNORECASE)
    s = re.sub(r"\bmine\b", possessive, s, flags=re.IGNORECASE)
    s = re.sub(r"\bme\b", subject, s)
    s = re.sub(r"\bwe're\b", "the team is", s, flags=re.IGNORECASE)
    s = re.sub(r"\bwe\b", "the team", s, flags=re.IGNORECASE)
    s = re.sub(r"\bour\b", "the team's", s, flags=re.IGNORECASE)
    s = _AGREE_RE.sub(lambda m: "the %s %ss" % (m.group(1), m.group(2)), s)
    if subject != "the user":
        # the -s agreement rule only knows "the user"/"the team"
        s = re.sub(r"\b(%s) (%s)\b" % (re.escape(subject), "|".join(_AGREE)),
                   lambda m: "%s %ss" % (m.group(1), m.group(2)), s)
    return re.sub(r"\s+", " ", s).strip()


def extract(text, subject="the user", drop_address=True):
    """Pull the fact-bearing knots out of one piece of text.

    `subject` is who "I" refers to. It defaults to the user, but in a
    multi-speaker setting — roleplay, group chats — every speaker needs their
    own, or all of their first-person narration collapses into one person and
    the names the memory exists to retrieve disappear.

    `drop_address` discards sentences aimed at the other party. True for
    assistant chat, where the other party is the assistant; False for
    roleplay, where it is a character with facts of their own.

    Returns a list of strings. Pure function, no state.
    """
    if not text:
        return []
    out = []
    for raw in _sentences(text):
        # Checked before trimming: the opener strip removes the question mark
        # along with the punctuation, and by then the evidence is gone.
        if _is_question(raw) or (drop_address and _addresses_other(raw)):
            continue
        sent = OPENERS.sub("", raw.strip() + " ").strip(" .,!?:")
        if len(sent) < MIN_SENTENCE or not _is_fact(sent):
            continue
        knot = _third_person(_trim(sent), subject)
        knot = _attribute(_fit(knot, MAX_ENTRY_CHARS), subject).strip()
        if len(knot) > MAX_ENTRY_CHARS:
            knot = _fit(knot, MAX_ENTRY_CHARS)
        if len(knot) >= MIN_SENTENCE:
            out.append(knot)
    return out


def _sentences(text):
    return _split_sentences(text)


# Not every sentence in a conversation is a claim about anybody.
#
# The extractor was built for a user *stating* things, and every benchmark
# conversation before real dialogue arrived consisted only of statements. Fed
# 800 turns of DailyDialog, 47% of stored knots turned out to be questions,
# remarks addressed to the other party, or fragments of neither:
#
#     "Do you think you'll ever get another pet"
#     "What was the party like last night, Jean"
#     "Don't believe what you see on TV"
#
# Half the stored characters, and every one of them competing for pack budget
# and ranking position against facts that are real.
_QUESTION = re.compile(r"\?\s*$")
_SECOND_PERSON = re.compile(r"\byou\b|\byour\b|\byours\b|\byou'(?:re|ll|ve|d)\b",
                            re.I)
_FIRST_PERSON = re.compile(r"\bI\b|\bI'(?:m|ve|ll|d)\b|\bmy\b|\bmine\b|\bme\b"
                           r"|\bwe\b|\bwe'(?:re|ve|ll)\b|\bour\b|\bus\b", re.I)


def _is_question(sentence):
    """A question is not a fact, whoever is asking it."""
    return bool(_QUESTION.search(sentence))


def _addresses_other(sentence):
    """A claim about the person being spoken to rather than the speaker.

    "You will have a good time in New York" says nothing about the user. A
    sentence naming both parties ("I told you about my sister") does, so the
    presence of any first-person reference keeps it.

    Deliberately off for roleplay: in a scene, "you are the vampire's sire" is
    a fact about the other character and worth keeping. In assistant chat the
    other party is the assistant, which has no facts of its own.
    """
    return bool(_SECOND_PERSON.search(sentence)
                and not _FIRST_PERSON.search(sentence))


class QontextMemory:
    """A knotted memory of a conversation.

    Feed it every turn with :meth:`observe`; ask it for the facts relevant to
    a prompt with :meth:`pack`. Everything is plain stdlib and the whole state
    is JSON-serialisable, so it persists to a single file.

        mem = QontextMemory()
        mem.observe("user", "People call me Marta and I work as a nurse.")
        mem.pack("What is the user's job?", budget=300)
        # 'the user works as a nurse'

    Parameters
    ----------
    max_entries:
        Hard ceiling on stored knots. When exceeded, the least valuable are
        evicted (oldest first among the least used), so a long-running agent
        cannot grow without bound.
    speakers:
        "user" (default) stores facts only from the user. "all" treats every
        speaker as their own subject, so "I drew my sword" from Carmine
        becomes "Carmine drew her sword" — needed for roleplay and group
        chats, where the other party is a character with facts of their own.
    """

    FORMAT_VERSION = 2

    def __init__(self, max_entries=DEFAULT_MAX_ENTRIES, speakers="user",
                 weave=None):
        if not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive int")
        if speakers not in ("user", "all"):
            raise ValueError("speakers must be 'user' or 'all'")
        self.max_entries = max_entries
        # "user": only the user states facts (assistant chatter is noise).
        # "all": every speaker is their own subject — roleplay, group chats,
        # anywhere the other party is a character with facts of their own.
        self.speakers = speakers
        # Optional WordWeave (qontext_weave.py): a persistent map of which
        # words hang together, used to reach knots the query has no word for.
        # External on purpose, so this file stays standalone.
        self.weave = weave
        self._knots = []          # list of dicts: text, seq, hits, ts, w
        self._seen = set()        # exact-duplicate guard
        self._index = {}          # token -> {seq, ...} inverted index
        self._by_frame = {}       # topic frame -> [record, ...]
        self._by_seq = {}         # seq -> record
        self._observed = 0
        self._seq = 0
        self._lock = threading.RLock()

    # ---------------------------------------------------------------- write

    def observe(self, speaker, text):
        """Watch one message. Only user text can introduce facts.

        Never raises on odd input: non-strings are coerced, empties ignored.
        Returns the knots added by this call.
        """
        text = _coerce(text)
        if not text:
            return []
        name = _coerce(speaker).strip()
        low = name.lower()
        with self._lock:
            self._observed += len(text)
            if low in ("user", "you", ""):
                subject = "the user"
            elif self.speakers == "user":
                return []      # assistant chatter never carries new facts
            else:
                subject = name   # roleplay: this speaker is their own subject
            return [k for k in extract(text, subject)
                    if self._add(k, context=text)]

    def add(self, knot):
        """Store a fact directly, bypassing extraction. Returns True if new."""
        knot = _coerce(knot).strip()[:MAX_ENTRY_CHARS]
        if len(knot) < MIN_SENTENCE:
            return False
        with self._lock:
            self._observed += len(knot)
            return self._add(knot)

    def _supersede(self, words, frame):
        """Drop knots the incoming one replaces. Caller holds the lock.

        People restate and correct facts constantly, and a memory that keeps
        every version hands the model a pile of near-contradictions. Two
        independent triggers, both deliberately conservative:

        1. Same topic frame — the knots say the same thing about the same
           thing, and only the payload differs. This catches reworded
           corrections ("my manager is Priya" -> "my manager is Tomas now").
        2. Near-identical wording — the same sentence said again with a few
           words changed, which no frame comparison is needed to spot.

        Anything else is treated as a genuinely different fact and kept. The
        cost of a wrong merge (a fact silently disappears) is far higher than
        the cost of a missed merge (two knots where one would do).

        Returns the highest retrieval count among the knots it removed, so
        the replacement can inherit it.
        """
        ids = _identifiers(frame)
        doomed = []
        seen = set()
        if frame:
            for k in self._by_frame.get(frame, ()):
                if id(k) not in seen:
                    seen.add(id(k))
                    doomed.append(k)
        if words:
            # |A ∩ B| / |A ∪ B| >= t implies |A ∩ B| >= t * |A|, so only knots
            # sharing that many words can possibly qualify.
            minimum = max(1, int(SUPERSEDE_SIMILARITY * len(words)))
            for k, shared in self._overlapping(words, minimum):
                # Veto: two knots about differently numbered things are never
                # the same fact, however similar the wording. "The manager of
                # team 5 is unavailable" and "...team 7..." share four words
                # in five and would otherwise merge.
                if k["ids"] != ids:
                    continue
                union = len(words) + len(k["w"]) - shared
                if union and shared / union >= SUPERSEDE_SIMILARITY:
                    if id(k) not in seen:
                        seen.add(id(k))
                        doomed.append(k)
        self._drop(doomed)
        return (max([k["hits"] for k in doomed] + [0]),
                sum(1 + k.get("reinforced", 0) for k in doomed))

    def _index_terms(self, knot_words, context):
        """Distinctive words from the source turn that the knot itself lost.

        Rarest first, by the document frequency the memory has seen so far —
        a word shared with hundreds of knots is not a useful entry point, and
        a word appearing here for the first time usually is.
        """
        if not INDEX_TERMS or not context:
            return frozenset()
        spare = [w for w in dict.fromkeys(_words(context)) if w not in knot_words]
        spare.sort(key=lambda t: len(self._index.get(t) or ()))
        return frozenset(spare[:INDEX_TERMS])

    def _add(self, knot, context=None):
        """Insert one knot. Caller holds the lock."""
        if knot in self._seen:
            return False
        words = frozenset(_words(knot))
        frame = _frame(knot)
        # A correction inherits the standing of what it replaces. Without
        # this, correcting a fact the user asks about constantly resets its
        # retrieval count to zero and hands it to the evictor.
        inherited, reinforced = self._supersede(words, frame)
        self._seq += 1
        record = {"text": knot, "seq": self._seq, "hits": inherited,
                  "ts": time.time(), "w": words, "f": frame,
                  "ids": _identifiers(frame), "reinforced": reinforced,
                  "imp": _importance(knot, reinforced, inherited),
                  # Matching only. Never rendered, never part of supersession:
                  # two knots that merely came from similar turns are not the
                  # same fact, and the 27/27 safety suite depends on that.
                  "idx": self._index_terms(words, context)}
        self._knots.append(record)
        self._seen.add(knot)
        self._index_add(record)
        self._evict()
        return True

    # The inverted index turns pack() from "score every knot" into "score the
    # knots that share a word with the query". At 10k knots that is the
    # difference between 48 ms and half a millisecond per turn.

    def _index_add(self, record):
        self._by_seq[record["seq"]] = record
        for token in record["w"] | record.get("idx", frozenset()):
            self._index.setdefault(token, set()).add(record["seq"])
        if record.get("f"):
            self._by_frame.setdefault(record["f"], []).append(record)

    def _index_remove(self, record):
        self._by_seq.pop(record["seq"], None)
        for token in record["w"] | record.get("idx", frozenset()):
            bucket = self._index.get(token)
            if bucket is not None:
                bucket.discard(record["seq"])
                if not bucket:
                    del self._index[token]
        frame = record.get("f")
        if frame and frame in self._by_frame:
            self._by_frame[frame] = [k for k in self._by_frame[frame]
                                     if k["seq"] != record["seq"]]
            if not self._by_frame[frame]:
                del self._by_frame[frame]

    def _drop(self, records):
        """Remove knots and their postings. Caller holds the lock."""
        doomed = {r["seq"] for r in records}
        if not doomed:
            return
        for record in records:
            self._index_remove(record)
        self._knots = [k for k in self._knots if k["seq"] not in doomed]
        self._seen = {k["text"] for k in self._knots}

    def _rarity(self, record):
        """How much this knot says that other knots do not.

        Mean inverse document frequency of its words. A knot whose vocabulary
        is shared with hundreds of others ("the weekly meeting ran long")
        scores near zero; one holding a name, a place or a codename scores
        high because nothing else in the memory contains those words.
        """
        if not record["w"]:
            return 0.0
        total = len(self._knots) or 1
        return sum(math.log(1.0 + total / (1.0 + len(self._index.get(t) or ())))
                   for t in record["w"]) / len(record["w"])

    def _evict(self):
        """Keep the memory bounded. Caller holds the lock.

        Least valuable first: never retrieved, then least distinctive, then
        oldest. Evicting purely by age loses the user's name on turn 3000 of
        a chatty session while keeping three thousand variations of "the
        meeting ran long" — the exact opposite of what a memory is for.
        """
        overflow = len(self._knots) - self.max_entries
        if overflow <= 0:
            return
        # Importance multiplies distinctiveness rather than outranking it.
        # Ordering by importance first was measured and was worse: chatter
        # that happens to say "meeting" inherits a commitment's weight and
        # survives, while "the user's editor is Neovim" — distinctive, but a
        # mere tool — gets evicted. Rarity is what tells a fact from filler;
        # importance says how much that fact is worth.
        # Importance nudges distinctiveness rather than outranking it. Two
        # stronger versions were measured and both were worse: ordering by
        # importance first, and multiplying by the raw 1-5 weight, each let
        # chatter that happens to say "meeting" survive while "Docs are in
        # Notion" — distinctive, but in no importance category — was evicted.
        # Rarity is what separates a fact from filler; importance only says
        # how much that fact is worth once it has qualified.
        ranked = sorted(self._knots,
                        key=lambda k: (k["hits"],
                                       self._rarity(k) * (1 + k.get("imp", 1.0) / 5.0),
                                       k["seq"]))
        self._drop(ranked[:overflow])

    def forget(self, pattern):
        """Drop every knot containing `pattern` (case-insensitive substring).

        Returns the number removed. Use for corrections and for scrubbing
        something the user asked not to be remembered.
        """
        needle = _coerce(pattern).lower()
        if not needle:
            return 0
        with self._lock:
            doomed = [k for k in self._knots if needle in k["text"].lower()]
            self._drop(doomed)
            return len(doomed)

    def clear(self):
        """Wipe all knots. Observation counters reset too."""
        with self._lock:
            self._knots, self._seen = [], set()
            self._index, self._by_frame, self._by_seq = {}, {}, {}
            self._observed, self._seq = 0, 0

    # ---------------------------------------------------------------- read

    def entries(self):
        """All stored knots, oldest first."""
        with self._lock:
            return [k["text"] for k in self._knots]

    def __len__(self):
        """Number of knots.

        Note this makes an *empty* memory falsy, as with any container. Test
        for existence with `is not None`, not `if mem:` — the latter silently
        skips a memory that simply has not learned anything yet.
        """
        with self._lock:
            return len(self._knots)

    def __contains__(self, text):
        with self._lock:
            return _coerce(text) in self._seen

    def __iter__(self):
        return iter(self.entries())

    def _expand(self, query):
        """Query words, their synonyms, and whatever the weave associates.

        Returns (all tokens, tokens that came only from association) so the
        caller can weight the second group lower — a thread the weave learned
        is weaker evidence than a word the user actually typed.
        """
        qwords = set(_words(query))
        expanded = set(qwords)
        for w in qwords:
            expanded |= SYNONYMS.get(w, set())
        associated = set()
        if self.weave is not None:
            raw = {w.lower() for w in re.findall(r"[\w-]+", _coerce(query))}
            for word in self.weave.expand(raw):
                stem = _stem(word)
                if stem not in expanded:
                    associated.add(stem)
            expanded |= associated
        return expanded, associated

    def _weights(self, expanded):
        """Inverse document frequency per query token.

        Without this every matched word counts the same, so "What is the dog's
        name?" scores a knot containing the common word "name" as highly as
        the one containing "dog" — and in a long memory the common word wins
        on sheer numbers. Rare words are what actually identify a fact.
        """
        total = len(self._knots) or 1
        weights = {}
        for token in expanded:
            df = len(self._index.get(token) or ())
            weights[token] = math.log(1.0 + total / (1.0 + df))
        return weights

    def _score(self, record, expanded, about_user, weights):
        matched = expanded & record["w"]
        overlap = sum(weights[t] for t in matched)
        # A word that was near the knot is weaker evidence than a word in it.
        hidden = (expanded & record.get("idx", frozenset())) - matched
        if hidden:
            overlap += INDEX_WEIGHT * sum(weights[t] for t in hidden)
        if overlap and about_user and SUBJECT_FOCUS:
            text = record["text"].lower()
            first_party = (("the user" in text or "the team" in text)
                           and not _CHAINED_POSSESSIVE.search(text))
            if not first_party:
                overlap *= (1.0 - SUBJECT_FOCUS)
        # Length normalisation: a long knot has more words and so more chances
        # to match by accident, and it costs more of the budget when it wins.
        # Between two knots that answer equally well, take the denser one.
        if overlap and IMPORTANCE_RANK:
            overlap *= (record.get("imp", 1.0) / 3.0) ** IMPORTANCE_RANK
        if overlap and LENGTH_NORM:
            overlap *= (REFERENCE_LENGTH / max(len(record["text"]),
                                               REFERENCE_LENGTH)) ** LENGTH_NORM
        subject = 1 if (about_user and "user" in record["text"].lower()) else 0
        return (round(overlap, 6), subject, record["seq"])

    def _candidates(self, expanded):
        """Knots sharing at least one word with the query. Caller holds lock."""
        seqs = set()
        for token in expanded:
            bucket = self._index.get(token)
            if bucket:
                seqs |= bucket
        if not seqs:
            return []
        by_seq = self._by_seq
        return [by_seq[s] for s in seqs if s in by_seq]

    def _overlapping(self, words, minimum):
        """Knots sharing at least `minimum` of `words`, without touching the
        rest of the memory.

        A knot that shares `minimum` of n words misses at most n - minimum of
        them, so it must contain at least one of any (n - minimum + 1) of
        them. Probing that many of the *rarest* words is therefore exact —
        no near-duplicate can hide from it — while skipping the huge posting
        lists of common words, which is what made add() linear in the size of
        the memory (26 seconds to load ten thousand knots).
        """
        index = self._index
        by_seq = self._by_seq
        probes = sorted(words, key=lambda t: len(index.get(t) or ()))
        probes = probes[:max(1, len(words) - minimum + 1)]
        seqs = set()
        for token in probes:
            seqs |= index.get(token) or set()
        out = []
        for seq in seqs:
            record = by_seq.get(seq)
            if record is None:
                continue
            shared = len(words & record["w"])
            if shared >= minimum:
                out.append((record, shared))
        return out

    def _ranked(self, query, all_knots=False):
        """[(score, knot)] best first. Caller holds the lock.

        By default only knots that share a word with the query are scored —
        everything else scores (0, ...) by construction and pack() would stop
        before reaching it. Pass all_knots=True when the caller genuinely
        wants every knot ranked (explain()).
        """
        expanded, associated = self._expand(query)
        about_user = "user" in _coerce(query).lower()
        pool = self._knots if all_knots else self._candidates(expanded)
        weights = self._weights(expanded)
        for token in associated:
            weights[token] *= ASSOCIATION_WEIGHT
        scored = [(self._score(k, expanded, about_user, weights), k)
                  for k in pool]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored

    def pack(self, query, budget=DEFAULT_BUDGET):
        """The densest set of relevant knots that fits in `budget` characters.

        Never exceeds the budget, never raises, returns "" when empty.
        """
        budget = max(0, int(budget))
        if not budget:
            return ""
        with self._lock:
            if not self._knots:
                return ""

            # A reserved slice, chosen by importance and not by the query.
            #
            # Retrieval assumes the query tells you what to fetch. In a chat
            # it does; in roleplay it does not — "I lean over and kiss her
            # cheek" shares no words with the facts the reply will need, and
            # measured on real logs a purely query-driven pack carried 2% of
            # them. The standing facts (who people are, what was agreed) are
            # needed in every scene regardless of what the turn says, so a
            # fraction of the budget carries them unconditionally.
            reserved, used = [], 0
            if PACK_RESERVE > 0 and len(self._knots) > 1:
                allowance = int(budget * PACK_RESERVE)
                for k in sorted(self._knots,
                                key=lambda k: (k.get("imp", 1.0), k["seq"]),
                                reverse=True):
                    cost = len(k["text"]) + (1 if reserved else 0)
                    if used + cost > allowance:
                        continue
                    reserved.append(k)
                    used += cost
                    if used >= allowance * 0.9:
                        break
            taken = {id(k) for k in reserved}

            ranked = self._ranked(query)
            if not ranked:
                # nothing matched: send the newest knot rather than nothing,
                # so the model still has some grounding
                newest = max(self._knots, key=lambda k: k["seq"])
                if len(newest["text"]) > budget:
                    return ""
                newest["hits"] += 1
                return newest["text"]
            # A weak match is worse than no match in a small memory: it
            # spends budget the strong matches need. In a large one the same
            # rule backfires — the top score is inflated by whichever long
            # knot matched best, and half of it cuts off the answer along
            # with the noise. Measured on a 481-knot roleplay log, dropping
            # the floor recovers 2 of 20 facts at no cost to the small suites.
            floor = (ranked[0][0][0] * RELEVANCE_FLOOR
                     if len(self._knots) <= FLOOR_MAX_KNOTS else 0.0)
            out, total = list(reserved), used
            for score, k in ranked:
                if score[0] < floor:
                    break
                if id(k) in taken:
                    continue
                cost = len(k["text"]) + (1 if out else 0)
                if total + cost > budget:
                    continue
                out.append(k)
                total += cost
            for k in out:
                k["hits"] += 1
            return "\n".join(k["text"] for k in out)

    def explain(self, query, budget=DEFAULT_BUDGET):
        """Why pack() chose what it chose — [(score, in_pack, text)]."""
        with self._lock:
            chosen = set(self.pack(query, budget).split("\n"))
            return [(score[0], k["text"] in chosen, k["text"])
                    for score, k in self._ranked(query, all_knots=True)]

    def stats(self):
        with self._lock:
            stored = sum(len(k["text"]) for k in self._knots)
            return {
                "mean_importance": (sum(k.get("imp", 1.0) for k in self._knots)
                                    / len(self._knots)) if self._knots else 0.0,
                "observed_chars": self._observed,
                "stored_chars": stored,
                "entries": len(self._knots),
                "density": stored / self._observed if self._observed else 0.0,
                "max_entries": self.max_entries,
            }

    # ---------------------------------------------------------------- persist

    def serialize(self):
        """The whole memory as bytes. Round-trips through deserialize()."""
        with self._lock:
            return json.dumps({
                "v": self.FORMAT_VERSION,
                "max": self.max_entries,
                "o": self._observed,
                "seq": self._seq,
                "k": [[k["text"], k["seq"], k["hits"], round(k["ts"], 3)]
                      for k in self._knots],
            }, ensure_ascii=False).encode("utf-8")

    @classmethod
    def deserialize(cls, data):
        """Rebuild from serialize() output.

        Accepts the v1 format ({"e": [...], "o": n}) as well, so memories
        written by earlier versions keep working. Raises ValueError on data
        that is not a Qontext memory at all — use load() if you would rather
        get an empty memory than an exception.
        """
        if isinstance(data, (bytes, bytearray)):
            data = bytes(data).decode("utf-8", "replace")
        try:
            d = json.loads(data)
        except Exception as e:
            raise ValueError("not a Qontext memory: %s" % e)
        if not isinstance(d, dict):
            raise ValueError("not a Qontext memory: expected an object")

        mem = cls(max_entries=d.get("max", DEFAULT_MAX_ENTRIES)
                  if isinstance(d.get("max"), int) and d.get("max", 0) > 0
                  else DEFAULT_MAX_ENTRIES)
        mem._observed = d.get("o", 0) if isinstance(d.get("o"), int) else 0

        if "k" in d:                                    # v2
            for row in d.get("k") or []:
                if isinstance(row, str):
                    row = [row]
                elif not isinstance(row, (list, tuple)):
                    continue
                text, seq, hits, ts = (list(row) + ["", 0, 0, 0.0])[:4]
                text = _coerce(text).strip()
                if not text or text in mem._seen:
                    continue
                text = text[:MAX_ENTRY_CHARS]
                knot_frame = _frame(text)
                record = {"text": text, "seq": int(seq or 0),
                          "hits": int(hits or 0), "ts": float(ts or 0.0),
                          "w": frozenset(_words(text)), "f": knot_frame,
                          "ids": _identifiers(knot_frame), "reinforced": 0,
                          "imp": _importance(text, 0, int(hits or 0))}
                mem._knots.append(record)
                mem._seen.add(text)
                mem._index_add(record)
        else:                                           # v1
            for text in d.get("e") or []:
                text = _coerce(text).strip()
                if text and text not in mem._seen:
                    mem._seq += 1
                    text = text[:MAX_ENTRY_CHARS]
                    knot_frame = _frame(text)
                    record = {"text": text, "seq": mem._seq, "hits": 0,
                              "ts": 0.0, "w": frozenset(_words(text)),
                              "f": knot_frame, "ids": _identifiers(knot_frame),
                              "reinforced": 0, "imp": _importance(text)}
                    mem._knots.append(record)
                    mem._seen.add(text)
                    mem._index_add(record)
        mem._seq = max([k["seq"] for k in mem._knots] + [d.get("seq") or 0])
        mem._evict()
        return mem

    def save(self, path):
        """Write to `path` atomically — a crash mid-write cannot corrupt it."""
        path = pathlib.Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = self.serialize()
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(blob)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return path

    @classmethod
    def load(cls, path, max_entries=DEFAULT_MAX_ENTRIES):
        """Read from `path`, or return a fresh memory if it is missing or
        unreadable. Deployment reality: a corrupt memory file should cost the
        user their history, not their session."""
        try:
            return cls.deserialize(pathlib.Path(path).read_bytes())
        except (OSError, ValueError, UnicodeDecodeError):
            return cls(max_entries=max_entries)

    def __repr__(self):
        return "<QontextMemory %d knots, %d observed chars>" % (
            len(self._knots), self._observed)


# Back-compat: the class was called QuipuMemory while the experiment ran.
QuipuMemory = QontextMemory


def _cli(argv):
    """Inspect a memory file without writing a script.

        python qontext_memory.py demo
        python qontext_memory.py show    qontext.qx
        python qontext_memory.py stats   qontext.qx
        python qontext_memory.py pack    qontext.qx "when is the demo?" [budget]
        python qontext_memory.py why     qontext.qx "when is the demo?"
        python qontext_memory.py forget  qontext.qx "bikkel"
    """
    command = argv[0] if argv else "demo"
    if command in ("-h", "--help", "help"):
        print(_cli.__doc__.strip())
        return 0
    if command == "demo":
        _demo()
        return 0
    if len(argv) < 2:
        print(_cli.__doc__.strip())
        return 2

    path = argv[1]
    if not pathlib.Path(path).is_file():
        print("no such memory file: %s" % path)
        return 1
    mem = QontextMemory.load(path)

    if command == "show":
        for knot in mem.entries():
            print("-", knot)
    elif command == "stats":
        st = mem.stats()
        print("knots        %d / %d" % (st["entries"], st["max_entries"]))
        print("observed     %d chars" % st["observed_chars"])
        print("stored       %d chars (%.0f%% of observed)"
              % (st["stored_chars"], 100 * st["density"]))
    elif command in ("pack", "why"):
        if len(argv) < 3:
            print("usage: %s FILE \"question\"" % command)
            return 2
        query = argv[2]
        budget = int(argv[3]) if len(argv) > 3 else DEFAULT_BUDGET
        if command == "pack":
            print(mem.pack(query, budget))
        else:
            for score, in_pack, text in mem.explain(query, budget):
                print("%6.2f %s %s" % (score, "->" if in_pack else "  ", text))
    elif command == "forget":
        if len(argv) < 3:
            print("usage: forget FILE \"text\"")
            return 2
        gone = mem.forget(argv[2])
        mem.save(path)
        print("forgot %d knot%s" % (gone, "" if gone == 1 else "s"))
    else:
        print(_cli.__doc__.strip())
        return 2
    return 0


def _demo():
    mem = QontextMemory()
    script = [
        ("user", "Morning! People call me Marta, and I work as a nurse."),
        ("assistant", "Nice to meet you, Marta."),
        ("user", "Oh, before I forget: the demo is on Friday at 10:00."),
        ("user", "The traffic was awful today, took me an hour to get home."),
        ("user", "Please keep explanations brief, I skim a lot."),
    ]
    for speaker, text in script:
        mem.observe(speaker, text)
    print(repr(mem))
    for knot in mem.entries():
        print("  -", knot)
    for q in ("What is the user's job?", "When is the demo?",
              "How should explanations be written?"):
        print("\n? %s\n  %s" % (q, mem.pack(q, 200).replace("\n", "\n  ")))
    st = mem.stats()
    print("\n%d chars observed -> %d stored (%.0f%%)"
          % (st["observed_chars"], st["stored_chars"], 100 * st["density"]))


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
