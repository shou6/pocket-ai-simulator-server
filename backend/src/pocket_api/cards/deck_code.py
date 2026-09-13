"""デッキコード（ゲームのデッキ QR の中身）とデッキの相互変換（F-11 / F-12）。

QR 画像 ↔ 文字列はブラウザでやる。ここは文字列 ↔ デッキだけを扱い、
CLI と WEB で同じ結果にする（`docs/deck-qr.md` 4 節）。

    標準 Base64 → トレーナーズ区画 / ポケモン区画 / エネルギー区画
    区画 = 1 バイトの枚数 + 1 枚につき 3 バイト（エネルギーは 1 バイト）
    カードの値 = deckBuilderNr × 10（トレーナーズはさらに +10,000,000）
"""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache

import pocket_engine_py as engine

from pocket_api.cards.deck_builder_ids import deck_builder_ids
from pocket_api.optimize.recipe import DECK_SIZE, DeckRecipe

TRAINER_OFFSET = 10_000_000
"""トレーナーズの値に足されている数。"""

ENERGY_CODES = (
    "Grass",
    "Fire",
    "Water",
    "Lightning",
    "Psychic",
    "Fighting",
    "Darkness",
    "Metal",
)
"""エネルギーの番号（1 始まり）。8 種すべて実機の QR で確認した（`docs/deck-qr.md` 2 節）。"""

MAX_ENERGY_TYPES = 3
"""デッキに指定できるエネルギーの種類の上限。"""

TRAINER_KIND_ORDER = ("グッズ", "ポケモンのどうぐ", "サポート", "スタジアム")
"""実機がトレーナーズ区画を書く順。種類ごとに番号順で並ぶ。"""


class _Reader:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw
        self._pos = 0

    def take(self, size: int, what: str) -> bytes:
        if self._pos + size > len(self._raw):
            raise ValueError(f"デッキコードの長さが足りません（{what}）")
        chunk = self._raw[self._pos : self._pos + size]
        self._pos += size
        return chunk

    @property
    def remaining(self) -> int:
        return len(self._raw) - self._pos


def decode_deck_code(code: str) -> DeckRecipe:
    """デッキコードをデッキにする。壊れていれば理由付きの `ValueError`。"""
    text = code.strip()
    if not text:
        raise ValueError("デッキコードが短すぎます")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("デッキコードが Base64 として読めません") from error
    if not raw:
        raise ValueError("デッキコードが短すぎます")

    reader = _Reader(raw)
    values: list[tuple[str, int]] = []
    for kind, label in (("TR", "トレーナーズ"), ("PK", "ポケモン")):
        count = reader.take(1, f"{label}の枚数")[0]
        for _ in range(count):
            value = int.from_bytes(reader.take(3, label), "big")
            is_trainer = value >= TRAINER_OFFSET
            if is_trainer != (kind == "TR"):
                raise ValueError(f"{label}区画に種別の違う値があります: {value}")
            number, rest = divmod(value - (TRAINER_OFFSET if is_trainer else 0), 10)
            if rest:
                raise ValueError(f"カードの値が 10 の倍数ではありません: {value}")
            values.append((kind, number))
    energy_count = reader.take(1, "エネルギーの個数")[0]
    energy_codes = reader.take(energy_count, "エネルギー")
    if reader.remaining:
        raise ValueError(f"デッキコードの末尾に余りが {reader.remaining} バイトあります")

    if len(values) != DECK_SIZE:
        raise ValueError(f"デッキは {DECK_SIZE} 枚である必要があります（{len(values)} 枚）")
    energy = _energy_names(list(energy_codes))

    ids = deck_builder_ids()
    counts: dict[str, int] = {}
    for kind, number in values:
        card_id = ids.representative(kind, number)
        if card_id is None:
            raise ValueError(f"カードが見つかりません（{kind}:{number}）")
        counts[card_id] = counts.get(card_id, 0) + 1
    return DeckRecipe(cards=tuple(sorted(counts.items())), energy=energy)


