# DDC-CWICR-OE: DataDrivenConstruction · OpenConstructionERP
# Copyright (c) 2026 Artem Boiko / DataDrivenConstruction
"""Trade-domain understanding for cost matching - *what kind of thing* a row is.

Why this exists
---------------
The lexical scorer compares tokens; tokens alone cannot say that
"Обучение на персонал за работа със системата" (a *service*) and an
air-cooled Gree chiller (an *HVAC asset*) live in different worlds and can
never price each other, however many generic nouns they happen to share
("работа", "система"). This module gives every text - bill line or corpus
row - a **domain**: the trade family a quantity surveyor would file it
under.

How it works
------------
``domain_of(text)`` reads the matcher's canonical concept tokens *and* the
Bulgarian stems the suffix ladder emits, so it speaks the same language as
the rest of the matcher (and as the cost databases the corpus was seeded
from). Each domain declares anchor forms; a text lands in the domain with
the most anchor evidence, with two deliberate safeguards:

* **unknown is a real answer.** A terse or unusual row carries no anchor;
  it gets ``None`` and is never blocked by the domain gate. Absence of
  classification must not destroy recall.
* **services dominate.** Words that name an act performed for the client
  (обучение, инструктаж, пусково-наладъчни работи, гаранционно обслужване,
  проектиране, надзор, документация) outrule material tokens elsewhere in
  the line, because "Обучение ... на системата" is a service ABOUT a
  system, not an HVAC row.

The gate itself lives in ``matcher.score_match``: two confidently
classified rows in disjoint domains cannot be the same price. Adjacent
trades (plumbing≈HVAC around pumps, roofing≈waterproofing, …) are explicitly
*not* conflicts - see :data:`_ADJACENT`.
"""

from __future__ import annotations

from app.modules.cost_match.matcher import canonical_tokens

# ── Domain anchors ──────────────────────────────────────────────────────────
# Anchors are canonical concept names (as produced by ``canonical_tokens`` -
# e.g. ``pipe``, ``fan``) OR Bulgarian stems the suffix ladder emits (e.g.
# ``обуч`` for "обучение", ``чилър`` which survives unstemmed). Where a
# concept already exists the anchor is the concept, so every language that
# maps onto it classifies identically. Add new forms as stems, not surface
# spellings, so inflection never splits a family.

_DOMAIN_ANCHORS: dict[str, frozenset[str]] = {
    # Земни работи: масови изкопи, засипки, планировка, депо за отпадъци.
    "earthworks": frozenset({
        "excavation", "backfill", "haulage", "excavator",
        "изкоп", "насип", "засип", "планиране", "планиров", "трамбов",
        "уплътн", "денивелац", "teren", "терен", "извозв",
    }),
    # Конструктив: бетон, армировка, кофраж, фундаменти, скелета.
    "structural": frozenset({
        "concrete", "reinforcement", "formwork", "foundation", "column",
        "beam", "concrete_mixer", "бетон", "арматур", "кофраж", "стобетон",
    }),
    # Зидария и стени.
    "masonry": frozenset({
        "masonry", "wall", "тухл", "зид", "газобетон", "блокче",
    }),
    # Довършителни: мазилки, бои, плочки, настилки, тавани.
    "finishes": frozenset({
        "plaster", "painting", "screed", "tiling", "flooring", "ceiling",
        "putty", "primer", "cladding", "drywall", "gypsum_fiber", "joint",
        "мазилк", "шпаклов", "латекс", "боядис", "гранитогрес", "замазк",
        "настилк", "облицов", "лайсн", "перваз",
    }),
    # Покривни работи.
    "roofing": frozenset({
        "roof", "керемид", "водосточн", "улук", "кенто", "ламарин",
        "мълниеприемн", "мълниеприем",
    }),
    # Изолации (топло/хидро) - споделена граница с покриви и довършителни.
    "waterproofing": frozenset({
        "waterproofing", "insulation", "membrane", "хидроизолац",
        "топлоизолац", "битум",
    }),
    # ВиК: тръби, фитинги, арматура, санитария.
    "plumbing": frozenset({
        "pipe", "valve", "reducer", "elbow", "nozzle", "sensor",
        "тръб", "кран", "муф", "колян", "холенд", "тройник", "сифон",
        "резб", "водомер", "канализац", "водопровод", "спирателн",
        "пожарогас", "хидрант", "поливн", "резервоар", "водосточн",
        "певп", "ппс", "полипропилен", "полиетилен",
    }),
    # ОВиК: чилъри, вентилация, климатизация, помпи.
    "hvac": frozenset({
        "fan", "duct", "recuperator", "chiller", "чилър", "климат",
        "вентилац", "въздуховод", "калорифер", "термопомп", "агрегат",
        "дифузер", "анемостат", "помп", "охлажд", "отоплен", "радиатор",
        "конвектор", "клима", "афтономн",
    }),
    # Електро: кабели, табла, осветление, заземяване.
    "electrical": frozenset({
        "cable", "grounding", "кабел", "прекъсвач", "табло", "освет",
        "контакт", "ключ", "лампа", "плафон", "проводник", "излаз",
        "разпределител", "зазем", "автомат", "грт", "клема", "клеми",
        "шуко", "мълниезащит", "трафопост",
    }),
    # Слаботокови: датчици, камери, известяване, мрежи.
    "elv": frozenset({
        "elv", "датчик", "камера", "извест", "сот", "видеонаблюд",
        "домофон", "рекордер", "сървър", "rj45", "коаксиал", "оптич",
        "пожароизвест", "достъп", "мегапиксел",
    }),
    # Метални конструкции.
    "metalworks": frozenset({
        "metal_structure", "steel", "металоконструкц", "стоман", "профил",
        "парапет", "огражд", "решетк", "стълбищ",
    }),
    # Озеленяване: дървета, храсти, трева, разсад.
    "landscaping": frozenset({
        "дърво", "дървет", "храст", "трев", "засажд", "разсад", "цвет",
        "ливад", "парк", "градин", "туф", "посадъчн", "зелен", "изсичане",
        "корон", "клон",
    }),
    # Услуги и организационни дейности - обучение, инструктаж, ПНР,
    # гаранционно обслужване, проектиране, надзор, документация.
    "services": frozenset({
        "обуч", "инструкт", "инструктаж", "пусково", "налад", "наладъчн",
        "гаранц", "сервиз", "поддръж", "обслужв", "експлоатац",
        "проектир", "надзор", "документац", "паспорт", "паспортизац",
        "изпитв", "изпитан", "изпробв", "консулт", "обезпеченост",
        "тест", "тестов", "верификац", "въвеждане",
    }),
    # Транспорт и механизация: машиночасове, превози, кранова работа.
    "plant_transport": frozenset({
        "concrete_mixer", "excavator", "машиночас", "машиносмян",
        "кран", "автокран", "самосвал", "превоз", "механизац",
    }),
}

