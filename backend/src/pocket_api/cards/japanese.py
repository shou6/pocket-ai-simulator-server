"""英名のカード名・ワザ名・特性名を和名に置き換える。

エンジン（deckgym-core）は英名で動くので、対戦ログや相性表を表示するときだけ
`data/cards/names_ja.json` を使って置き換える。和名が無いものは英名のまま出す。
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_NAMES_PATH = REPO_ROOT / "data" / "cards" / "names_ja.json"

_BRACKET_RE = re.compile(r"「([^」]+)」")


class Translator:
    """英名 → 和名の置き換え。"""

    def __init__(
        self, cards: dict[str, str], attacks: dict[str, str], abilities: dict[str, str]
    ) -> None:
        self._cards = cards
        self._attacks = attacks
        self._abilities = abilities
        # 長い名前から先に照合する（`Mewtwo ex` の中の `Mew` を先に置き換えない）。
        # 英字が続く位置では一致させない（`Mew` が `Mewtwo` の一部に一致しない）
        names = sorted(cards, key=len, reverse=True)
        pattern = "|".join(re.escape(name) for name in names)
        self._card_re = re.compile(rf"(?<![A-Za-z])(?:{pattern})(?![A-Za-z])") if names else None

    @classmethod
    def from_cards(cls, cards: dict[str, dict[str, Any]]) -> Translator:
        """`names_ja.json` の `cards` から組み立てる。

        英名はエンジンのカード名（`names_ja.json` には入っていない）ではなく、
        Bulbapedia のページ名の元になった英名で引く必要があるため、
        英名は取り込み時に `english` として記録してある。
        同じ英名で和名が割れる場合（再録での表記揺れ）は多数決で決める。
        """
        name_votes: dict[str, Counter[str]] = {}
        attacks: dict[str, str] = {}
        abilities: dict[str, str] = {}
        for entry in cards.values():
            english = entry.get("english")
            if english:
                name_votes.setdefault(english, Counter())[entry["name"]] += 1
            attacks.update(entry.get("attacks", {}))
            abilities.update(entry.get("abilities", {}))
        names = {en: votes.most_common(1)[0][0] for en, votes in name_votes.items()}
        return cls(names, attacks, abilities)

    @classmethod
    def from_file(cls, path: Path = DEFAULT_NAMES_PATH) -> Translator:
        """和名ファイルを読む。無ければ何も置き換えない Translator を返す。"""
        if not path.exists():
            return cls({}, {}, {})
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_cards(data["cards"])

    def card_name(self, english: str) -> str:
        return self._cards.get(english, english)

    def text(self, value: str) -> str:
        """文中の英名を和名にする。「」内はワザ名・特性名・カード名として引く。"""

        def bracket(match: re.Match[str]) -> str:
            inner = match.group(1)
            ja = self._attacks.get(inner) or self._abilities.get(inner) or self._cards.get(inner)
            return f"「{ja or inner}」"

        value = _BRACKET_RE.sub(bracket, value)
        if self._card_re is None:
            return value
        return self._card_re.sub(lambda m: self._cards[m.group(0)], value)
