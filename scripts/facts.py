"""
Balíček faktů a komentář složený z nich — sdílená část generátorů textů.

Proč takhle: komentář nad grafem má být doložitelný. Každá věta proto odkazuje na
fakta, ze kterých vychází, a čísla se do vět dosazují až nakonec ze seznamu hodnot.
Ve větě tedy nemůže stát číslo, které není mezi fakty, a nemůže zmizet číslo,
které fakt uvádí. Tvar odpovídá balíčkům faktů v hodnocení LLM komentářů, takže
tentýž balíček umí vyplnit šablona (dnes) i jazykový model (po vyhodnocení pilotu)
beze změny stránky.

Používá `compute_resp.py` (respirační bloky) a `compute_genomics.py` (genomika).
Jen výpočet a formátování, žádné I/O.
"""

import re

NBSP = " "
MAX_COMMENT_WORDS = 80         # tvrdý limit komentáře (stejný jako v hodnocení LLM komentářů)


def fmt_num(x: float, lang: str, decimals: int = 1) -> str:
    s = f"{x:,.{decimals}f}"
    return s.replace(",", NBSP).replace(".", ",") if lang == "cs" else s


def fmt_int(n: int, lang: str) -> str:
    return fmt_num(n, lang, 0)


def fmt_pct(x: float, lang: str, decimals: int = 1) -> str:
    return f"{fmt_num(x, lang, decimals)}{NBSP}%" if lang == "cs" else f"{fmt_num(x, lang, decimals)}%"


def plural_cs(n: int, one: str, few: str, many: str) -> str:
    return one if n == 1 else few if 2 <= n <= 4 else many


class Facts:
    """
    Hodnoty, fakta a věty komentáře. Věty i fakta nesou zástupné znaky {jmeno},
    které `fill()` nahradí hodnotou ve zvoleném jazyce.
    """

    def __init__(self, max_words: int = MAX_COMMENT_WORDS):
        self.values: dict[str, dict] = {}
        self.facts: list[dict] = []
        self.required: list[str] = []
        self.sentences: list[dict] = []
        self.max_words = max_words

    # ── hodnoty ──────────────────────────────────────────────────────────────

    def val(self, name: str, raw, cs: str, en: str, week: str | None = None):
        self.values[name] = {"raw": raw, "cs": cs, "en": en, "week": week}

    def num(self, name: str, x: float, week: str | None = None, decimals: int = 1):
        self.val(name, round(float(x), decimals), fmt_num(x, "cs", decimals),
                 fmt_num(x, "en", decimals), week)

    def count(self, name: str, n: int, week: str | None = None):
        self.val(name, int(n), fmt_int(n, "cs"), fmt_int(n, "en"), week)

    def pct(self, name: str, x: float, week: str | None = None, decimals: int = 1):
        self.val(name, round(float(x), decimals), fmt_pct(x, "cs", decimals),
                 fmt_pct(x, "en", decimals), week)

    def text(self, name: str, cs: str, en: str):
        self.val(name, None, cs, en)

    # ── fakta a věty ─────────────────────────────────────────────────────────

    def fact(self, fid: str, kind: str, cs: str, en: str, required: bool = False):
        self.facts.append({"id": fid, "kind": kind, "cs": cs, "en": en})
        if required:
            self.required.append(fid)

    def say(self, cs: str, en: str, facts: list[str], optional: bool = False):
        """`optional` = věta, která se vynechá, když by komentář přesáhl limit slov."""
        self.sentences.append({"cs": cs, "en": en, "facts": facts, "optional": optional})

    def fill(self, text: str, lang: str) -> str:
        return re.sub(r"\{(\w+)\}", lambda m: self.values[m.group(1)][lang], text)

    def block(self) -> dict:
        """Hotová dvojice `facts` + `comment` do výstupního JSON."""
        def words() -> int:
            # Delší z obou jazyků: angličtina bývá o pár slov delší a limit platí pro oba texty.
            return max(sum(len(self.fill(s[lang], lang).split()) for s in self.sentences)
                       for lang in ("cs", "en"))
        while words() > self.max_words and any(s["optional"] for s in self.sentences):
            last = max(i for i, s in enumerate(self.sentences) if s["optional"])
            del self.sentences[last]

        known = {f["id"] for f in self.facts}
        used = {fid for s in self.sentences for fid in s["facts"]}
        if used - known:
            raise ValueError(f"komentář odkazuje na neexistující fakta: {sorted(used - known)}")
        if set(self.required) - used:
            raise ValueError(f"komentář vynechal povinná fakta: {sorted(set(self.required) - used)}")
        return {
            "facts": {"values": self.values, "facts": self.facts, "required": self.required},
            "comment": {
                "origin": "template",
                "cs": " ".join(self.fill(s["cs"], "cs") for s in self.sentences),
                "en": " ".join(self.fill(s["en"], "en") for s in self.sentences),
                "sentences": [{"cs": self.fill(s["cs"], "cs"), "en": self.fill(s["en"], "en"),
                               "facts": s["facts"]} for s in self.sentences],
            },
        }
