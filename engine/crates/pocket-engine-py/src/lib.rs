//! `pocket-engine` の Python バインディング。
//!
//! `maturin develop` でビルドすると、Python 側から
//! `import pocket_engine_py` で利用できる。
//!
//! Rust 側のエラーはすべて `ValueError` に変換する。Python から panic を
//! 見せないことがこの層の責務（`.claude/rules/engine.md`）。

// メモリ確保を mimalloc にする。方策 `l` は先読みで盤面を何度も複製し、確保と解放が多い。
// システムの malloc から替えると、B4a の上位 4 デッキの総当たりで 1 スレッド毎秒 22.7 → 47.6 試合、
// 12 スレッドで 85.9 → 162.4 試合になった（2026-09-14、i7-8700T）。結果（勝敗）は変わらない。
// ライブラリの pocket-engine ではなく、最終成果物のバインディング側で指定する。
#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use pocket_engine::cards::{
    AbilityInfo, AttackInfo, CardDossier, CardStatus, card_dossiers, card_statuses,
    incomplete_cards,
};
use pocket_engine::deck::{infer_energy_line, parse_deck, parse_deck_inferring_energy};
use pocket_engine::game::Strategy;
use pocket_engine::matchup::{
    Outcome, Record, evaluate_matchup as evaluate, replay_matchup_game as replay_matchup,
};
use pocket_engine::replay::{CardRef, PokemonSnapshot, Replay, ReplayStep, play_one_game_with_log};

/// エンジンのバージョン文字列を返す。
#[pyfunction]
fn engine_version() -> &'static str {
    pocket_engine::engine_version()
}

/// いま動いている deckgym-core のコミットハッシュを返す。
///
/// 開発中は `[patch]` が手元のフォークを見るので、`Cargo.toml` の `rev` ではなく
/// フォークの HEAD になる。勝率のキャッシュとサロゲートの学習データの鍵に使う。
#[pyfunction]
fn deckgym_revision() -> &'static str {
    pocket_engine::deckgym_revision()
}

/// デッキテキストを検証する。不正なら `ValueError` を送出する。
#[pyfunction]
fn validate_deck(text: &str) -> PyResult<()> {
    parse_deck(text)
        .map(|_| ())
        .map_err(|err| PyValueError::new_err(err.to_string()))
}

/// デッキテキストを検証する。`Energy:` 行がなければ推定して補う。
///
/// limitless のデッキリストには登録者がエネルギーを指定していないものがある。
/// 補った結果のデッキテキストを返す。
#[pyfunction]
fn normalize_deck(text: &str) -> PyResult<String> {
    let normalized = match infer_energy_line(text) {
        Some(line) => format!("{line}\n{text}"),
        None => text.to_string(),
    };
    parse_deck_inferring_energy(text)
        .map(|_| normalized)
        .map_err(|err| PyValueError::new_err(err.to_string()))
}

/// カード 1 枚の参照（画像の表示に使う）。
#[pyclass(name = "CardRef", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyCardRef {
    /// デッキテキストで使う識別子（`A1 001` 形式）。カード画像の場所に使う。
    pub id: String,
    /// カード名（英語）。表示するときに和名へ置き換える。
    pub name: String,
}

impl From<CardRef> for PyCardRef {
    fn from(value: CardRef) -> Self {
        Self {
            id: value.id,
            name: value.name,
        }
    }
}

/// 盤面にいるポケモン 1 匹の状態。
#[pyclass(name = "PokemonSnapshot", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyPokemonSnapshot {
    /// デッキテキストで使う識別子（`A1 001` 形式）。
    pub id: String,
    /// カード名。
    pub name: String,
    /// 残り HP。
    pub remaining_hp: u32,
    /// 最大 HP。
    pub max_hp: u32,
    /// 付いているエネルギー。
    pub energy: Vec<String>,
    /// 受けている状態異常。
    pub status: Vec<String>,
    /// 付いているどうぐ。
    /// ポケモンのどうぐ。ブロロローム（B4 115）だけ 2 枚持てる。
    pub tools: Vec<PyCardRef>,
}