# Stems that name a bundled site *activity*, not a priced object. "ПЕВП тръба
# - включително изкоп и засипване" is a plumbing row that happens to mention
# dig/fill; those words alone must not reclassify it as earthworks. When a
# domain's entire evidence is activity vocabulary, a competing object anchor
# decides the trade instead.
_ACTIVITY_ANCHORS: dict[str, frozenset[str]] = {
    "earthworks": frozenset({
        "excavation", "backfill", "haulage",
        "изкоп", "насип", "засип", "планиране", "планиров", "трамбов",
        "уплътн", "денивелац", "извозв",
    }),
}

# Trades whose rows legitimately price each other: a pump is plumbing kit on
# a ВиК bill and HVAC kit on an оВиК one; roof waterproofing reads as roofing
# in one catalogue and as insulation in the next. Adjacency is symmetric
# evidence of *possible* identity, not a merge - the lexical evidence still
# decides the match.
_ADJACENT: frozenset[frozenset[str]] = frozenset({
    frozenset({"plumbing", "hvac"}),
    frozenset({"roofing", "waterproofing"}),
    frozenset({"finishes", "waterproofing"}),
    frozenset({"finishes", "masonry"}),
    frozenset({"electrical", "elv"}),
    frozenset({"earthworks", "landscaping"}),
    frozenset({"earthworks", "plant_transport"}),
})

# Service vocabulary outranks every physical domain: "Направа на ..." is
# work, but "Обучение на персонал за работа със системата" is never HVAC
# even though an HVAC word sits inside it. The detector runs services first
# and only falls through to the physical domains when no service anchor is
# present.
_SERVICE_PRIORITY = "services"


def domain_of(text: str | None) -> str | None:
    """Classify ``text`` into a trade domain, or ``None`` when unclassifiable.

    Deterministic, table-driven and deliberately conservative: a row whose
    evidence splits across domains (or carries none) returns ``None`` so the
    gate never blocks a pair on a guess.
    """
    if not text:
        return None
    tokens = set(canonical_tokens(text))
    if not tokens:
        return None
    hits: dict[str, int] = {}
    for domain, anchors in _DOMAIN_ANCHORS.items():
        count = len(tokens & anchors)
        if count:
            hits[domain] = count
    if not hits:
        return None
    if hits.get(_SERVICE_PRIORITY):
        return _SERVICE_PRIORITY
    best = max(hits.values())
    leaders = [d for d, n in hits.items() if n == best]
    if len(leaders) != 1:
        # Split evidence means the row spans trades; unknown keeps it open.
        return None
    leader = leaders[0]
    activity = _ACTIVITY_ANCHORS.get(leader)
    if activity and hits[leader] == len(tokens & _DOMAIN_ANCHORS[leader] & activity):
        # The leading trade rests entirely on bundled-activity vocabulary -
        # "изкоп и засипване" riding along a pipe row. A real object anchor
        # in another trade outranks it; a tie between objects stays open.
        others = {d: n for d, n in hits.items() if d != leader}
        if others:
            best = max(others.values())
            runner_up = [d for d, n in others.items() if n == best]
            return runner_up[0] if len(runner_up) == 1 else None
    return leader


def domains_conflict(query_domain: str | None, candidate_domain: str | None) -> bool:
    """Whether two domains are disjoint enough to forbid a price pairing.

    ``True`` only when *both* sides are classified, they name different
    trades, and the trade pair is not on the adjacency list. An unclassified
    side (``None``) can never conflict - silence is not identity.
    """
    if not query_domain or not candidate_domain:
        return False
    if query_domain == candidate_domain:
        return False
    if frozenset({query_domain, candidate_domain}) in _ADJACENT:
        return False
    return True


__all__ = ["domains_conflict", "domain_of"]