def _energy_names(codes: list[int]) -> tuple[str, ...]:
    if not codes or len(codes) > MAX_ENERGY_TYPES:
        raise ValueError(f"エネルギーは 1〜{MAX_ENERGY_TYPES} 種です（{len(codes)} 種）")
    names: list[str] = []
    for code in codes:
        if not 1 <= code <= len(ENERGY_CODES):
            raise ValueError(f"エネルギーの番号が不正です: {code}")
        names.append(ENERGY_CODES[code - 1])
    return tuple(names)


@lru_cache(maxsize=4096)
def _details(card_id: str) -> tuple[str, str, int, str | None]:
    """（種類, 名前, 進化段階, 進化元）。"""
    found = engine.card_details([card_id])
    if not found:
        raise ValueError(f"カードが見つかりません: {card_id}")
    card = found[0]
    return card.kind, card.name, card.stage or 0, card.evolves_from


def encode_deck_code(recipe: DeckRecipe) -> str:
    """デッキをデッキコードにする。

    見るのは deckBuilderNr だけで、再録のどれを使っていても同じコードになる。
    ポケモンは進化ラインごとにまとめて並べる（実機も進化ラインで並べる。
    ライン同士の並びは実機でも任意なので、元のコードと文字列は一致しない）。
    """
    if recipe.size != DECK_SIZE:
        raise ValueError(f"デッキは {DECK_SIZE} 枚である必要があります（{recipe.size} 枚）")
    if not recipe.energy or len(recipe.energy) > MAX_ENERGY_TYPES:
        raise ValueError(f"エネルギーは 1〜{MAX_ENERGY_TYPES} 種です（{len(recipe.energy)} 種）")
    energy_codes: list[int] = []
    for name in recipe.energy:
        if name not in ENERGY_CODES:
            raise ValueError(f"デッキに指定できないエネルギーです: {name}")
        energy_codes.append(ENERGY_CODES.index(name) + 1)

    ids = deck_builder_ids()
    trainers: list[tuple[int, int]] = []
    pokemon: list[tuple[str, int, str, int]] = []
    for card_id, count in recipe.cards:
        key = ids.key_of(card_id)
        if key is None:
            raise ValueError(f"デッキコードの番号が分からないカードです: {card_id}")
        kind, number = key
        card_kind, name, stage, _ = _details(card_id)
        for _ in range(count):
            if kind == "TR":
                order = (
                    TRAINER_KIND_ORDER.index(card_kind)
                    if card_kind in TRAINER_KIND_ORDER
                    else len(TRAINER_KIND_ORDER)
                )
                trainers.append((order, number))
            else:
                pokemon.append((name, stage, card_id, number))

    names_in_deck = {name for name, _, _, _ in pokemon}

    def line_root(card_id: str) -> str:
        """デッキ内で遡れるいちばん前の進化元の名前。"""
        _, name, _, ancestor = _details(card_id)
        root = name
        seen = {name}
        while ancestor and ancestor in names_in_deck and ancestor not in seen:
            root = ancestor
            seen.add(ancestor)
            ancestor = next(
                (_details(cid)[3] for n, _, cid, _ in pokemon if n == ancestor),
                None,
            )
        return root

    roots = {card_id: line_root(card_id) for _, _, card_id, _ in pokemon}
    root_number = {
        roots[card_id]: number
        for name, _, card_id, number in sorted(pokemon, key=lambda p: -p[3])
        if name == roots[card_id]
    }
    pokemon.sort(
        key=lambda p: (root_number.get(roots[p[2]], p[3]), roots[p[2]], p[1], p[3]),
    )

    out = bytearray([len(trainers)])
    for _, number in sorted(trainers):
        out += (TRAINER_OFFSET + number * 10).to_bytes(3, "big")
    out.append(len(pokemon))
    for _, _, _, number in pokemon:
        out += (number * 10).to_bytes(3, "big")
    out.append(len(energy_codes))
    out += bytes(energy_codes)
    return base64.b64encode(bytes(out)).decode("ascii")