impl From<PokemonSnapshot> for PyPokemonSnapshot {
    fn from(value: PokemonSnapshot) -> Self {
        Self {
            id: value.id,
            name: value.name,
            remaining_hp: value.remaining_hp,
            max_hp: value.max_hp,
            energy: value.energy,
            status: value.status,
            tools: value.tools.into_iter().map(Into::into).collect(),
        }
    }
}

/// 行動 1 つと、その直後の盤面。
#[pyclass(name = "ReplayStep", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyReplayStep {
    /// 何ターン目か。
    pub turn: u8,
    /// 行動したプレイヤー（0 が先攻）。
    pub actor: usize,
    /// 行動の説明。
    pub description: String,
    /// 行動後の両者のポイント。
    pub points: (u8, u8),
    /// 行動後の両者のバトルポケモン。
    pub active: (Option<PyPokemonSnapshot>, Option<PyPokemonSnapshot>),
    /// 行動後の両者のベンチ。
    pub bench: (Vec<PyPokemonSnapshot>, Vec<PyPokemonSnapshot>),
    /// 行動後の両者の手札枚数。
    pub hand_size: (usize, usize),
    /// 行動後の両者の手札の内容。
    pub hand: (Vec<PyCardRef>, Vec<PyCardRef>),
    /// 行動後に場に出ているスタジアム。
    pub stadium: Option<PyCardRef>,
    /// スタジアムの持ち主（0 が先攻）。
    pub stadium_owner: Option<usize>,
    /// 行動後の両者の山札枚数。
    pub deck_size: (usize, usize),
}

impl From<ReplayStep> for PyReplayStep {
    fn from(value: ReplayStep) -> Self {
        let [active_a, active_b] = value.active;
        let [bench_a, bench_b] = value.bench;
        Self {
            turn: value.turn,
            actor: value.actor,
            description: value.description,
            points: (value.points[0], value.points[1]),
            active: (active_a.map(Into::into), active_b.map(Into::into)),
            bench: (
                bench_a.into_iter().map(Into::into).collect(),
                bench_b.into_iter().map(Into::into).collect(),
            ),
            hand_size: (value.hand_size[0], value.hand_size[1]),
            hand: {
                let [a, b] = value.hand;
                (
                    a.into_iter().map(Into::into).collect(),
                    b.into_iter().map(Into::into).collect(),
                )
            },
            stadium: value.stadium.map(Into::into),
            stadium_owner: value.stadium_owner,
            deck_size: (value.deck_size[0], value.deck_size[1]),
        }
    }
}

/// 1 試合分の記録。
#[pyclass(name = "Replay", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyReplay {
    /// 行動の並び。
    pub steps: Vec<PyReplayStep>,
    /// 両者の初手（準備前の 5 枚）。
    pub opening_hands: (Vec<PyCardRef>, Vec<PyCardRef>),
    /// 試合の結果（`PlayerA` / `PlayerB` / `Tie` / `Unfinished`）。
    pub result: String,
    /// 最終的なポイント。
    pub points: (u8, u8),
    /// 決着までのターン数。
    pub turns: u8,
}

impl From<Replay> for PyReplay {
    fn from(value: Replay) -> Self {
        Self {
            steps: value.steps.into_iter().map(Into::into).collect(),
            opening_hands: {
                let [a, b] = value.opening_hands;
                (
                    a.into_iter().map(Into::into).collect(),
                    b.into_iter().map(Into::into).collect(),
                )
            },
            result: format!("{:?}", value.result),
            points: (value.points[0], value.points[1]),
            turns: value.turns,
        }
    }
}

/// 1 試合を回し、行動ごとの記録を返す。
#[pyfunction]
#[pyo3(signature = (deck_a, deck_b, strategy_a, strategy_b, seed))]
fn replay_game(
    py: Python<'_>,
    deck_a: &str,
    deck_b: &str,
    strategy_a: &str,
    strategy_b: &str,
    seed: u64,
) -> PyResult<PyReplay> {
    let to_value_error = |err: &dyn std::fmt::Display| PyValueError::new_err(err.to_string());

    let deck_a = parse_deck(deck_a).map_err(|err| to_value_error(&err))?;
    let deck_b = parse_deck(deck_b).map_err(|err| to_value_error(&err))?;
    let strategy_a: Strategy = strategy_a.parse().map_err(|err| to_value_error(&err))?;
    let strategy_b: Strategy = strategy_b.parse().map_err(|err| to_value_error(&err))?;

    let replay = py
        .detach(|| play_one_game_with_log(&deck_a, &deck_b, strategy_a, strategy_b, seed))
        .map_err(|err| to_value_error(&err))?;
    Ok(replay.into())
}

/// カード 1 枚の実装状況。
#[pyclass(name = "CardStatus", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyCardStatus {
    /// デッキテキストで使う識別子（`A1 001` 形式）。
    pub id: String,
    /// カード名（英語）。
    pub name: String,
    /// 実装済みかどうか。
    pub is_complete: bool,
    /// 実装状況の説明。未実装の理由を UI に出すために使う。
    pub description: &'static str,
}

impl From<CardStatus> for PyCardStatus {
    fn from(status: CardStatus) -> Self {
        Self {
            id: status.id,
            name: status.name,
            is_complete: status.implementation.is_complete(),
            description: status.implementation.description(),
        }
    }
}

#[pymethods]
impl PyCardStatus {
    fn __repr__(&self) -> String {
        format!(
            "CardStatus(id={}, name={}, {})",
            self.id, self.name, self.description
        )
    }
}

/// すべてのカードの実装状況を返す。
#[pyfunction]
fn card_statuses_all() -> Vec<PyCardStatus> {
    card_statuses().into_iter().map(Into::into).collect()
}

/// ワザ 1 つの内容。
#[pyclass(name = "AttackInfo", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyAttackInfo {
    /// ワザ名（英語）。
    pub title: String,
    /// 必要なエネルギー（日本語のタイプ名）。
    pub cost: Vec<String>,
    /// 基礎ダメージ。
    pub damage: u32,
    /// 効果文（英語）。
    pub effect: Option<String>,
}

impl From<AttackInfo> for PyAttackInfo {
    fn from(value: AttackInfo) -> Self {
        Self {
            title: value.title,
            cost: value.cost,
            damage: value.damage,
            effect: value.effect,
        }
    }
}

/// 特性 1 つの内容。
#[pyclass(name = "AbilityInfo", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyAbilityInfo {
    /// 特性名（英語）。
    pub title: String,
    /// 効果文（英語）。
    pub effect: String,
}

impl From<AbilityInfo> for PyAbilityInfo {
    fn from(value: AbilityInfo) -> Self {
        Self {
            title: value.title,
            effect: value.effect,
        }
    }
}

/// カード 1 枚の性能。
#[pyclass(name = "CardDossier", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyCardDossier {
    /// デッキテキストで使う識別子（`A1 001` 形式）。
    pub id: String,
    /// カード名（英語）。
    pub name: String,
    /// 種類。
    pub kind: String,
    /// 進化段階。
    pub stage: Option<u8>,
    /// 進化元のカード名。
    pub evolves_from: Option<String>,
    /// 最大 HP。
    pub hp: Option<u32>,
    /// タイプ（日本語）。
    pub energy_type: Option<String>,
    /// 弱点のタイプ（日本語）。
    pub weakness: Option<String>,
    /// にげるコスト。
    pub retreat_cost: Option<usize>,
    /// 特性。
    pub ability: Option<PyAbilityInfo>,
    /// ワザ。
    pub attacks: Vec<PyAttackInfo>,
    /// トレーナーズの効果文（英語）。
    pub effect: Option<String>,
    /// 古代・未来のポケモンなら `古代` / `未来`。
    pub lineage: Option<String>,
}

impl From<CardDossier> for PyCardDossier {
    fn from(value: CardDossier) -> Self {
        Self {
            id: value.id,
            name: value.name,
            kind: value.kind,
            stage: value.stage,
            evolves_from: value.evolves_from,
            hp: value.hp,
            energy_type: value.energy_type,
            weakness: value.weakness,
            retreat_cost: value.retreat_cost,
            ability: value.ability.map(Into::into),
            attacks: value.attacks.into_iter().map(Into::into).collect(),
            effect: value.effect,
            lineage: value.lineage,
        }
    }
}

/// 指定した識別子のカードの性能を返す。カードを知らない相手に渡す資料に使う。
///
/// `PyO3` は Python のリストを所有した `Vec` としてしか受け取れないため、借用にはできない。
#[pyfunction]
#[allow(clippy::needless_pass_by_value)]
fn card_details(ids: Vec<String>) -> Vec<PyCardDossier> {
    card_dossiers(&ids).into_iter().map(Into::into).collect()
}

/// 未実装のカードだけを返す。
#[pyfunction]
fn incomplete_card_statuses() -> Vec<PyCardStatus> {
    incomplete_cards().into_iter().map(Into::into).collect()
}

/// ある座席での成績。
#[pyclass(name = "Record", frozen, get_all, skip_from_py_object)]
#[derive(Clone)]
pub struct PyRecord {
    /// 勝ち。
    pub wins: u32,
    /// 負け。
    pub losses: u32,
    /// 引き分け。
    pub ties: u32,
    /// 決着がつかなかった試合。
    pub unfinished: u32,
    /// 試合数。
    pub games: u32,
    /// 決着した試合数。
    pub decided: u32,
    /// 勝率。決着した試合がなければ `None`。
    pub win_rate: Option<f64>,
    /// 勝率の 95% 信頼区間。決着した試合がなければ `None`。
    pub confidence_interval_95: Option<(f64, f64)>,
}

impl From<Record> for PyRecord {
    fn from(record: Record) -> Self {
        Self {
            wins: record.wins,
            losses: record.losses,
            ties: record.ties,
            unfinished: record.unfinished,
            games: record.games(),
            decided: record.decided(),
            win_rate: record.win_rate(),
            confidence_interval_95: record.confidence_interval_95(),
        }
    }
}

#[pymethods]
impl PyRecord {
    fn __repr__(&self) -> String {
        format!(
            "Record(wins={}, losses={}, ties={}, unfinished={})",
            self.wins, self.losses, self.ties, self.unfinished
        )
    }
}

/// 相性評価の結果。
#[pyclass(name = "Matchup", frozen, get_all)]
pub struct PyMatchup {
    /// 評価対象デッキが先攻だった試合の成績。
    pub going_first: PyRecord,
    /// 評価対象デッキが後攻だった試合の成績。
    pub going_second: PyRecord,
    /// 先攻後攻を合算した成績。
    pub overall: PyRecord,
    /// 評価対象デッキ側の方策コード。
    pub strategy_a: String,
    /// 相手デッキ側の方策コード。
    pub strategy_b: String,
    /// 使ったマスターシード。
    pub seed: u64,
    /// 対戦に使った deckgym のコミットハッシュ。
    pub deckgym_revision: &'static str,
    /// 対戦に使ったエンジンのバージョン。
    pub engine_version: &'static str,
    /// 試合ごとの結果（`win` / `loss` / `tie` / `unfinished`）。試合の順で、前半が評価対象デッキの先攻。
    pub outcomes: Vec<&'static str>,
}

fn outcome_name(outcome: Outcome) -> &'static str {
    match outcome {
        Outcome::Win => "win",
        Outcome::Loss => "loss",
        Outcome::Tie => "tie",
        Outcome::Unfinished => "unfinished",
    }
}

#[pymethods]
impl PyMatchup {
    fn __repr__(&self) -> String {
        format!(
            "Matchup(games={}, win_rate={:?}, strategy_a={}, strategy_b={})",
            self.overall.games, self.overall.win_rate, self.strategy_a, self.strategy_b
        )
    }
}

/// 2 デッキを `games` 試合対戦させ、相性を返す。
///
/// 先攻後攻は半分ずつ入れ替える。同じ引数なら必ず同じ結果になる。
/// 方策コードは `r` / `aa` / `et` / `w` / `v` / `er` / `m` / `e[:深さ]`。
#[pyfunction]
#[pyo3(signature = (deck_a, deck_b, strategy_a, strategy_b, games, seed))]
fn evaluate_matchup(
    py: Python<'_>,
    deck_a: &str,
    deck_b: &str,
    strategy_a: &str,
    strategy_b: &str,
    games: u32,
    seed: u64,
) -> PyResult<PyMatchup> {
    let to_value_error = |err: &dyn std::fmt::Display| PyValueError::new_err(err.to_string());

    let deck_a = parse_deck(deck_a).map_err(|err| to_value_error(&err))?;
    let deck_b = parse_deck(deck_b).map_err(|err| to_value_error(&err))?;
    let strategy_a: Strategy = strategy_a.parse().map_err(|err| to_value_error(&err))?;
    let strategy_b: Strategy = strategy_b.parse().map_err(|err| to_value_error(&err))?;

    // 対戦は長時間 CPU を回すので GIL を手放す
    let matchup = py
        .detach(|| evaluate(&deck_a, &deck_b, strategy_a, strategy_b, games, seed))
        .map_err(|err| to_value_error(&err))?;

    Ok(PyMatchup {
        going_first: matchup.going_first.into(),
        going_second: matchup.going_second.into(),
        overall: matchup.overall().into(),
        strategy_a: matchup.strategy_a,
        strategy_b: matchup.strategy_b,
        seed: matchup.seed,
        deckgym_revision: matchup.deckgym_revision,
        engine_version: pocket_engine::engine_version(),
        outcomes: matchup.outcomes.iter().map(|o| outcome_name(*o)).collect(),
    })
}

/// 相性評価の 1 試合を、ログ付きで再現した結果。
#[pyclass(name = "MatchupGame", frozen, get_all, skip_from_py_object)]
pub struct PyMatchupGame {
    /// 対戦の記録。プレイヤー 0 が先攻。
    pub replay: PyReplay,
    /// 評価対象デッキが先攻だったか。
    pub a_is_first: bool,
    /// 評価対象デッキから見た結果（`win` / `loss` / `tie` / `unfinished`）。
    pub outcome: &'static str,
}

/// `evaluate_matchup` と同じ引数で、`index` 試合目（0 始まり）をログ付きで再現する。
#[pyfunction]
#[pyo3(signature = (deck_a, deck_b, strategy_a, strategy_b, games, seed, index))]
#[allow(clippy::too_many_arguments)]
fn replay_matchup_game(
    py: Python<'_>,
    deck_a: &str,
    deck_b: &str,
    strategy_a: &str,
    strategy_b: &str,
    games: u32,
    seed: u64,
    index: u32,
) -> PyResult<PyMatchupGame> {
    let to_value_error = |err: &dyn std::fmt::Display| PyValueError::new_err(err.to_string());

    let deck_a = parse_deck(deck_a).map_err(|err| to_value_error(&err))?;
    let deck_b = parse_deck(deck_b).map_err(|err| to_value_error(&err))?;
    let strategy_a: Strategy = strategy_a.parse().map_err(|err| to_value_error(&err))?;
    let strategy_b: Strategy = strategy_b.parse().map_err(|err| to_value_error(&err))?;

    let game = py
        .detach(|| replay_matchup(&deck_a, &deck_b, strategy_a, strategy_b, games, seed, index))
        .map_err(|err| to_value_error(&err))?;
    Ok(PyMatchupGame {
        replay: game.replay.into(),
        a_is_first: game.a_is_first,
        outcome: outcome_name(game.outcome),
    })
}

#[pymodule]
fn pocket_engine_py(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyCardStatus>()?;
    m.add_class::<PyAttackInfo>()?;
    m.add_class::<PyAbilityInfo>()?;
    m.add_class::<PyCardDossier>()?;
    m.add_class::<PyCardRef>()?;
    m.add_class::<PyPokemonSnapshot>()?;
    m.add_class::<PyReplayStep>()?;
    m.add_class::<PyReplay>()?;
    m.add_class::<PyRecord>()?;
    m.add_class::<PyMatchup>()?;
    m.add_class::<PyMatchupGame>()?;
    m.add_function(wrap_pyfunction!(engine_version, m)?)?;
    m.add_function(wrap_pyfunction!(deckgym_revision, m)?)?;
    m.add_function(wrap_pyfunction!(validate_deck, m)?)?;
    m.add_function(wrap_pyfunction!(normalize_deck, m)?)?;
    m.add_function(wrap_pyfunction!(card_statuses_all, m)?)?;
    m.add_function(wrap_pyfunction!(card_details, m)?)?;
    m.add_function(wrap_pyfunction!(incomplete_card_statuses, m)?)?;
    m.add_function(wrap_pyfunction!(evaluate_matchup, m)?)?;
    m.add_function(wrap_pyfunction!(replay_game, m)?)?;
    m.add_function(wrap_pyfunction!(replay_matchup_game, m)?)?;
    Ok(())
}
