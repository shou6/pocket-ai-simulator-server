//! ポケポケ固有の定石を入れた方策。
//!
//! deckgym 内蔵の方策は、進化もベンチ展開もしない見本実装であり、
//! 上位アーキタイプの主役カードを場に出せない。ここでは実際のプレイヤーが
//! 共有している定石（`docs/play-principles.md`）に沿って行動を評価する。
//!
//! # 1 手先を読まない理由
//!
//! deckgym の `forecast_action` と `apply_action` はどちらも `pub(crate)` で、
//! 外部クレートから「行動を適用した後の盤面」を得る手段がない。そのため
//! ここでは盤面と行動の意味から直接スコアを付ける。
//!
//! # ルールの再実装について
//!
//! ダメージの見積もりに弱点（+20）を含めるなど、一部でルールを近似している。
//! これは方策が内部で使う見積もりであり、対戦の進行そのものは deckgym に任せている。

use std::cell::Cell;
use std::collections::{HashMap, HashSet};
use std::fmt::Debug;
use std::sync::LazyLock;

use deckgym::actions::{Action, SimpleAction};
use deckgym::card_ids::CardId;
use deckgym::database::get_card_by_enum;
use deckgym::models::{Attack, Card, EnergyType, StatusCondition, TrainerCard, TrainerType};
use deckgym::players::Player;
use deckgym::state::PlayedCard;
use deckgym::{Deck, State};
use rand::rngs::StdRng;
use strum::IntoEnumIterator;

/// 弱点によるダメージの上乗せ。ポケポケでは固定 20。
const WEAKNESS_BONUS: u32 = 20;

/// 勝利に必要なポイント。
const POINTS_TO_WIN: f64 = 3.0;

/// きぜつを取れる行動の価値。ポイントが動くので最優先。
const KNOCKOUT_VALUE: f64 = 1000.0;

/// きぜつに至らないダメージ 1 割分の価値。
const DAMAGE_PROGRESS_VALUE: f64 = 100.0;

/// 進化の価値。HP とワザが上がり、盤面が強くなる。
const EVOLVE_VALUE: f64 = 120.0;

/// ベンチにポケモンを出す価値。出せなくなると即敗北するため厚みは重要。
///
/// エネルギーを付けるより優先する。ベンチが薄いとバトル場を 1 匹倒された時点で
/// 負けるため、序盤は展開が最優先になる。
const PLACE_VALUE: f64 = 110.0;

/// 進化系統のたねをベンチに出すときの加点。裏で育てるのが基本形。
const PLACE_EVOLUTION_BASIC_BONUS: f64 = 40.0;

/// 初期配置で 2 進化系のたねをバトル場に出すときの減点。
///
/// 序盤は繋ぎのポケモンで時間を稼ぎ、主役はベンチで育てる
/// （`docs/play-principles.md` 5 節）。完成まで 2 段階かかる系統に限る。
const SETUP_EVOLUTION_BASIC_PENALTY: f64 = 200.0;

/// 初期配置で 1 進化系のたねをバトル場に出すときの加点。
///
/// リオル → メガルカリオ ex のように 1 段で主役になる系統は、前に出して
/// 次の番に進化させるのが基本。対戦ログ：リオルをベンチに置き、
/// メガルカリオが 30 試合中 16 試合で一度も攻撃しなかった。
const SETUP_ONE_STAGE_BASIC_BONUS: f64 = 30.0;

/// 特性を使う価値。
const ABILITY_VALUE: f64 = 70.0;

/// 初期配置で、0 コストのワザを持つたねをバトル場に出す加点。最初の番から動ける。
const SETUP_ZERO_COST_BONUS: f64 = 40.0;

/// そのワザが相手をねむり・まひにするときの追加の加点。時間を稼げる。
const SETUP_ZERO_COST_LOCK_BONUS: f64 = 60.0;

/// 前に出すポケモンの残り HP（割合）に対する価値。
const PROMOTION_HEALTH_VALUE: f64 = 30.0;

/// 相手をねむり・まひにするワザの価値。相手の番をほぼ奪える。
const STATUS_SLEEP_PARALYSIS_VALUE: f64 = 80.0;

/// 相手をこんらんにするワザの価値。半分の確率でワザが失敗する。
const STATUS_CONFUSION_VALUE: f64 = 40.0;

/// 相手をやけど・どくにするワザの価値。毎ターン追加ダメージが入る。
const STATUS_DAMAGE_OVER_TIME_VALUE: f64 = 30.0;

/// 初ターンに進化できるたね（イーブイの「進化の加速」など）を前に出す加点。
///
/// この特性はバトル場にいるときだけ働くため、進化系統のたねでも前に出す。
const SETUP_FIRST_TURN_EVOLVER_BONUS: f64 = 150.0;

/// 効果を判別できないトレーナーズの価値。
///
/// 対応表にないカードをまったく使わないのは損なので、控えめな正の値にする。
const TRAINER_UNKNOWN_VALUE: f64 = 30.0;

/// 使うべきでないトレーナーズの評価。ターンを終えるより低くする。
pub const TRAINER_USELESS_VALUE: f64 = -40.0;

/// 進化パーツが足りないときのドローカードの加点。
const DRAW_FOR_EVOLUTION_BONUS: f64 = 60.0;

/// 引きずり出した相手を今のターンに倒せるときの妨害カードの価値。
const DISRUPT_WITH_KNOCKOUT_VALUE: f64 = 300.0;

/// 手札の上限。これに近いとドローカードが無駄になる。
const HAND_LIMIT: f64 = 10.0;

/// ベンチの枠数。
const BENCH_SLOTS: usize = 3;

/// にげるを検討し始める残り HP の割合。これより余裕があれば逃げない。
///
/// 余裕のある盤面から逃げるとエネルギーが遊び、1 ターン損をする。
const RETREAT_HP_THRESHOLD: f64 = 0.5;

/// にげるの基礎価値。きぜつを避けられる分だけ上乗せする。
const RETREAT_BASE_VALUE: f64 = -50.0;

/// 逃げ先に付いているエネルギー 1 個あたりの加点。すぐ攻撃できるほど良い。
const RETREAT_DESTINATION_ENERGY_VALUE: f64 = 30.0;

/// 育て終えた主力と入れ替えるときの加点。
///
/// 2 進化デッキは、序盤を繋ぎのポケモンで耐え、ベンチで育てた主役を
/// 完成後にバトル場へ出すのが基本形（`docs/play-principles.md` 5 節）。
const RETREAT_TO_STRONGER_VALUE: f64 = 250.0;

/// 前に出た瞬間に倒される主役に付けるときの下げ幅。
///
/// ベンチの「あと 1 個」（70）と「あと 2 個」（62）の差より大きくして、健在なほうを先に育てる。
const DYING_BENCH_MAIN_PENALTY: f64 = 15.0;

/// 殴れるバトル場から逃げてでも減らす価値のあるポイント差。
///
/// 3 点のメガ ex を 1 点の繋ぎに替える場面を通し、3 点を 2 点に替えるだけの場面は通さない。
const POINTS_WORTH_A_RETREAT: f64 = 2.0;

/// 逃げ先が今のバトル場より強いとみなす打点の差。
const STRONGER_DAMAGE_MARGIN: u32 = 20;

/// 前に出すと負け筋になるポケモンの減点。
///
/// 相手にあと何点渡せるかを数え、倒されたら 3 点に達するポケモンは
/// 他に選択肢があるかぎり前に出さない（`docs/play-principles.md` 2 節）。
const LOSING_PROMOTION_PENALTY: f64 = 400.0;

/// ターンを終える価値。他に何もなければ選ばれる。
const END_TURN_VALUE: f64 = 0.0;

/// 「してもよい」効果を使わない選択（`Noop`）の価値。効果を使う側より低くする。
const NOOP_VALUE: f64 = 0.0;

/// 相手に弱点を突かれる高得点のポケモン（ex・メガシンカ ex）を場に出すときの減点。
///
/// 相手の攻撃者がこちらの弱点タイプなら、ex は 2 回で倒され 2〜3 点を渡す。
/// 対戦ログ：草デッキ相手にメガアブソル ex（弱点：草）を出して 30 試合中 14 回倒されていた。
const EXPOSURE_PENALTY: f64 = 100.0;

/// 自傷する特性を、倒れる残り HP で使ったときの評価。
const SELF_KNOCKOUT_ABILITY_VALUE: f64 = -50.0;

/// 入れ替えたいのに逃げられないとき、にげるコスト分のエネルギーを付ける価値。
const RETREAT_ENERGY_VALUE: f64 = 60.0;

/// 入れ替えたい場面でのにげる補助（スピーダーなど）の価値。
const RETREAT_HELPER_VALUE: f64 = 120.0;

/// 相手のバトル場に付いているエネルギー 1 個あたりのナツメの加点。
const SABRINA_PER_ENERGY_VALUE: f64 = 25.0;

/// 全カードの「進化元」の対応表。レアキャンディで飛ばす中間進化を辿るのに使う。
///
/// デッキに Combusken が入っていなくても、Mega Blaziken ex（Combusken から進化）
/// の系統に Torchic が属することを判定できる。
static EVOLVES_FROM: LazyLock<HashMap<String, String>> = LazyLock::new(|| {
    CardId::iter()
        .filter_map(|id| match get_card_by_enum(id) {
            Card::Pokemon(pokemon) => pokemon
                .evolves_from
                .as_ref()
                .map(|from| (pokemon.name.clone(), from.clone())),
            Card::Trainer(_) => None,
        })
        .collect()
});

/// 進化系統の根元にあたるたねポケモンの名前を返す。たねならそのまま。
fn base_basic_name(name: &str) -> String {
    let mut current = name.to_string();
    // 進化段階は最大 2 なので、念のため上限を設けて辿る
    for _ in 0..4 {
        match EVOLVES_FROM.get(&current) {
            Some(from) => current = from.clone(),
            None => break,
        }
    }
    current
}

/// デッキに含まれる進化ポケモンの、根元のたねポケモンの名前を集める。
///
/// ここに含まれるたねは「ベンチで育てる主役の卵」であり、繋ぎとして
/// バトル場に出すべきではない。
#[must_use]
pub fn evolution_line_basics(deck: &Deck) -> HashSet<String> {
    deck.cards
        .iter()
        .filter_map(|card| match card {
            Card::Pokemon(pokemon) if pokemon.stage > 0 => Some(base_basic_name(&pokemon.name)),
            _ => None,
        })
        .collect()
}

/// デッキに含まれる進化系統ごとに、根元のたねの名前と最終進化の段数を返す。
///
/// アチャモ → メガバシャーモ ex なら 2、リオル → メガルカリオ ex なら 1。
/// 2 進化系のたねは裏で育て、1 進化系のたねは前に出してすぐ進化させる。
#[must_use]
pub fn evolution_line_top_stage(deck: &Deck) -> HashMap<String, u8> {
    let mut stages: HashMap<String, u8> = HashMap::new();
    for card in &deck.cards {
        let Card::Pokemon(pokemon) = card else {
            continue;
        };
        if pokemon.stage == 0 {
            continue;
        }
        let base = base_basic_name(&pokemon.name);
        let entry = stages.entry(base).or_insert(0);
        *entry = (*entry).max(pokemon.stage);
    }
    stages
}

/// 進化元ごとに、デッキに入っている進化先の (最大打点, 最大 HP) を集める。
fn evolution_options(deck: &Deck) -> HashMap<String, Vec<(u32, u32)>> {
    let mut options: HashMap<String, Vec<(u32, u32)>> = HashMap::new();
    for card in &deck.cards {
        let Card::Pokemon(pokemon) = card else {
            continue;
        };
        let Some(from) = pokemon.evolves_from.as_ref() else {
            continue;
        };
        let damage = pokemon
            .attacks
            .iter()
            .map(|attack| attack.fixed_damage)
            .max()
            .unwrap_or(0);
        options
            .entry(from.clone())
            .or_default()
            .push((damage, pokemon.hp));
    }
    options
}

/// デッキから導いた、方策が判断に使う文脈。
#[derive(Debug, Clone, Default)]
pub struct PlayerContext {
    /// 進化系統の根元にあたるたねポケモンの名前。
    pub evolution_basics: HashSet<String>,
    /// 進化系統ごとの最終進化の段数（根元のたねの名前 → 段数）。
    pub line_top_stage: HashMap<String, u8>,
    /// 進化元の名前 → デッキに入っている進化先の (最大打点, 最大 HP) の一覧。
    ///
    /// 「より優れた進化先がデッキにあるなら、劣るほうには進化しない」判断に使う。
    /// 自分のデッキの中身は当然知っている情報なので、手札にある候補だけで比べる必要はない。
    pub evolution_options: HashMap<String, Vec<(u32, u32)>>,
    /// この番、バトル場にエネルギーゾーンのエネルギーを付けられないか
    /// （アローラキュウコン ex「Binding Snow」の返し）。deckgym の状態からは読めないので、
    /// 合法手にバトル場への付与がないことから判断し、手番ごとに更新する
    pub active_energy_locked: Cell<bool>,
}

impl PlayerContext {
    /// デッキから文脈を組み立てる。
    #[must_use]
    pub fn from_deck(deck: &Deck) -> Self {
        Self {
            active_energy_locked: Cell::new(false),
            evolution_basics: evolution_line_basics(deck),
            line_top_stage: evolution_line_top_stage(deck),
            evolution_options: evolution_options(deck),
        }
    }

    /// このたねの進化系統の最終段数。進化しないなら 0。
    fn line_top_stage_of(&self, card: &Card) -> u8 {
        match card {
            Card::Pokemon(pokemon) if pokemon.stage == 0 => {
                self.line_top_stage.get(&pokemon.name).copied().unwrap_or(0)
            }
            _ => 0,
        }
    }

    /// このカードが、ベンチで育てるべき進化系統のたねか。
    pub(crate) fn is_evolution_basic(&self, card: &Card) -> bool {
        match card {
            Card::Pokemon(pokemon) => {
                pokemon.stage == 0 && self.evolution_basics.contains(&pokemon.name)
            }
            Card::Trainer(_) => false,
        }
    }

    /// バトル場にいれば初ターンでも進化できる特性を持つか（イーブイの「進化の加速」など）。
    fn is_first_turn_evolver(card: &Card) -> bool {
        match card {
            Card::Pokemon(pokemon) => is_active_self_evolver_effect(
                pokemon
                    .ability
                    .as_ref()
                    .map(|ability| ability.effect.as_str()),
            ),
            Card::Trainer(_) => false,
        }
    }

    /// このポケモンが主役（進化系統のたね、または進化済み）か。
    pub(crate) fn is_main_line(&self, card: &Card) -> bool {
        match card {
            Card::Pokemon(pokemon) => {
                pokemon.stage > 0
                    || self.evolution_basics.contains(&pokemon.name)
                    // 進化しないたねでも、2 点以上を渡すうえに自分で殴れる ex はデッキの主役。
                    // 進化系統だけを主役とみなしていたため、メガアブソル ex（たね HP170、
                    // 悪 2 個で 80）が繋ぎ扱いになり、94 試合中 51% でワザに必要な
                    // 2 個に届いていなかった（ユーザーの依頼で調べたサザンドラ戦の敗因）。
                    // 打点で線を引くのは、同じたねの ex でもツボツボ ex（打点 20）は
                    // 殴らせる相手ではなく壁だから
                    || (knockout_points(card) >= 2.0
                        && attack_power_and_stage(card).0 >= BASIC_MAIN_DAMAGE)
            }
            Card::Trainer(_) => false,
        }
    }
}

/// たねの ex を主役とみなす最低打点。これ未満は壁や繋ぎとして扱う。
const BASIC_MAIN_DAMAGE: u32 = 50;

/// きぜつしたときに相手が得るポイント数。
///
/// deckgym の `Card::get_knockout_points` は `pub(crate)` で呼べないため、
/// 同じ判定をここに持つ。
pub(crate) fn knockout_points(card: &Card) -> f64 {
    let Card::Pokemon(pokemon) = card else {
        return 1.0;
    };
    if pokemon.name.starts_with("Mega ") {
        3.0
    } else if pokemon.name.to_lowercase().split(' ').next_back() == Some("ex") {
        2.0
    } else {
        1.0
    }
}

/// カードの最大 HP。トレーナーズなら 0。
pub(crate) fn max_hp_points(card: &Card) -> u32 {
    match card {
        Card::Pokemon(pokemon) => pokemon.hp,
        Card::Trainer(_) => 0,
    }
}

/// カードの最大 HP（計算用）。
fn max_hp(card: &Card) -> f64 {
    f64::from(max_hp_points(card))
}

/// カードのタイプ。トレーナーズなら `None`。
fn energy_type_of(card: &Card) -> Option<EnergyType> {
    match card {
        Card::Pokemon(pokemon) => Some(pokemon.energy_type),
        Card::Trainer(_) => None,
    }
}

/// カードの弱点。
fn weakness_of(card: &Card) -> Option<EnergyType> {
    match card {
        Card::Pokemon(pokemon) => pokemon.weakness,
        Card::Trainer(_) => None,
    }
}

/// 今のターンにワザを使うべきか。
///
/// ワザを使うとターンが終わる。グッズや展開など、まだ価値のある行動が残っている
/// うちは攻撃しない。人間は必ず攻撃を最後に行う。
///
/// # 引数
///
/// - `best_other`：ワザとターン終了以外で最も評価の高い行動の評価。なければ `None`
#[must_use]
pub fn should_attack_now(best_other: Option<f64>) -> bool {
    best_other.is_none_or(|score| score <= 0.0)
}

/// 入れ替えたいのに逃げられないとき、にげるコスト分のエネルギーを付ける価値。
///
/// # 引数
///
/// - `wants_swap`：裏に育った主役がいて、バトル場と入れ替えたいか
/// - `attached`：バトル場に付いているエネルギーの数
/// - `retreat_cost`：バトル場のにげるコスト
#[must_use]
pub fn retreat_energy_bonus(wants_swap: bool, attached: usize, retreat_cost: usize) -> f64 {
    if wants_swap && attached < retreat_cost {
        RETREAT_ENERGY_VALUE
    } else {
        0.0
    }
}

/// どうぐの効果が、その付け先で働くか。
///
/// 効果文に「たねポケモン」「[W] ポケモン」のような条件が付いたどうぐがある。
/// 条件を満たさない相手に付けても何も起きない（ユーザーの指摘：小さなふうせんを
/// 2 進化のメガチルタリス ex に付けていた）。
///
/// # 引数
///
/// - `stage`：付け先の進化段階（0 = たね、1 = 1 進化、2 = 2 進化）
/// - `energy_type`：付け先のタイプ。不明なら `None`（条件は満たせないものとして扱う）
#[must_use]
pub fn tool_applies_to(tool: &str, stage: u8, energy_type: Option<&str>) -> bool {
    match tool {
        // 「たねポケモン」限定
        "Small Balloon" => stage == 0,
        // 「1 進化ポケモン」限定
        "Elegant Cape" => stage == 1,
        // タイプ限定
        "Inflatable Boat" => energy_type == Some("Water"),
        "Leaf Cape" => energy_type == Some("Grass"),
        _ => true,
    }
}

/// どうぐの付け先 1 つのスコア。効果が働くかを見てから、役割に合うかを見る。
///
/// # 引数
///
/// - `stage` / `energy_type`：付け先の進化段階とタイプ。効果の条件判定に使う
#[must_use]
pub fn score_tool_target_for(
    tool: &str,
    is_active: bool,
    wants_swap: bool,
    is_main: bool,
    stage: u8,
    energy_type: Option<&str>,
) -> f64 {
    if !tool_applies_to(tool, stage, energy_type) {
        return TRAINER_USELESS_VALUE;
    }
    score_tool_target(tool, is_active, wants_swap, is_main)
}

/// どうぐの付け先 1 つのスコア。役割に合うポケモンに付ける。
///
/// 効果の条件は見ない。条件込みで判断するときは `score_tool_target_for` を使う。
///
/// # 引数
///
/// - `tool`：どうぐの名前
/// - `is_active`：付け先がバトル場か
/// - `wants_swap`：バトル場を裏の主役と入れ替えたいか
/// - `is_main`：付け先が主役か
#[must_use]
pub fn score_tool_target(tool: &str, is_active: bool, wants_swap: bool, is_main: bool) -> f64 {
    match tool {
        // にげるコストを下げる。入れ替えたいバトル場のポケモンに付ける
        "Small Balloon" | "Inflatable Boat" | "Air Balloon" => {
            if is_active && wants_swap {
                150.0
            } else if is_active {
                40.0
            } else {
                10.0
            }
        }
        // HP を上げる。長く戦う主役に付ける
        "Leaf Cape" | "Giant Cape" | "Elegant Cape" => {
            if is_main {
                90.0
            } else {
                40.0
            }
        }
        // ベンチを守る。ベンチにいる主役に付ける
        "Protective Poncho" => {
            if !is_active && is_main {
                80.0
            } else {
                10.0
            }
        }
        // 殴られたときに働く。バトル場に付ける
        "Rocky Helmet" | "Deceptive Needle" if is_active => 80.0,
        _ => 30.0,
    }
}

/// 裏に育った主役がいて、バトル場と入れ替えたい状態か。
fn wants_swap(ctx: &PlayerContext, state: &State, me: usize) -> bool {
    let Some(active) = state.in_play_pokemon[me][0].as_ref() else {
        return false;
    };
    // バトル場は「この番に 1 個付ければ撃てる打点」で見る。
    // 1 個足りないだけの主役を、今撃てる繋ぎより弱いと誤判定しないため
    let current = own_active_attainable(ctx, active);
    let stronger_behind = state
        .enumerate_in_play_pokemon(me)
        .filter(|(index, _)| *index != 0)
        .any(|(_, pokemon)| {
            best_ready_damage(pokemon) > current.saturating_add(STRONGER_DAMAGE_MARGIN)
        });
    stronger_behind || wall_available_for_main(ctx, state, me, active)
}

/// 主役が撃てず、健在な壁がベンチにいるか（主役は壁の後ろで完成させる）。
fn wall_available_for_main(
    ctx: &PlayerContext,
    state: &State,
    me: usize,
    active: &PlayedCard,
) -> bool {
    // 主役は今のエネルギーで撃てなければ壁の後ろへ。3 点のメガ ex は、この番に付けても
    // 撃てないなら 1 点のポケモンの後ろへ（1 個足りないだけの主役は前で殴る：既存の定石）
    // 逃げる判断では壁だけを見る。前にいる 3 点のメガは、1 個足りないだけなら付けて殴る
    // （既存の定石）。撃てないメガを前に「置きに行かない」判断は進化の先送りと
    // 逃げ先の選択で行う
    shelter_available(
        ctx,
        state,
        me,
        &active.card,
        best_ready_damage(active) > 0,
        true,
    )
}

/// 守りたいポケモン（主役、または 3 点のメガ ex）が撃てず、後ろに隠せる相手がベンチにいるか。
///
/// - `can_attack_now`：今のエネルギーで撃てるか（壁の後ろに下がる判断）
/// - `can_attack_this_turn`：この番に 1 個付ければ撃てるか（3 点のメガ ex を隠す判断）
fn shelter_available(
    ctx: &PlayerContext,
    state: &State,
    me: usize,
    protected: &Card,
    can_attack_now: bool,
    can_attack_this_turn: bool,
) -> bool {
    let points = knockout_points(protected);
    let wants_wall = ctx.is_main_line(protected) && !can_attack_now;
    let wants_shelter = points >= 3.0 && !can_attack_this_turn;
    if !wants_wall && !wants_shelter {
        return false;
    }
    state
        .enumerate_in_play_pokemon(me)
        .filter(|(index, _)| *index != 0)
        .any(|(_, p)| {
            (wants_wall && is_wall_played(ctx, p))
                || (wants_shelter && is_shelter_played(ctx, points, p))
        })
}

/// 場のポケモンが `protected_points` のポケモンを隠せる相手か。
fn is_shelter_played(ctx: &PlayerContext, protected_points: f64, pokemon: &PlayedCard) -> bool {
    let healthy = pokemon.get_remaining_hp() * 2 >= max_hp_points(&pokemon.card);
    is_shelter_for(
        protected_points,
        knockout_points(&pokemon.card),
        healthy,
        is_true_wall_card(ctx, &pokemon.card),
    )
}

/// 隠す相手として成り立つか。
///
/// 壁（受けるダメージ −N か打点 30 以下のたね）なら何でも隠せる。壁がなくても、
/// 3 点のメガ ex は健在な 1 点のポケモンの後ろに置く（ユーザーの指摘：撃てない
/// メガバシャーモ ex を前に放置して殴られ続けた。メガ系は倒されたら終わり）。
#[must_use]
pub fn is_shelter_for(
    protected_points: f64,
    shelter_points: f64,
    shelter_healthy: bool,
    shelter_is_wall: bool,
) -> bool {
    if !shelter_healthy {
        return false;
    }
    shelter_is_wall || (protected_points >= 3.0 && shelter_points <= 1.0)
}

/// 場のポケモンが健在な壁か（HP 半分以上）。
///
/// 入れ替えとエネルギーの判断では、壁を「受けることが役割のポケモン」に絞る。
/// HP が高いだけのたね（ダークライ、フーパ ex など）はアタッカーであり、
/// その後ろに主役を下げると攻撃の手が止まる
fn is_wall_played(ctx: &PlayerContext, pokemon: &PlayedCard) -> bool {
    is_true_wall_card(ctx, &pokemon.card)
        && pokemon.get_remaining_hp() * 2 >= max_hp_points(&pokemon.card)
}

/// 受けることが役割のたねか。「受けるダメージ −N」の特性を持つ、または打点が 30 以下。
fn is_true_wall_card(ctx: &PlayerContext, card: &Card) -> bool {
    if !is_wall_card(ctx, card) {
        return false;
    }
    let (power, _) = attack_power_and_stage(card);
    has_damage_reduction(card) || power <= 30
}

/// この番に逃げるコストを払えるか（付いているエネルギーと手札のスピーダー）。
fn can_retreat_this_turn(state: &State, me: usize, active: &PlayedCard) -> bool {
    let speeds = state.hands[me]
        .iter()
        .filter(|card| card.get_name() == "X Speed")
        .count();
    active.attached_energy.len() + speeds >= retreat_cost_of(&active.card)
}

/// この番のエネルギー（まだ付けていない分）も使えば逃げるコストを払えるか。
fn can_pay_retreat_this_turn(state: &State, me: usize, active: &PlayedCard) -> bool {
    let turn_energy = usize::from(state.energy_zone[me].current.is_some());
    let speeds = state.hands[me]
        .iter()
        .filter(|card| card.get_name() == "X Speed")
        .count();
    active.attached_energy.len() + speeds + turn_energy >= retreat_cost_of(&active.card)
}

/// 進化のスコア。
fn score_evolve(
    ctx: &PlayerContext,
    state: &State,
    me: usize,
    evolution: &Card,
    in_play_idx: usize,
) -> f64 {
    let opponent = (me + 1) % 2;
    let target = state.in_play_pokemon[me][in_play_idx].as_ref();
    // 壁の後ろに下がる主役は、逃げるコストの軽い進化前のうちに逃げる。
    // 進化してから逃げようとするとコストが上がって逃げられない
    if in_play_idx == 0
        && std::env::var_os("POCKET_NO_EVOLVE_DEFER").is_none()
        && target
            .is_some_and(|active| evolve_on_active_should_wait(ctx, state, me, active, evolution))
    {
        return EVOLVE_DEFERRED_VALUE;
    }
    let target_bonus = target.map_or(0.0, |pokemon| {
        evolve_target_bonus(
            in_play_idx,
            pokemon.get_remaining_hp(),
            pokemon.attached_energy.len(),
        )
    });
    EVOLVE_VALUE + max_hp(evolution) / 10.0 + target_bonus
        - exposure_penalty(
            knockout_points(evolution),
            weakness_exposed(state, opponent, evolution),
        )
}

/// バトル場での進化を、逃げた後に回すべきか。
///
/// 進化後にこの番のエネルギーを付けても撃てず、後ろに隠せる相手がベンチにいて、
/// 今の姿なら逃げるコストを払えるとき。
fn evolve_on_active_should_wait(
    ctx: &PlayerContext,
    state: &State,
    me: usize,
    active: &PlayedCard,
    evolution: &Card,
) -> bool {
    let turn_energy = usize::from(state.energy_zone[me].current.is_some());
    let can_attack_after = attack_requirements(evolution)
        .iter()
        .any(|required| energy_missing(required, &active.attached_energy) <= turn_energy);
    // 3 点のメガを前で進化させて撃てないまま晒すのが問題になるのは、先攻でエネルギーが
    // 遅れているとき、または相手が自分の弱点タイプのとき（ユーザーの指摘：先攻・後攻と
    // タイプ相性で手を変える）。後攻で弱点も突かれないなら、前で進化して次の番に撃つ
    let opponent = (me + 1) % 2;
    let exposed_matters = is_going_first(me) || weakness_exposed(state, opponent, evolution);
    // 先攻や弱点を突かれる盤面で、撃てない 3 点のメガを前で作るのは、逃げるコストを
    // 払えるかに関わらず見送る（エネルギーが先にベンチへ行き、逃げられなくなってから
    // 前で進化する流れを防ぐ）
    if exposed_matters && shelter_available(ctx, state, me, evolution, false, can_attack_after) {
        return true;
    }
    evolve_before_hiding_is_premature(
        wall_available_for_main(ctx, state, me, active),
        can_pay_retreat_this_turn(state, me, active),
    )
}

/// 自分が先攻か。`game.rs` の `new_game` でプレイヤー 0 を先攻に固定している。
fn is_going_first(me: usize) -> bool {
    me == 0
}

/// ふしぎなアメを今使うと、バトル場の進化前を先送りすべき場面で進化させてしまうか。
///
/// 進化先がバトル場のポケモンしかないと deckgym が自動で確定するため、
/// トレーナーズを使う時点で止める（ユーザーの指摘：アチャモを前でメガバシャーモ ex にし、
/// 撃てないまま殴られ続けた）。
fn rare_candy_is_premature(ctx: &PlayerContext, state: &State, me: usize) -> bool {
    let Some(active) = state.in_play_pokemon[me][0].as_ref() else {
        return false;
    };
    let stage_twos: Vec<&Card> = state.hands[me]
        .iter()
        .filter(|card| matches!(card, Card::Pokemon(p) if p.stage == 2))
        .collect();
    let mut active_is_target = false;
    for evolution in &stage_twos {
        let base = base_basic_name(&evolution.get_name());
        // この番に出したポケモンにはふしぎなアメを使えない
        let on_bench = state
            .enumerate_in_play_pokemon(me)
            .filter(|(index, _)| *index != 0)
            .any(|(_, p)| p.card.get_name() == base && !p.played_this_turn);
        if on_bench {
            // ベンチの進化前に使える。先送りの対象にならない
            return false;
        }
        if active.card.get_name() == base
            && evolve_on_active_should_wait(ctx, state, me, active, evolution)
        {
            active_is_target = true;
        }
    }
    active_is_target
}

/// 逃げた後に進化させるべきか（先に進化すると逃げるコストが上がる）。
///
/// ユーザーの指摘：ミツハニー（コスト 1）のうちに逃げず、ビークイン ex（コスト 2）に
/// 進化してから逃げようとして逃げられなくなっていた。
#[must_use]
pub fn evolve_before_hiding_is_premature(hiding_planned: bool, can_retreat_now: bool) -> bool {
    hiding_planned && can_retreat_now
}

/// 逃げた後に進化させる場合の、バトル場での進化の価値。逃げる（200）やスピーダー（120）より低く、
/// 逃げた後にベンチで進化する分は通常の価値で評価される
const EVOLVE_DEFERRED_VALUE: f64 = 50.0;

/// 主役を壁の後ろに下げるべきか。
///
/// 主役がまだ撃てず、健在な壁がベンチにいるなら入れ替えて裏で完成させる
/// （ユーザーの指摘：E1 のビークイン ex がスピーダー 2 枚とツボツボ ex を持ちながら前に居座った）。
#[must_use]
pub fn main_should_hide_behind_wall(
    active_is_main: bool,
    active_can_attack: bool,
    wall_ready_on_bench: bool,
) -> bool {
    active_is_main && !active_can_attack && wall_ready_on_bench
}

/// にげるコスト（エネルギーの数）。トレーナーズなら 0。
fn retreat_cost_of(card: &Card) -> usize {
    match card {
        Card::Pokemon(pokemon) => pokemon.retreat_cost.len(),
        Card::Trainer(_) => 0,
    }
}

/// 残り HP が相手の打点に耐えられるか。
///
/// ちょうど同じダメージならきぜつするので、上回っている必要がある。
#[must_use]
pub fn survives_next_attack(remaining_hp: u32, incoming_damage: u32) -> bool {
    remaining_hp > incoming_damage
}

/// ワザのコストに対して、あと何個エネルギーが足りないか。タイプ付きで照合する。
///
/// タイプ指定（草など）は同じタイプでしか払えず、無色はどのタイプでも払える。
/// 個数だけで数えると、草・草のワザに水を付けても「足りている」と誤判定する。
///
/// # 引数
///
/// - `required`：ワザのコスト
/// - `attached`：付いているエネルギー
#[must_use]
pub fn energy_missing(required: &[EnergyType], attached: &[EnergyType]) -> usize {
    let mut pool: Vec<EnergyType> = attached.to_vec();
    let mut missing = 0usize;
    let mut colorless = 0usize;

    // まずタイプ指定を同じタイプで払う
    for energy in required {
        if *energy == EnergyType::Colorless {
            colorless += 1;
            continue;
        }
        match pool.iter().position(|have| have == energy) {
            Some(index) => {
                pool.swap_remove(index);
            }
            None => missing += 1,
        }
    }
    // 残りで無色を払う
    missing + colorless.saturating_sub(pool.len())
}

/// このタイプのエネルギーを付けると、いずれかのワザに近づくか。
///
/// 草・草のワザしかないポケモンに水を付けても攻撃には近づかない。
/// そうしたエネルギーは付けても無駄になる。
///
/// # 引数
///
/// - `attacks`：そのポケモンの各ワザのコスト
/// - `attached`：付いているエネルギー
/// - `incoming`：付けようとしているエネルギーのタイプ
#[must_use]
pub fn energy_is_useful(
    attacks: &[Vec<EnergyType>],
    attached: &[EnergyType],
    incoming: EnergyType,
) -> bool {
    let mut after: Vec<EnergyType> = attached.to_vec();
    after.push(incoming);
    attacks
        .iter()
        .any(|required| energy_missing(required, &after) < energy_missing(required, attached))
}

/// この番にエネルギーを 1 個付ければ撃てるワザの最大打点。
///
/// 入れ替えの判断で「今この瞬間に撃てる打点」だけを見ると、エネルギーが 1 個
/// 足りないだけの主役を、30 ダメージの繋ぎより弱いと誤判定する。
///
/// # 引数
///
/// - `attacks`：（コスト, 基礎ダメージ）の一覧
/// - `attached`：付いているエネルギー
#[must_use]
pub fn attainable_damage(attacks: &[(Vec<EnergyType>, u32)], attached: &[EnergyType]) -> u32 {
    attacks
        .iter()
        .filter(|(required, _)| energy_missing(required, attached) <= 1)
        .map(|(_, damage)| *damage)
        .max()
        .unwrap_or(0)
}

/// そのポケモンについて、この番に 1 個付ければ撃てる最大打点。
pub(crate) fn attainable_damage_of(pokemon: &PlayedCard) -> u32 {
    let Card::Pokemon(card) = &pokemon.card else {
        return 0;
    };
    let attacks: Vec<(Vec<EnergyType>, u32)> = card
        .attacks
        .iter()
        .map(|attack| (attack.energy_required.clone(), attack.fixed_damage))
        .collect();
    attainable_damage(&attacks, &pokemon.attached_energy)
}

/// 自分のバトルポケモンの「この番に撃てる打点」。
///
/// 通常は 1 個付ければ撃てる打点だが、付けられない番（Binding Snow の返し）は今の打点。
fn own_active_attainable(ctx: &PlayerContext, active: &PlayedCard) -> u32 {
    active_attainable_damage(
        ctx.active_energy_locked.get(),
        best_ready_damage(active),
        attainable_damage_of(active),
    )
}

/// バトル場の打点の見積もり。付けられない番は今撃てる打点で比べる。
///
/// アローラキュウコン ex「Binding Snow」の返しに、1 個付ければ撃てる打点で
/// バトル場を評価すると、付けられないのに強いと誤認してベンチと入れ替えない。
#[must_use]
pub fn active_attainable_damage(locked: bool, ready: u32, attainable: u32) -> u32 {
    if locked { ready } else { attainable }
}

/// 合法手から「バトル場にエネルギーゾーンのエネルギーを付けられない番か」を判断する。
///
/// ベンチへの付与があるのにバトル場への付与がなければ、バトル場だけ禁止されている。
fn active_energy_locked(state: &State, me: usize, possible_actions: &[Action]) -> bool {
    if state.in_play_pokemon[me][0].is_none() {
        return false;
    }
    let mut bench_attach = false;
    for action in possible_actions {
        if let SimpleAction::Attach {
            attachments,
            is_turn_energy: true,
        } = &action.action
        {
            if attachments.iter().any(|(_, _, idx)| *idx == 0) {
                return false;
            }
            bench_attach = true;
        }
    }
    bench_attach
}

/// そのポケモンの各ワザのコスト。
fn attack_requirements(card: &Card) -> Vec<Vec<EnergyType>> {
    match card {
        Card::Pokemon(pokemon) => pokemon
            .attacks
            .iter()
            .map(|attack| attack.energy_required.clone())
            .collect(),
        Card::Trainer(_) => Vec::new(),
    }
}

/// そのポケモンが今使えるワザの最大打点。エネルギーが足りないワザは数えない。
pub(crate) fn best_ready_damage(pokemon: &PlayedCard) -> u32 {
    let Card::Pokemon(card) = &pokemon.card else {
        return 0;
    };
    card.attacks
        .iter()
        .filter(|attack| energy_missing(&attack.energy_required, &pokemon.attached_energy) == 0)
        .map(|attack| attack.fixed_damage)
        .max()
        .unwrap_or(0)
}

/// 弱点を含めたダメージ。
pub(crate) fn with_weakness(damage: u32, attacker: &Card, defender: &Card) -> u32 {
    if damage > 0
        && weakness_of(defender).is_some()
        && weakness_of(defender) == energy_type_of(attacker)
    {
        damage + WEAKNESS_BONUS
    } else {
        damage
    }
}

/// 相手のバトルポケモンが、今のエネルギーで `target` に与えられる打点。
pub(crate) fn incoming_damage_to(state: &State, opponent: usize, target: &PlayedCard) -> u32 {
    // 相手は次の番にエネルギーを 1 個付けてから攻撃できる。
    // 今のエネルギーだけで見ると、ワザで捨てた直後の主役を打点 0 と誤る
    let attacker = state.in_play_pokemon[opponent][0].as_ref();
    let raw = attacker.map_or(0, |attacker| {
        with_weakness(attainable_damage_of(attacker), &attacker.card, &target.card)
    });
    // 眠り・まひの相手は次の番に動けない。まひは確実に 1 番飛ぶので 0、
    // 眠りは番の終わりのコインで覚めるので半分と見る。
    // これを見ていなかったため、眠らせても「次の番に殴られる」と評価され、
    // 眠らせる価値がまるごと消えていた（ユーザーの依頼で調べたメガチルタリス戦）
    let raw = match attacker {
        Some(attacker) if attacker.is_paralyzed() => 0,
        Some(attacker) if attacker.is_asleep() => raw / 2,
        _ => raw,
    };
    if raw == 0 {
        return 0;
    }
    // 受けるダメージを減らす特性（ツボツボ ex の Solid Shell −20）
    let reduction = match &target.card {
        Card::Pokemon(pokemon) => {
            damage_reduction_of_text(pokemon.ability.as_ref().map(|a| a.effect.as_str()))
        }
        Card::Trainer(_) => 0,
    };
    raw.saturating_sub(reduction)
}

/// 相手のバトル場が、こちらの次の番にバトル場へのエネルギー付与を封じられるか。
///
/// アローラキュウコン ex の「Binding Snow」（水 2 個で 80。相手の次の番、
/// エネルギーゾーンからバトル場へ付けられない）のようなワザ。今のエネルギーで
/// 撃てるかどうかまで見る。
///
/// 撃たれると、バトル場に置いた主役はエネルギーが乗らず、削られるだけになる。
/// 先読みは 1 番先の盤面しか見ないので、この「次の番も育たない」を評価に足す必要がある
/// （ユーザーの指摘：メガルカリオ ex を前で作り、Binding Snow を受け続けて E1 のまま
/// 削られ、最後は逃げてエネルギーを捨てていた）。
#[must_use]
pub fn opponent_can_lock_active_energy(state: &State, opponent: usize) -> bool {
    let Some(attacker) = state.in_play_pokemon[opponent][0].as_ref() else {
        return false;
    };
    let Card::Pokemon(card) = &attacker.card else {
        return false;
    };
    card.attacks.iter().any(|attack| {
        attack
            .effect
            .as_deref()
            .is_some_and(|effect| effect.contains("can't take any Energy from their Energy Zone"))
            && energy_missing(&attack.energy_required, &attacker.attached_energy) == 0
    })
}

/// 「This Pokémon takes -20 damage from attacks.」の 20。該当しなければ 0。
#[must_use]
pub fn damage_reduction_of_text(ability_effect: Option<&str>) -> u32 {
    let Some(text) = ability_effect else {
        return 0;
    };
    if !is_damage_reduction_text(text) {
        return 0;
    }
    text.split_once("takes -")
        .and_then(|(_, rest)| rest.split(' ').next())
        .and_then(|n| n.parse::<u32>().ok())
        .unwrap_or(0)
}

/// ワザの効果文から読み取った価値。
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct AttackEffectValue {
    /// 効果による追加ダメージ（ベンチ数などに応じて決まる分）。
    pub extra_damage: u32,
    /// 状態異常など、ダメージ以外の価値。
    pub status_bonus: f64,
    /// 相手のバトルポケモンをやけどにするか。
    pub applies_burn: bool,
    /// 相手のバトルポケモンをどくにするか。
    pub applies_poison: bool,
}

/// やけど 1 回分のダメージ。自分の番の終わりのポケモンチェックで入る。
const BURN_DAMAGE: u32 = 20;

/// どく 1 回分のダメージ。同上。
const POISON_DAMAGE: u32 = 10;

/// ワザのダメージに、自分の番の終わりに入るやけど・どくの分を足す。
///
/// メガバーニング（弱点込み 140 + やけど 20）で 160 HP のメガジュカインを倒せるのに、
/// 倒せないと見てアカギで 1 点のバタフリーを取っていた（ユーザーの指摘）。
///
/// # 引数
///
/// - `burned`：このワザで相手がやけどになる、またはすでにやけどか（重複しない）
/// - `poisoned`：同じくどくか
#[must_use]
pub fn damage_with_status(base: u32, burned: bool, poisoned: bool) -> u32 {
    let burn = if burned { BURN_DAMAGE } else { 0 };
    let poison = if poisoned { POISON_DAMAGE } else { 0 };
    base + burn + poison
}

/// ワザの効果文を評価する（状態異常など、ダメージ以外の価値）。
///
/// ダメージの増減は [`expected_damage`]、ベンチへのダメージや自傷は [`attack_side_effects`] で読む。
/// `extra_damage` は互換のため「自分のベンチ 1 匹につき N」だけを返す。
#[must_use]
pub fn attack_effect_value(effect: Option<&str>, own_bench_count: usize) -> AttackEffectValue {
    let Some(text) = effect else {
        return AttackEffectValue::default();
    };

    // 「This attack does N more damage for each of your Benched Pokémon.」
    let extra_damage = text
        .split_once(" more damage for each of your Benched Pok")
        .and_then(|(head, _)| head.rsplit(' ').next())
        .and_then(|n| n.parse::<u32>().ok())
        .map_or(0, |per| per * own_bench_count as u32);

    // 相手のバトルポケモンに状態異常を与える効果。
    // ねむり・まひは相手の番を奪うので、ダメージがなくても価値が高い
    let targets_opponent = text.contains("opponent's Active Pok");
    let status_bonus = if !targets_opponent {
        0.0
    } else if text.contains("now Asleep") || text.contains("now Paralyzed") {
        STATUS_SLEEP_PARALYSIS_VALUE
    } else if text.contains("now Confused") {
        STATUS_CONFUSION_VALUE
    } else if text.contains("now Burned") || text.contains("now Poisoned") {
        STATUS_DAMAGE_OVER_TIME_VALUE
    } else {
        0.0
    };

    AttackEffectValue {
        extra_damage,
        status_bonus,
        applies_burn: targets_opponent && text.contains("now Burned"),
        applies_poison: targets_opponent && text.contains("now Poisoned"),
    }
}

/// ワザのダメージを見積もるための盤面の情報。
///
/// 条件付きの加算を判定する真偽値がまとまっているため、構造体の bool の数の指摘は許容する
#[derive(Debug, Clone, Default)]
#[allow(clippy::struct_excessive_bools)]
pub struct AttackContext {
    /// ワザの基礎ダメージ。
    pub base_damage: u32,
    /// 自分のベンチの数。
    pub own_bench: usize,
    /// 相手のベンチの数。
    pub opponent_bench: usize,
    /// 相手のバトルポケモンに付いているエネルギーの数。
    pub opponent_active_energy: usize,
    /// 自分のバトルポケモンにどうぐが付いているか。
    pub has_tool: bool,
    /// ワザのコストを超えて付いている、自分のタイプのエネルギーの数。
    pub extra_energy: usize,
    /// 相手のバトルポケモンが ex か。
    pub opponent_is_ex: bool,
    /// 相手のバトルポケモンに特性があるか。
    pub opponent_has_ability: bool,
    /// 両者のバトルポケモンに付いているエネルギーの合計。
    pub both_active_energy: usize,
    /// 自分のベンチに、自分と同じタイプのたねがいるか。
    pub has_bench_basic_of_own_type: bool,
    /// 自分の手札の枚数。「手札 1 枚につき N ダメージ」のワザに使う。
    pub hand_size: usize,
    /// 「見せた枚数だけ N ダメージ」で見せられる枚数。
    ///
    /// 数え方はカードごとに違う（こいぬまみれは場と手札の同じワザ持ち）ので、
    /// 呼び出し側が数えて渡す。
    pub revealed_count: usize,
}

/// 「... does N ...」の N を取り出す。`marker` の直前の数値。
fn number_before(text: &str, marker: &str) -> Option<u32> {
    text.split_once(marker)
        .and_then(|(head, _)| head.rsplit(' ').next())
        .and_then(|n| n.parse::<u32>().ok())
}

/// 「Flip N coins」の N。「Flip a coin」は 1。
fn coin_count(text: &str) -> Option<u32> {
    if text.contains("Flip a coin") {
        return Some(1);
    }
    text.split_once("Flip ")
        .and_then(|(_, rest)| rest.split(' ').next())
        .and_then(|n| n.parse::<u32>().ok())
}

/// ワザの効果文を読んで、相手のバトルポケモンへの期待ダメージ（弱点は含まない）を返す。
///
/// 解釈は deckgym の `EFFECT_MECHANIC_MAP` に合わせる。「does N damage for each ...」は
/// 基礎ダメージを置き換え、「does N more damage ...」は加算する。コインは期待値で見る。
/// 対戦ログ：スイクン ex「Crystal Waltz」を 20 と見て、実際は 140 だった。
#[must_use]
pub fn expected_damage(effect: Option<&str>, ctx: &AttackContext) -> f64 {
    let base = f64::from(ctx.base_damage);
    let Some(text) = effect else {
        return base;
    };

    // 基礎値を置き換える形
    // 手札の枚数で伸びるワザ（ハンドキネシス）。基礎値を置き換える
    if let Some(per) = number_before(text, " damage for each card in your hand") {
        return f64::from(per) * ctx.hand_size as f64;
    }
    // 見せた枚数で伸びるワザ（こいぬまみれ）。数え方はカードごとに違うので、
    // 呼び出し側が数えた枚数を使う
    if let Some(per) = number_before(text, " damage for each Pok")
        && text.contains("you revealed in this way")
    {
        return f64::from(per) * ctx.revealed_count as f64;
    }
    if let Some(per) = number_before(text, " damage for each Benched Pok") {
        if text.contains("both yours and your opponent's") {
            return f64::from(per) * (ctx.own_bench + ctx.opponent_bench) as f64;
        }
        return f64::from(per) * ctx.own_bench as f64;
    }
    if let (Some(per), Some(coins)) = (
        number_before(text, " damage for each heads"),
        coin_count(text),
    ) {
        if !text.contains("more damage for each heads") {
            return f64::from(per) * f64::from(coins) / 2.0;
        }
        return base + f64::from(per) * f64::from(coins) / 2.0;
    }

    // 加算する形
    let mut total = base;
    if let Some(per) = number_before(text, " more damage for each of your Benched Pok") {
        total += f64::from(per) * ctx.own_bench as f64;
    }
    if let Some(per) = number_before(
        text,
        " more damage for each Energy attached to your opponent's Active Pok",
    ) {
        total += f64::from(per) * ctx.opponent_active_energy as f64;
    }
    if let Some(extra) = number_before(text, " more damage.") {
        let applies = if text.starts_with("Flip a coin. If heads") {
            return total + f64::from(extra) / 2.0;
        } else if text.contains("has a Pok\u{e9}mon Tool attached") {
            ctx.has_tool
        } else if text.contains("extra [") && text.contains("] Energy attached") {
            ctx.extra_energy >= 1
        } else if text.contains("opponent's Active Pok\u{e9}mon is a Pok\u{e9}mon ex") {
            ctx.opponent_is_ex
        } else if text.contains("opponent's Active Pok\u{e9}mon has an Ability") {
            ctx.opponent_has_ability
        } else if let Some(min) = number_before(text, " or more, this attack") {
            ctx.both_active_energy >= min as usize
        } else if text.starts_with("You may discard 1 of your Benched Basic") {
            ctx.has_bench_basic_of_own_type
        } else if text.contains(" for each") {
            // 上で扱った「for each」系
            false
        } else {
            // 条件を読めない加算は付かないものとして扱う（過大評価を避ける）
            false
        };
        if applies {
            total += f64::from(extra);
        }
    }
    total
}

/// ベンチへのダメージの対象。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BenchTarget {
    /// 相手のベンチ 1 匹。
    OneBenched,
    /// 相手のベンチ全員。
    EachBenched,
    /// 相手の場のポケモン 1 匹（バトル場も可）。
    AnyPokemon,
}

/// ワザの、バトル場へのダメージ以外の効果。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct AttackSideEffects {
    /// ベンチ（または任意の 1 匹）へのダメージと対象。
    pub bench_damage: Option<(u32, BenchTarget)>,
    /// 自分に与えるダメージ。
    pub self_damage: u32,
    /// エネルギーをすべて捨てるか。
    pub discards_all_energy: bool,
    /// 捨てるエネルギーの数（すべて捨てる場合は 0 で、`discards_all_energy` を見る）。
    pub discarded_energy: u32,
}

/// ワザの効果文から、ベンチへのダメージ・自傷・エネルギーの消費を読む。
#[must_use]
pub fn attack_side_effects(effect: Option<&str>) -> AttackSideEffects {
    let Some(text) = effect else {
        return AttackSideEffects::default();
    };
    let bench_damage =
        if let Some(n) = number_before(text, " damage to each of your opponent's Benched Pok") {
            Some((n, BenchTarget::EachBenched))
        } else if let Some(n) = number_before(text, " damage to 1 of your opponent's Benched Pok") {
            Some((n, BenchTarget::OneBenched))
        } else {
            number_before(text, " damage to 1 of your opponent's Pok")
                .map(|n| (n, BenchTarget::AnyPokemon))
        };
    let self_damage = number_before(text, " damage to itself").unwrap_or(0);
    let discards_all_energy = text.contains("Discard all");
    let discarded_energy = if discards_all_energy {
        0
    } else if text.starts_with("Discard ") && text.contains(" Energy from this Pok") {
        // 「Discard Fire[R] Energy」は 1 個、「Discard 2 [P] Energy」は 2 個
        text.split(' ')
            .nth(1)
            .and_then(|n| n.parse::<u32>().ok())
            .unwrap_or(1)
    } else {
        0
    };
    AttackSideEffects {
        bench_damage,
        self_damage,
        discards_all_energy,
        discarded_energy,
    }
}

/// ベンチへのダメージの価値。
///
/// 倒せる相手がいればきぜつと同じ価値（最もポイントの大きいもの）。
/// 倒せなければ、最も価値のある削り。
///
/// # 引数
///
/// - `targets`：候補ごとの（残り HP、倒したときのポイント）
#[must_use]
pub fn score_bench_damage(damage: u32, targets: &[(u32, f64)]) -> f64 {
    if damage == 0 {
        return 0.0;
    }
    let knockout = targets
        .iter()
        .filter(|(remaining, _)| *remaining <= damage)
        .map(|(_, points)| KNOCKOUT_VALUE * points)
        .fold(0.0, f64::max);
    if knockout > 0.0 {
        return knockout;
    }
    targets
        .iter()
        .filter(|(remaining, _)| *remaining > 0)
        .map(|(remaining, points)| {
            DAMAGE_PROGRESS_VALUE * f64::from(damage) / f64::from(*remaining) * points
        })
        .fold(0.0, f64::max)
}

/// 盤面からワザの見積もりに使う情報を集める。
fn attack_context(
    state: &State,
    me: usize,
    attack: &Attack,
    attacker: &PlayedCard,
    defender: &PlayedCard,
) -> AttackContext {
    let opponent = (me + 1) % 2;
    let own_type = energy_type_of(&attacker.card);
    let required_of_type = own_type.map_or(0, |t| {
        attack.energy_required.iter().filter(|e| **e == t).count()
    });
    let attached_of_type = own_type.map_or(0, |t| {
        attacker.attached_energy.iter().filter(|e| **e == t).count()
    });
    let has_bench_basic_of_own_type = state
        .enumerate_in_play_pokemon(me)
        .filter(|(index, _)| *index != 0)
        .any(|(_, p)| match &p.card {
            Card::Pokemon(c) => c.stage == 0 && Some(c.energy_type) == own_type,
            Card::Trainer(_) => false,
        });
    AttackContext {
        base_damage: attack.fixed_damage,
        own_bench: state
            .enumerate_in_play_pokemon(me)
            .count()
            .saturating_sub(1),
        opponent_bench: state
            .enumerate_in_play_pokemon(opponent)
            .count()
            .saturating_sub(1),
        opponent_active_energy: defender.attached_energy.len(),
        has_tool: !attacker.attached_tools.is_empty(),
        extra_energy: attached_of_type.saturating_sub(required_of_type),
        opponent_is_ex: defender.card.get_name().ends_with(" ex"),
        opponent_has_ability: matches!(&defender.card, Card::Pokemon(c) if c.ability.is_some()),
        both_active_energy: attacker.attached_energy.len() + defender.attached_energy.len(),
        has_bench_basic_of_own_type,
        hand_size: state.hands[me].len(),
        revealed_count: revealed_for(state, me, attack),
    }
}

/// 「見せた枚数だけダメージ」で見せられる枚数。
///
/// 数え方はカードごとに違う。今は「同じワザを持つポケモンを場と手札から見せる」形
/// （こいぬまみれ）に対応する。読み取れない効果文は 0 を返し、基礎値のままにする。
fn revealed_for(state: &State, me: usize, attack: &Attack) -> usize {
    let Some(effect) = attack.effect.as_deref() else {
        return 0;
    };
    if !effect.contains("you revealed in this way") {
        return 0;
    }
    if !(effect.contains("in play and in your hand") && effect.contains(" attack, and this attack"))
    {
        return 0;
    }
    let has_same_attack = |card: &Card| match card {
        Card::Pokemon(pokemon) => pokemon.attacks.iter().any(|a| a.title == attack.title),
        Card::Trainer(_) => false,
    };
    let on_board = state
        .enumerate_in_play_pokemon(me)
        .filter(|(_, played)| has_same_attack(&played.card))
        .count();
    let in_hand = state.hands[me]
        .iter()
        .filter(|c| has_same_attack(c))
        .count();
    on_board + in_hand
}

/// 相手のバトルポケモンにこのワザで与えるダメージ（弱点・効果・状態異常込み）。
fn damage_to_defender(
    state: &State,
    me: usize,
    attack: &Attack,
    attacker: &PlayedCard,
    defender: &PlayedCard,
) -> u32 {
    let effect = attack_effect_value(attack.effect.as_deref(), 0);
    let ctx = attack_context(state, me, attack, attacker, defender);
    // 期待値は 10 刻みに丸める（コインの期待値など）
    // 期待値は 0 以上なので符号の欠落はない
    #[allow(clippy::cast_sign_loss, clippy::cast_possible_truncation)]
    let raw = expected_damage(attack.effect.as_deref(), &ctx)
        .round()
        .max(0.0) as u32;
    let base = with_weakness(raw, &attacker.card, &defender.card);
    damage_with_status(
        base,
        effect.applies_burn || defender.is_burned(),
        effect.applies_poison || defender.is_poisoned(),
    )
}

/// 今のエネルギーで使えるワザのうち、相手のバトルポケモンに最も大きく入るダメージ。
/// 相手のバトル場へ、この番に与えられる最大ダメージ。効果文まで読む。
///
/// `attainable_damage_of` は基礎値しか見ないため、「手札の枚数ぶん」「見せた枚数ぶん」の
/// ようなワザを大幅に過小評価する（ハンドキネシスを 20 と見ていた）。先読みの評価では
/// こちらを使う。1 個付ければ撃てるワザまで含めるかは `with_one_more_energy` で選ぶ。
pub(crate) fn damage_to_defender_now(state: &State, me: usize, with_one_more_energy: bool) -> u32 {
    let opponent = (me + 1) % 2;
    let (Some(attacker), Some(defender)) = (
        state.in_play_pokemon[me][0].as_ref(),
        state.in_play_pokemon[opponent][0].as_ref(),
    ) else {
        return 0;
    };
    let Card::Pokemon(card) = &attacker.card else {
        return 0;
    };
    let allowance = usize::from(with_one_more_energy);
    card.attacks
        .iter()
        .filter(|attack| {
            energy_missing(&attack.energy_required, &attacker.attached_energy) <= allowance
        })
        .map(|attack| damage_to_defender(state, me, attack, attacker, defender))
        .max()
        .unwrap_or(0)
}

pub(crate) fn best_ready_damage_to_defender(state: &State, me: usize) -> Option<(u32, f64)> {
    let opponent = (me + 1) % 2;
    let attacker = state.in_play_pokemon[me][0].as_ref()?;
    let defender = state.in_play_pokemon[opponent][0].as_ref()?;
    let Card::Pokemon(card) = &attacker.card else {
        return None;
    };
    let damage = card
        .attacks
        .iter()
        .filter(|attack| energy_missing(&attack.energy_required, &attacker.attached_energy) == 0)
        .map(|attack| damage_to_defender(state, me, attack, attacker, defender))
        .max()
        .unwrap_or(0);
    Some((damage, f64::from(defender.get_remaining_hp())))
}

/// 引きずり出す価値があるか。
///
/// 今のバトル場を倒せるなら不要。倒せなくても、相手のほうがポイントが大きく
/// 残り HP の半分以上を削れるなら、非 ex を引きずり出すより主力を殴る（ユーザーの指摘）。
///
/// # 引数
///
/// - `dragged_ko_points`：引きずり出して倒せる相手のうち最大のポイント（倒せなければ 0）
/// - `active_points`：今の相手のバトルポケモンを倒したときのポイント
/// - `active_ko_now`：今の相手をこの番に倒せるか
/// - `active_damage_ratio`：今の相手の残り HP に対する、この番に与えるダメージの割合
#[must_use]
pub fn drag_is_worth(
    dragged_ko_points: f64,
    active_points: f64,
    active_ko_now: bool,
    active_damage_ratio: f64,
) -> bool {
    if dragged_ko_points <= 0.0 || active_ko_now {
        return false;
    }
    !(active_points > dragged_ko_points && active_damage_ratio >= 0.5)
}

/// ワザを使う行動のスコア。
fn score_attack(state: &State, me: usize, attack: &Attack) -> f64 {
    let opponent = (me + 1) % 2;
    let (Some(attacker), Some(defender)) = (
        state.in_play_pokemon[me][0].as_ref(),
        state.in_play_pokemon[opponent][0].as_ref(),
    ) else {
        return 0.0;
    };

    let effect = attack_effect_value(attack.effect.as_deref(), 0);
    let side = attack_side_effects(attack.effect.as_deref());

    let damage = f64::from(damage_to_defender(state, me, attack, attacker, defender));
    let remaining = f64::from(defender.get_remaining_hp());
    let points = knockout_points(&defender.card);

    let mut score = if damage >= remaining && damage > 0.0 {
        // きぜつを取れる。相手が ex やメガシンカ ex ならさらに価値が高い
        KNOCKOUT_VALUE * points
    } else if remaining <= 0.0 {
        effect.status_bonus
    } else {
        DAMAGE_PROGRESS_VALUE * (damage / remaining) * points + effect.status_bonus
    };

    // ベンチ（または任意の 1 匹）へのダメージ。倒せる相手がいればきぜつの価値
    if let Some((bench_damage, target)) = side.bench_damage {
        let targets: Vec<(u32, f64)> = state
            .enumerate_in_play_pokemon(opponent)
            .filter(|(index, _)| target == BenchTarget::AnyPokemon || *index != 0)
            .map(|(_, p)| (p.get_remaining_hp(), knockout_points(&p.card)))
            .collect();
        let hit = with_weakness(bench_damage, &attacker.card, &defender.card);
        score += match target {
            BenchTarget::EachBenched => targets
                .iter()
                .map(|t| score_bench_damage(hit, std::slice::from_ref(t)))
                .sum(),
            _ => score_bench_damage(hit, &targets),
        };
    }

    // 自傷。倒れるなら相手にポイントを渡す
    if side.self_damage > 0 {
        if side.self_damage >= attacker.get_remaining_hp() {
            score -= KNOCKOUT_VALUE * knockout_points(&attacker.card);
        } else {
            score -= f64::from(side.self_damage);
        }
    }

    // エネルギーの消費。次の番に撃てなくなる分だけ引く
    let discarded = if side.discards_all_energy {
        attacker.attached_energy.len() as u32
    } else {
        side.discarded_energy
    };
    score -= ENERGY_DISCARD_VALUE * f64::from(discarded);

    score
}

/// エネルギーの付け先 1 つのスコア。
///
/// ワザのコストを満たす分だけ付け、余剰は次の主力へ回す（`docs/play-principles.md` 3 節）。
///
/// # 引数
///
/// - `is_active`：バトル場か
/// - `attached`：付いているエネルギーの数
/// - `min_cost` / `max_cost`：そのポケモンのワザの最小コストと最大コスト
/// - `is_main`：ベンチで育てる主役（進化系統のたね、または進化済み）か
#[must_use]
pub fn score_attach_target(
    is_active: bool,
    attached: usize,
    min_cost: usize,
    max_cost: usize,
    is_main: bool,
) -> f64 {
    if max_cost == 0 {
        // ワザがないポケモンに付けても意味がない
        return 5.0;
    }
    score_attach_by_missing(
        is_active,
        min_cost.saturating_sub(attached),
        attached >= max_cost,
        AttachRole::of(is_main, is_main),
    )
}

/// [`score_attach_target`] に進化段階を加えたもの。`stage` が 0 の主役（進化前）は
/// 前にいても最優先にしない。
#[must_use]
pub fn score_attach_target_with_stage(
    is_active: bool,
    attached: usize,
    min_cost: usize,
    max_cost: usize,
    is_main: bool,
    is_final_form: bool,
) -> f64 {
    if max_cost == 0 {
        return 5.0;
    }
    score_attach_by_missing(
        is_active,
        min_cost.saturating_sub(attached),
        attached >= max_cost,
        AttachRole::of(is_main, is_final_form),
    )
}

/// 壁が前にいるときの付け先のスコア。
///
/// 壁は受けるためにいるので、エネルギーは裏で完成させる主役に付ける
/// （ユーザーの指摘：ツボツボ始動は裏でビークインを完成させる）。
///
/// # 引数
///
/// - `is_active`：バトル場か
/// - `missing`：最も安いワザまであと何個か
/// - `wall_in_front`：バトル場が壁か
/// - `is_main`：主役の系統か
#[must_use]
pub fn score_attach_target_behind_wall(
    is_active: bool,
    missing: usize,
    wall_in_front: bool,
    is_main: bool,
) -> f64 {
    let base = score_attach_by_missing(is_active, missing, false, AttachRole::of(is_main, is_main));
    if wall_in_front && !is_active && is_main {
        base + WALL_MAIN_ATTACH_BONUS
    } else {
        base
    }
}

/// 壁が前にいるとき、裏の主役への付与に足す加点。前の壁（あと 1 個 = 80）を上回らせる。
const WALL_MAIN_ATTACH_BONUS: f64 = 30.0;

/// エネルギーの付け先の役割。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum AttachRole {
    /// 主役の系統ではない（繋ぎ）。
    Other,
    /// 主役の系統の進化前。
    MainBasic,
    /// 主役の系統の完成形（進化済み、または進化しないたねの主役）。
    MainEvolved,
}

impl AttachRole {
    fn of(is_main: bool, is_evolved: bool) -> Self {
        match (is_main, is_evolved) {
            (false, _) => Self::Other,
            (true, false) => Self::MainBasic,
            (true, true) => Self::MainEvolved,
        }
    }
}

/// エネルギーの付け先のスコア（中核）。
///
/// # 引数
///
/// - `missing`：最も安いワザを使うまでに、あと何個必要か
/// - `surplus`：すべてのワザのコストを満たしていて、これ以上は余剰か
fn score_attach_by_missing(
    is_active: bool,
    missing: usize,
    surplus: bool,
    role: AttachRole,
) -> f64 {
    let is_main = role != AttachRole::Other;
    let is_evolved = role == AttachRole::MainEvolved;
    if is_active {
        // あと 1 個で攻撃できるなら最優先。遠いほど価値が下がり、
        // 3 エネ待ちの繋ぎに付け続けるより裏の主役を動かすほうが良くなる。
        // ただし前に出した主役は、裏のたねより優先して動かす
        match missing {
            0 if surplus => 10.0,
            // 裏の進化済み主役（あと 1 個 70 + 打点と段階の加点 ~32）より上にする。
            // 対戦ログ：E1 のメガバシャーモを前に置いたまま、裏の 2 体目に付けて撃てなくなっていた
            // 前にいる進化済みの主役（E1 のメガ）は裏の 2 体目（70 + 加点 ~32）より優先。
            // 進化前（アチャモ）は 90 にとどめ、裏で進化したばかりの主役（62 + 加点 ~32）に譲る
            1 if is_main && is_evolved => 110.0,
            1 if is_main => 90.0,
            // 繋ぎを 1 ターン早く動かすより、裏の進化済み主役を早く動かすほうが良い
            1 => 80.0,
            2 if is_main => 80.0,
            2 => 60.0,
            _ => 40.0,
        }
    } else if surplus {
        5.0
    } else if is_main {
        // 繋ぎが攻撃できるようになったら、裏の主役を育てる。
        // あと 2 個の進化済み主役（140）を、あと 1 個の進化前（30）より優先できるよう
        // 差を小さくし、打点と進化段階の加点（bench_attach_tiebreak）で決める。
        // 完成形（進化済み、または進化しないたねの主役）はわずかに優先する。
        // メガアブソル ex のようなたねの最終形にエネルギーが集まらず、94 試合中 51% で
        // ワザに必要な 2 個に届いていなかった（ユーザーの依頼で調べたサザンドラ戦）
        let bonus = if is_evolved { 5.0 } else { 0.0 };
        bonus
            + match missing {
                0 | 1 => 70.0,
                2 => 62.0,
                _ => 45.0,
            }
    } else {
        25.0
    }
}

/// エネルギーを付ける行動のスコア。付け先ごとに評価して合算する。
fn score_attach(
    ctx: &PlayerContext,
    state: &State,
    me: usize,
    attachments: &[(u32, EnergyType, usize)],
) -> f64 {
    attachments
        .iter()
        .map(|(amount, energy, in_play_idx)| {
            let Some(target) = state.in_play_pokemon[me][*in_play_idx].as_ref() else {
                return 0.0;
            };
            let attacks = attack_requirements(&target.card);
            let attached = &target.attached_energy;
            let mut score = if attacks.is_empty() || !energy_is_useful(&attacks, attached, *energy)
            {
                // ワザがない、またはこのタイプでは攻撃に近づかない。付けても無駄
                5.0
            } else {
                let missing = attacks
                    .iter()
                    .map(|required| energy_missing(required, attached))
                    .min()
                    .unwrap_or(0);
                let surplus = attacks
                    .iter()
                    .all(|required| energy_missing(required, attached) == 0);
                // 進化済みか、それ以上進化しないたね（メガアブソル ex のような最終形）なら
                // 完成形として扱う。進化前のたねと同じ重みだとエネルギーが分散し、
                // ワザに必要な数に届かない（ユーザーの依頼で調べたサザンドラ戦の敗因）
                let is_final_form = match &target.card {
                    Card::Pokemon(p) => p.stage > 0 || !ctx.is_evolution_basic(&target.card),
                    Card::Trainer(_) => false,
                };
                score_attach_by_missing(
                    *in_play_idx == 0,
                    missing,
                    surplus,
                    AttachRole::of(ctx.is_main_line(&target.card), is_final_form),
                )
            };
            // 壁が前にいるなら、裏の主役を優先して育てる
            let wall_in_front = state.in_play_pokemon[me][0]
                .as_ref()
                .is_some_and(|active| is_wall_played(ctx, active));
            if wall_in_front && *in_play_idx != 0 && ctx.is_main_line(&target.card) {
                score += WALL_MAIN_ATTACH_BONUS;
            }
            if *in_play_idx == 0 {
                let opponent = (me + 1) % 2;
                // 壁の後ろに下がる前提の主役に付けても、逃げるときに捨てることになる
                if wall_available_for_main(ctx, state, me, target)
                    && can_retreat_this_turn(state, me, target)
                {
                    return 5.0 * f64::from(*amount);
                }
                // 次の番に倒されるのに、付けても撃てず逃げる助けにもならないなら見捨てる
                let incoming = incoming_damage_to(state, opponent, target);
                let dies = !survives_next_attack(target.get_remaining_hp(), incoming);
                // 付けて撃てるようになっても、倒せず半分も削れないなら、倒される前の一撃の
                // ために 1 個失うだけ（ユーザーの指摘：30/60 のアチャモに付けてつつく 20）
                let worth_attacking_after = state.in_play_pokemon[opponent][0]
                    .as_ref()
                    .is_some_and(|defender| match &target.card {
                        Card::Pokemon(card) => card.attacks.iter().any(|attack| {
                            energy_missing(&attack.energy_required, attached) == 1
                                && energy_is_useful(&attacks, attached, *energy)
                                && {
                                    let dmg =
                                        damage_to_defender(state, me, attack, target, defender);
                                    dmg >= defender.get_remaining_hp()
                                        || dmg * 2 >= defender.get_remaining_hp()
                                }
                        }),
                        Card::Trainer(_) => false,
                    });
                let can_attack_after = worth_attacking_after;
                let retreat_cost = retreat_cost_of(&target.card);
                let can_retreat_after = retreat_cost > 0
                    && attached.len() + 1 >= retreat_cost
                    && attached.len() < retreat_cost
                    && wants_swap(ctx, state, me);
                if attach_to_active_is_wasted(dies, can_attack_after, can_retreat_after) {
                    return TRAINER_USELESS_VALUE * f64::from(*amount);
                }
                // 裏の主役が育っているのに逃げられないなら、コスト分を付けて逃げる
                score += retreat_energy_bonus(
                    wants_swap(ctx, state, me),
                    target.attached_energy.len(),
                    retreat_cost_of(&target.card),
                );
            } else {
                let (power, evolution_stage) = attack_power_and_stage(&target.card);
                score += bench_attach_tiebreak(power, evolution_stage);
                score -= dying_bench_main_discount(ctx, state, me, *in_play_idx, target);
            }
            score * f64::from(*amount)
        })
        .sum()
}

/// ベンチから前に出すポケモン 1 匹のスコア。
///
/// きぜつ後の昇格や、にげる先の選択に使う。
///
/// # 引数
///
/// - `ready_damage`：今のエネルギーで出せる打点
/// - `has_energy`：エネルギーが付いているか
/// - `points_if_ko`：倒されたときに相手が得るポイント
/// - `opponent_points`：相手の現在のポイント
/// - `survives`：相手の次の攻撃に耐えられるか
#[must_use]
pub fn score_promotion(
    ready_damage: u32,
    has_energy: bool,
    points_if_ko: f64,
    opponent_points: u8,
    survives: bool,
) -> f64 {
    let mut score = f64::from(ready_damage);
    if has_energy {
        score += 50.0;
    }
    if survives {
        score += 100.0;
    }
    // 倒されたら相手が勝つポケモンは、次の攻撃で倒される場合にかぎり前に出さない。
    // 耐えられるなら、渡すポイントが大きいこと自体は減点しない
    if !survives && f64::from(opponent_points) + points_if_ko >= POINTS_TO_WIN {
        score -= LOSING_PROMOTION_PENALTY;
    }
    score
}

/// ベンチの主役を、エネルギーの付け先として後回しにするか。
///
/// 前に出た番に倒される体力なら、育てても相手にポイントを渡すだけになる。
/// ただし健在な主役が他にいるときだけ効かせる。唯一の主役なら育てるしかない
/// （ユーザーの指摘：seed27 ターン 11、10/210 のメガではなく無傷のメガに貼る）。
#[must_use]
pub fn dying_bench_main_is_deprioritised(
    dies_if_promoted: bool,
    healthy_alternative: bool,
) -> bool {
    dies_if_promoted && healthy_alternative
}

/// ベンチの主役が「前に出た番に倒される」なら、健在な主役がいるぶんだけ価値を下げる。
fn dying_bench_main_discount(
    ctx: &PlayerContext,
    state: &State,
    me: usize,
    in_play_idx: usize,
    target: &PlayedCard,
) -> f64 {
    if !ctx.is_main_line(&target.card) {
        return 0.0;
    }
    let opponent = (me + 1) % 2;
    let survives = |pokemon: &PlayedCard| {
        survives_next_attack(
            pokemon.get_remaining_hp(),
            incoming_damage_to(state, opponent, pokemon),
        )
    };
    let healthy_alternative = state.enumerate_in_play_pokemon(me).any(|(index, other)| {
        index != in_play_idx && index != 0 && ctx.is_main_line(&other.card) && survives(other)
    });
    if dying_bench_main_is_deprioritised(!survives(target), healthy_alternative) {
        DYING_BENCH_MAIN_PENALTY
    } else {
        0.0
    }
}

/// ベンチの主役どうしでエネルギーの付け先が同点のときの優先度。
///
/// 打点の高いほう、進化済みのほうを優先する。あと 1 個で動くのが同じでも、
/// シング（0 ダメージ）よりメガハーモニー（130 ダメージ）を先に動かすべき。
///
/// # 引数
///
/// - `max_damage`：そのポケモンのワザの最大基礎ダメージ
/// - `stage`：進化段階（たね 0、1 進化 1、2 進化 2）
#[must_use]
pub fn bench_attach_tiebreak(max_damage: u32, stage: u8) -> f64 {
    f64::from(max_damage) / 10.0 + 10.0 * f64::from(stage)
}

/// ワザの最大基礎ダメージと進化段階。トレーナーズなら (0, 0)。
fn attack_power_and_stage(card: &Card) -> (u32, u8) {
    match card {
        Card::Pokemon(pokemon) => (
            pokemon
                .attacks
                .iter()
                .map(|attack| attack.fixed_damage)
                .max()
                .unwrap_or(0),
            pokemon.stage,
        ),
        Card::Trainer(_) => (0, 0),
    }
}

/// 相手のベンチから引きずり出す対象 1 匹のスコア。
///
/// 今のターンに倒せる相手を最優先し、倒せるなら ex を選ぶ。
/// 倒せないなら、削れている相手を選んで進捗を稼ぐ。
///
/// # 引数
///
/// - `remaining_hp`：対象の残り HP
/// - `points`：倒したときに得るポイント
/// - `my_damage`：自分のバトルポケモンが今出せる打点（弱点込み）
#[must_use]
pub fn score_drag_target(remaining_hp: u32, points: f64, my_damage: u32) -> f64 {
    if my_damage >= remaining_hp {
        return 500.0 * points;
    }
    if remaining_hp == 0 {
        return 0.0;
    }
    20.0 * points + 50.0 * f64::from(my_damage) / f64::from(remaining_hp)
}

/// `Activate`（バトル場への入れ替え）のスコア。
///
/// 自分のポケモンならきぜつ後の昇格、相手のポケモンなら妨害カードによる引きずり出し。
fn score_activate(state: &State, me: usize, player: usize, in_play_idx: usize) -> f64 {
    let Some(target) = state.in_play_pokemon[player][in_play_idx].as_ref() else {
        return 0.0;
    };
    let opponent = (me + 1) % 2;

    if player == me {
        let incoming = incoming_damage_to(state, opponent, target);
        score_promotion_with_hp(Promotion {
            ready_damage: best_ready_damage(target),
            has_energy: !target.attached_energy.is_empty(),
            points_if_ko: knockout_points(&target.card),
            opponent_points: state.points[opponent],
            survives: survives_next_attack(target.get_remaining_hp(), incoming),
            remaining_hp: target.get_remaining_hp(),
            max_hp: max_hp_points(&target.card),
            can_retreat: target.attached_energy.len() >= retreat_cost_of(&target.card),
            ability_needs_active: needs_active_spot(&target.card),
        })
    } else {
        let my_damage = state.in_play_pokemon[me][0].as_ref().map_or(0, |attacker| {
            with_weakness(best_ready_damage(attacker), &attacker.card, &target.card)
        });
        score_drag_target(
            target.get_remaining_hp(),
            knockout_points(&target.card),
            my_damage,
        )
    }
}

/// にげるのスコア。
///
/// 逃げるべきなのは「バトル場がきぜつ寸前で、しかも相手に渡すポイントが大きい」場合と、
/// 「ベンチで育てた主役が完成した」場合。余裕のある盤面からの意味のない撤退は、
/// 付けたエネルギーを遊ばせる 1 ターンの損になる。
fn score_retreat(ctx: &PlayerContext, state: &State, me: usize, to_in_play_idx: usize) -> f64 {
    let Some(active) = state.in_play_pokemon[me][0].as_ref() else {
        return RETREAT_BASE_VALUE;
    };
    let total = max_hp(&active.card);
    if total <= 0.0 {
        return RETREAT_BASE_VALUE;
    }
    let opponent = (me + 1) % 2;
    let destination = state.in_play_pokemon[me][to_in_play_idx].as_ref();

    // この番に倒せるなら、逃げてその機会を捨てない。逃げ先も倒せるなら替えてよい
    if let Some(defender) = state.in_play_pokemon[opponent][0].as_ref() {
        let can_knockout = |pokemon: &PlayedCard| {
            with_weakness(best_ready_damage(pokemon), &pokemon.card, &defender.card)
                >= defender.get_remaining_hp()
        };
        let destination_can_knockout = destination.is_some_and(&can_knockout);
        if retreat_wastes_knockout(can_knockout(active), destination_can_knockout) {
            return RETREAT_BASE_VALUE;
        }
    }

    // ベンチで育てた主役が完成しているなら、削れていなくても入れ替える
    let swap_in_bonus = destination.map_or(0.0, |pokemon| {
        let ready = best_ready_damage(pokemon);
        let current = own_active_attainable(ctx, active);
        if ready > current.saturating_add(STRONGER_DAMAGE_MARGIN) {
            RETREAT_TO_STRONGER_VALUE
        } else {
            0.0
        }
    });

    // 主役が撃てないなら、健在な壁と入れ替えて裏で完成させる。壁は受けるためにいるので、
    // 壁が次の攻撃で倒されるかどうかは見ない（ユーザーの指摘：主役が何もできずに受けるのは誤り）
    if destination
        .is_some_and(|pokemon| is_shelter_played(ctx, knockout_points(&active.card), pokemon))
        && wall_available_for_main(ctx, state, me, active)
    {
        return RETREAT_BASE_VALUE + RETREAT_TO_STRONGER_VALUE;
    }

    let remaining_ratio = f64::from(active.get_remaining_hp()) / total;
    if remaining_ratio > RETREAT_HP_THRESHOLD && swap_in_bonus <= 0.0 {
        // まだ倒される心配が薄く、入れ替える相手も育っていない
        return RETREAT_BASE_VALUE;
    }

    // 削れているほど、そして渡すポイントが大きいほど逃げる価値が高い。
    // 相手の打点で倒される盤面なら危険度を最大にする
    let incoming = incoming_damage_to(state, opponent, active);

    // その場で進化して耐えられるなら、逃げるより進化するほうが良い
    let evolution_in_hand = state.hands[me].iter().find(|card| match card {
        Card::Pokemon(pokemon) => pokemon.evolves_from.as_deref() == Some(&active.card.get_name()),
        Card::Trainer(_) => false,
    });
    if let Some(evolution) = evolution_in_hand {
        let damage_taken = max_hp_points(&active.card).saturating_sub(active.get_remaining_hp());
        if evolving_saves_active(max_hp_points(evolution), damage_taken, incoming) {
            return RETREAT_BASE_VALUE + swap_in_bonus;
        }
    }
    // 逃げ先も次の攻撃で倒されるなら、ポイントを 1 ターン遅らせるだけでエネルギーを失う。
    // 逃げ先がこの番に相手を倒せるなら別
    if let Some(pokemon) = destination {
        let defender = state.in_play_pokemon[opponent][0].as_ref();
        let destination_survives = survives_next_attack(
            pokemon.get_remaining_hp(),
            incoming_damage_to(state, opponent, pokemon),
        );
        let destination_can_knockout = defender.is_some_and(|defender| {
            with_weakness(best_ready_damage(pokemon), &pokemon.card, &defender.card)
                >= defender.get_remaining_hp()
        });
        let destination_useful = retreat_destination_is_useful(
            destination_can_knockout,
            own_active_attainable(ctx, active) > 0,
            knockout_points(&active.card),
            knockout_points(&pokemon.card),
        );
        if retreat_is_pointless(destination_survives, destination_useful) {
            return RETREAT_BASE_VALUE;
        }
    }

    let danger = retreat_danger(active.get_remaining_hp(), incoming);
    let points_at_risk = knockout_points(&active.card);
    let benefit = KNOCKOUT_VALUE * danger * points_at_risk;

    // 逃げ先の評価。攻撃できるか、負け筋にならないか。
    // 残り体力の多さは見ない。試したところ 37 組全体の一致度が悪化した
    // （瀕死の 1 点を守るために元気な ex を前に出し、2 点を渡す場面が増える）
    let destination_score = destination.map_or(0.0, |pokemon| {
        let losing =
            f64::from(state.points[opponent]) + knockout_points(&pokemon.card) >= POINTS_TO_WIN;
        let survives = survives_next_attack(
            pokemon.get_remaining_hp(),
            incoming_damage_to(state, opponent, pokemon),
        );
        // 同じ条件なら、耐えられて殴れる逃げ先を選ぶ（テストで発覚：無傷の E2 メガバシャーモではなく
        // 倒される E2 のクイタランへ逃げていた）。
        // 撃てない 3 点のメガを前に出しに行かない（ユーザーの指摘：メガ系は倒されたら終わり）。
        // この番のエネルギーが残っていれば、1 個付けて撃てるかで見る
        let damage_after_swap = if state.energy_zone[me].current.is_some() {
            attainable_damage_of(pokemon)
        } else {
            best_ready_damage(pokemon)
        };
        // 1 点の繋ぎを守るために撃てないメガを前に出すのは割に合わない（きぜつ 1 点分 = 1000 を
        // 打ち消す大きさ）。3 点のメガを守る逃げ（3000）には負けない
        let exposes_mega = knockout_points(&pokemon.card) >= 3.0 && damage_after_swap == 0;
        let mega_penalty = if exposes_mega { KNOCKOUT_VALUE } else { 0.0 };
        // 同点のときだけ効く程度の加点にとどめる。大きな加点にすると 37 組全体が悪化した
        let tiebreak =
            (if survives { 1.0 } else { 0.0 }) + f64::from(best_ready_damage(pokemon)) / 1000.0;
        score_retreat_destination(pokemon.attached_energy.len(), losing, survives) + tiebreak
            - mega_penalty
    });

    RETREAT_BASE_VALUE + benefit + swap_in_bonus + destination_score
}

/// バトル場へのエネルギー付与が無駄か。
///
/// 次の番に倒されるポケモンに付けても、その 1 個で撃てる・逃げられるようにならなければ、
/// エネルギーごと失う。見捨ててベンチの主役を育てる（Gemini の解析：エネルギーを奪われた
/// ビークイン ex に付け直して、撃てないまま倒された）。
#[must_use]
pub fn attach_to_active_is_wasted(
    dies_next_turn: bool,
    can_attack_after_attach: bool,
    can_retreat_after_attach: bool,
) -> bool {
    dies_next_turn && !can_attack_after_attach && !can_retreat_after_attach
}

/// ベンチを犠牲にして追加ダメージを出す価値があるか（ビークイン ex「Chase Order」など）。
///
/// 犠牲なしで倒せるなら不要、犠牲にしても倒せないなら無駄（Gemini の解析：最後の
/// ツボツボ ex を捨てて 140 を出したが、180 のキュウコン ex を倒せなかった）。
#[must_use]
pub fn sacrifice_for_damage_is_worth(
    base_damage: u32,
    boosted_damage: u32,
    remaining_hp: u32,
) -> bool {
    base_damage < remaining_hp && boosted_damage >= remaining_hp
}

/// 逃げる危険度。相手の次の攻撃で倒されるなら 1、耐えられるなら 0。
///
/// 以前は耐えられても残り HP の割合で段階的に危険度を付けていたが、
/// 90/210 のメガバシャーモがぶつかる 30 を前に逃げてエネルギーを失っていた。
/// 高 HP・高火力の主役は倒される前に倒し切る（動画の立ち回り）。
#[must_use]
pub fn retreat_danger(remaining_hp: u32, incoming_damage: u32) -> f64 {
    if survives_next_attack(remaining_hp, incoming_damage) {
        0.0
    } else {
        1.0
    }
}

/// 逃げても意味がないか。
///
/// 逃げ先が相手の次の攻撃に耐えられないなら、ポイントを渡すのを 1 ターン遅らせるだけで
/// にげるに使ったエネルギーと逃げ先のエネルギーを失う（ユーザーの指摘：10/70 のポワルンから
/// 50/80 のクイタランへ逃げ、次の 60 で倒された）。逃げ先がこの番に相手を倒せるなら別。
#[must_use]
pub fn retreat_is_pointless(destination_survives: bool, destination_useful: bool) -> bool {
    !destination_survives && !destination_useful
}

/// 逃げ先が倒されるとしても、逃げる価値があるか。
///
/// この番に相手を倒せる、または渡すポイントが減る（3 点のメガアブソル ex を 1 点の
/// サザンドラに替える）なら価値がある（ユーザーの指摘：20/170 のメガアブソル ex を
/// 前に残し、撃てるサザンドラをベンチに置いたまま番を終えていた）。
/// 「先に殴れる」だけでは足りない（倒せない一撃のために繋ぎとエネルギーを失う）。
#[must_use]
pub fn retreat_destination_is_useful(
    can_knockout: bool,
    active_can_attack: bool,
    active_points: f64,
    destination_points: f64,
) -> bool {
    if can_knockout {
        return true;
    }
    let saved = active_points - destination_points;
    if saved <= 0.0 {
        // 渡すポイントが減らないなら、逃げてエネルギーを失うだけ
        return false;
    }
    // 主役がこの番に殴れるなら、殴ってから倒されるほうが得。ただし 3 点のメガ ex を
    // 1 点の繋ぎに替えられる場面は別で、2 点の差は殴り 1 回より大きい
    // （ユーザーの指摘：バシャーモ対バタフリー seed27 ターン 11）
    !active_can_attack || saved >= POINTS_WORTH_A_RETREAT
}

/// 逃げるとこの番のきぜつを捨てることになるか。
///
/// バトル場がこの番に相手のバトル場を倒せるなら、殴ってから倒されるほうが得
/// （`docs/play-principles.md`）。逃げ先もこの番に倒せるなら、替えてから殴ってよい。
///
/// シード 16 ターン 9 で、残り 80 の相手を 140 で倒せば 3 点で勝ちだったのに、
/// 逃げて撃たずに終えていた。「3 点を取る」と「3 点を渡さない」が同点になり、
/// 逃げ先の加点で逃げが勝っていた。
#[must_use]
pub fn retreat_wastes_knockout(active_can_knockout: bool, destination_can_knockout: bool) -> bool {
    active_can_knockout && !destination_can_knockout
}

/// にげる先 1 匹のスコア。
///
/// きぜつ後の昇格（[`score_promotion`]）と同じく、倒されたら相手が勝つポケモンでも
/// 相手の次の攻撃に耐えられるなら減点しない。対戦ログで、相手 1 点の場面で
/// 耐えられるメガバシャーモ（E1）ではなく E0 のアチャモへ逃げて主役を温存していた。
///
/// # 引数
///
/// - `energy`：逃げ先に付いているエネルギーの数
/// - `losing_if_ko`：倒されると相手のポイントが勝利に達するか
/// - `survives`：相手の次の攻撃に耐えられるか
#[must_use]
pub fn score_retreat_destination(energy: usize, losing_if_ko: bool, survives: bool) -> f64 {
    let base = RETREAT_DESTINATION_ENERGY_VALUE * energy as f64;
    if losing_if_ko && !survives {
        base - LOSING_PROMOTION_PENALTY
    } else {
        base
    }
}

/// トレーナーズ 1 枚のスコア。
///
/// 効果を無視して一律に評価すると、逃げる予定もないのに X Speed を使うなど、
/// 手札を無駄に減らす。カードごとに、盤面と噛み合うときだけ高く評価する。
///
/// # 引数
///
/// - `name`：カード名
/// - `hand_size`：自分の手札枚数
/// - `active_hp` / `active_max_hp`：自分のバトルポケモンの残り HP と最大 HP
/// - `bench_full`：ベンチが埋まっているか
#[must_use]
pub fn score_trainer(
    name: &str,
    hand_size: usize,
    active_hp: u32,
    active_max_hp: u32,
    bench_full: bool,
) -> f64 {
    let hand = hand_size as f64;
    let hurt = if active_max_hp == 0 {
        0.0
    } else {
        1.0 - f64::from(active_hp) / f64::from(active_max_hp)
    };

    match name {
        // ドローソース。手札が少ないほど価値が高く、上限近くでは引けず無駄になる
        "Professor's Research" | "Copycat" | "Mars" | "Sightseer" => {
            let room = (HAND_LIMIT - hand).max(0.0);
            if room <= 1.0 {
                TRAINER_USELESS_VALUE
            } else {
                20.0 + room * 12.0
            }
        }

        // サーチ。ベンチが埋まっていれば出す先がない。
        // 「ドローより常に先」（山札の圧縮）は 37 組の一致度を悪化させたので入れていない
        "Poké Ball" | "Quick-Grow Extract" | "Pokémon Communication" => {
            if bench_full {
                5.0
            } else {
                90.0
            }
        }

        // 相手の盤面をいじる、またはエネルギーを回収する。攻撃と噛み合うので高い
        "Cyrus" | "Repel" | "Team Rocket's Boss" | "Flame Patch" => 100.0,

        // 相手に選ばせる入れ替え。狙撃には使えないので、相手の投資を崩す価値だけ見る
        "Sabrina" => 0.0,

        // 進化の補助。盤面が強くなる。サーチより先に使う
        "Rare Candy" => 150.0,

        // 回復。ダメージを受けていなければ無駄
        "Potion" | "Lucky Ice Pop" | "Erika" | "Pokémon Center Lady" => {
            if hurt < 0.15 {
                TRAINER_USELESS_VALUE
            } else {
                40.0 + hurt * 120.0
            }
        }

        // にげるコストの補助。逃げたい場面でなければ手札の無駄
        "X Speed" => {
            if hurt < 0.5 {
                TRAINER_USELESS_VALUE
            } else {
                20.0 + hurt * 60.0
            }
        }

        // 相手のどうぐを剥がす。付いていない相手には効かないが判別できないので控えめ
        "Field Blower" => 20.0,

        // 攻撃の上乗せ。そのターンに攻撃する前提なので高い
        "Korrina" => 90.0,

        _ => TRAINER_UNKNOWN_VALUE,
    }
}

/// ナツメのスコア。相手のバトル場に付いたエネルギーが多いほど価値がある。
///
/// 相手が選ぶので狙撃には使えず、投資のない相手に使っても手札とサポートの枠を
/// 無駄にするだけ（対戦ログでエネルギー 0 のメガルカリオに使っていた）。
#[must_use]
pub fn score_sabrina(opponent_active_energy: usize) -> f64 {
    SABRINA_PER_ENERGY_VALUE * opponent_active_energy as f64
}

/// ドローソースか。
fn is_draw_card(name: &str) -> bool {
    matches!(
        name,
        "Professor's Research" | "Copycat" | "Mars" | "Sightseer"
    )
}

/// 手札を増やす手（ドロー・サーチ）か。先読みでは、これを他の手より先に打つ。
pub(crate) fn is_draw_or_search_card(name: &str) -> bool {
    is_draw_card(name) || matches!(name, "Poké Ball" | "Korrina")
}

/// 相手のベンチを引きずり出す妨害カードか。
fn is_drag_card(name: &str) -> bool {
    matches!(name, "Cyrus" | "Repel")
}

/// 場の進化系統のポケモンを、次の番に進化させるパーツが手札にない状態か。
///
/// この状態ではドローで進化パーツを探す価値が上がる。
/// 2 進化系は、最終進化が手札にあってもレアキャンディがなければ不足とみなす。
fn needs_evolution_parts(ctx: &PlayerContext, state: &State, me: usize) -> bool {
    let has_candy = state.hands[me]
        .iter()
        .any(|card| card.get_name() == "Rare Candy");
    state.enumerate_in_play_pokemon(me).any(|(_, pokemon)| {
        let Card::Pokemon(card) = &pokemon.card else {
            return false;
        };
        let base = base_basic_name(&card.name);
        let top_stage = ctx.line_top_stage.get(&base).copied().unwrap_or(0);
        if card.stage >= top_stage {
            return false;
        }
        let has_next = has_direct_evolution_in_hand(state, me, &card.name);
        let has_top = state.hands[me].iter().any(|held| match held {
            Card::Pokemon(p) => p.stage == top_stage && base_basic_name(&p.name) == base,
            Card::Trainer(_) => false,
        });
        evolution_parts_missing(top_stage, has_next, has_top, has_candy)
    })
}

/// 相手のベンチに、引きずり出せば今倒せるポケモンがいるか。
fn dragged_knockout_points(state: &State, me: usize) -> f64 {
    let opponent = (me + 1) % 2;
    let Some(attacker) = state.in_play_pokemon[me][0].as_ref() else {
        return 0.0;
    };
    state
        .enumerate_in_play_pokemon(opponent)
        .filter(|(index, _)| *index != 0)
        .filter(|(_, target)| {
            with_weakness(best_ready_damage(attacker), &attacker.card, &target.card)
                >= target.get_remaining_hp()
        })
        .map(|(_, target)| knockout_points(&target.card))
        .fold(0.0, f64::max)
}

/// トレーナーズを使う行動のスコア。盤面の文脈による加点を含む。
/// 相手の逃げエネを増やす札を、この番に使う価値があるか。
///
/// 効果は相手の次の番の終わりまでしか続かない。逃げエネで打点が上がるワザを
/// この番に撃てるなら大きな得になるが、撃てないなら乗らないまま切れる
/// （ユーザーの指摘：「アリアドスの特性 + ロケット団のベトベトバズーカで逃げエネを
/// 増やして一気に殴る」がセオリー）。
///
/// # 引数
///
/// - `scales_with_retreat`：バトル場に「逃げエネで打点が上がるワザ」があるか
/// - `can_attack_now`：そのワザをこの番に撃てるか（この番に付けられる 1 個を含む）
///
/// 相手を前に縛る使い方（`+1` で逃げられなくする）は**入れていない**。
/// 実戦では使う手だが（ユーザーの指摘）、縛る価値まで判定しない実装では
/// メタ全体の勝率が 32.4% → 31.5% と下がった。メガバシャーモ ex には
/// +3.5% だったが、ビークイン ex に -4.6%、マタドガス ex に -3.6% と負け越す。
/// 「どの相手を縛る価値があるか」を決められるまで入れない
/// （`docs/play-principles.md`）。
#[must_use]
pub fn retreat_cost_boost_is_worth(scales_with_retreat: bool, can_attack_now: bool) -> bool {
    scales_with_retreat && can_attack_now
}

/// その効果文が「相手のバトル場の逃げエネを増やす」ものか。
fn raises_opponent_retreat_cost(effect: &str) -> bool {
    effect.contains("opponent's Active Pokémon's Retreat Cost is") && effect.contains("more")
}

/// そのワザの打点が相手の逃げエネで上がるか。
fn attack_scales_with_retreat_cost(effect: Option<&str>) -> bool {
    effect.is_some_and(|text| {
        text.contains("for each Energy in your opponent's Active Pokémon's Retreat Cost")
    })
}

/// バトル場が「逃げエネで打点が上がるワザ」をこの番に撃てるか。
///
/// この番に付けられるエネルギー 1 個は数に入れる。付け先を決める前に
/// トレーナーズを使う順番になることがあるため。
fn active_scales_with_retreat_now(ctx: &PlayerContext, state: &State, me: usize) -> (bool, bool) {
    let Some(active) = state.in_play_pokemon[me][0].as_ref() else {
        return (false, false);
    };
    let Card::Pokemon(card) = &active.card else {
        return (false, false);
    };
    let scaling: Vec<&Attack> = card
        .attacks
        .iter()
        .filter(|attack| attack_scales_with_retreat_cost(attack.effect.as_deref()))
        .collect();
    if scaling.is_empty() {
        return (false, false);
    }
    let spare =
        usize::from(!ctx.active_energy_locked.get() && state.energy_zone[me].current.is_some());
    let ready = scaling
        .iter()
        .any(|attack| energy_missing(&attack.energy_required, &active.attached_energy) <= spare);
    (true, ready)
}

fn score_play(ctx: &PlayerContext, state: &State, me: usize, card: &TrainerCard) -> f64 {
    let name = card.name.as_str();
    let opponent = (me + 1) % 2;

    // スタジアムは効果を先に使ってから他を進める（ユーザーの指摘：あまくかおる森を博士の研究の後に出していた）
    if card.trainer_card_type == TrainerType::Stadium {
        let already_mine = state.active_stadium_owner == Some(me)
            && state
                .active_stadium
                .as_ref()
                .is_some_and(|s| s.get_name() == name);
        return score_stadium_play(Some(card.effect.as_str()), already_mine);
    }

    // ふしぎなアメは、バトル場の進化前を先送りすべき場面では使わない
    if name == "Rare Candy"
        && std::env::var_os("POCKET_NO_EVOLVE_DEFER").is_none()
        && rare_candy_is_premature(ctx, state, me)
    {
        return TRAINER_USELESS_VALUE;
    }

    // タイプ条件のあるトレーナーズは、そのタイプのポケモンが場にいなければ無駄
    // （ユーザーの指摘：超デッキでコルニ＝闘限定のバフを 40 試合で 30 回打っていた）
    if let Some(required) = required_type_of(&card.effect) {
        let has_type =
            state
                .enumerate_in_play_pokemon(me)
                .any(|(_, pokemon)| match &pokemon.card {
                    Card::Pokemon(p) => format!("{:?}", p.energy_type) == required,
                    Card::Trainer(_) => false,
                });
        if !has_type {
            return TRAINER_USELESS_VALUE;
        }
    }

    // フィールドブロアーは相手のどうぐか相手のスタジアムがあるときだけ
    if name == "Field Blower" {
        let opponent_tool = state
            .enumerate_in_play_pokemon(opponent)
            .any(|(_, p)| !p.attached_tools.is_empty());
        let opponent_stadium =
            state.active_stadium.is_some() && state.active_stadium_owner == Some(opponent);
        return score_field_blower(opponent_tool, opponent_stadium);
    }

    // 相手の逃げエネを増やす札は、その打点が乗る番にだけ使う
    if raises_opponent_retreat_cost(&card.effect) {
        let (scales, can_attack_now) = active_scales_with_retreat_now(ctx, state, me);
        if !retreat_cost_boost_is_worth(scales, can_attack_now) {
            return TRAINER_USELESS_VALUE;
        }
    }

    let active = state.in_play_pokemon[me][0].as_ref();
    let bench_full = state.enumerate_in_play_pokemon(me).count() > BENCH_SLOTS;
    let mut score = score_trainer(
        name,
        state.hands[me].len(),
        active.map_or(0, PlayedCard::get_remaining_hp),
        active.map_or(0, |pokemon| max_hp_points(&pokemon.card)),
        bench_full,
    );

    // 進化パーツを探すためのドローは価値が上がる
    if is_draw_card(name) && score > 0.0 && needs_evolution_parts(ctx, state, me) {
        score += DRAW_FOR_EVOLUTION_BONUS;
    }

    // 引きずり出して倒せるなら、妨害は攻撃と同じ価値を持つ。
    // 今のバトル場を倒せる、または主力を大きく削れるなら使わない
    if is_drag_card(name) {
        let dragged = dragged_knockout_points(state, me);
        let opponent = (me + 1) % 2;
        let active_points = state.in_play_pokemon[opponent][0]
            .as_ref()
            .map_or(0.0, |pokemon| knockout_points(&pokemon.card));
        let (damage, remaining) = best_ready_damage_to_defender(state, me).unwrap_or((0, 0.0));
        let ko_now = remaining > 0.0 && f64::from(damage) >= remaining;
        let ratio = if remaining > 0.0 {
            f64::from(damage) / remaining
        } else {
            0.0
        };
        score = if drag_is_worth(dragged, active_points, ko_now, ratio) {
            score + DISRUPT_WITH_KNOCKOUT_VALUE
        } else {
            TRAINER_USELESS_VALUE
        };
    }

    // ナツメは相手のバトル場に付いたエネルギーを無駄にさせる分だけ価値がある
    if name == "Sabrina" {
        let opponent = (me + 1) % 2;
        let invested = state.in_play_pokemon[opponent][0]
            .as_ref()
            .map_or(0, |pokemon| pokemon.attached_energy.len());
        score = score_sabrina(invested);
    }

    // 入れ替えたい場面では、にげる補助が本来の価値を持つ
    if name == "X Speed" && wants_swap(ctx, state, me) {
        score = score.max(RETREAT_HELPER_VALUE);
    }

    score
}

/// ポケモンを場に出す行動のスコア。
fn score_place(
    ctx: &PlayerContext,
    state: &State,
    me: usize,
    card: &Card,
    in_play_idx: usize,
) -> f64 {
    let opponent = (me + 1) % 2;
    let exposure = exposure_penalty(
        knockout_points(card),
        weakness_exposed(state, opponent, card),
    );
    if in_play_idx == 0 {
        // バトル場が空いているなら埋めるのが最優先。ただし主役の卵は裏で育てるので、
        // 繋ぎになれるポケモンがいればそちらを前に出す
        let mut score = KNOCKOUT_VALUE + max_hp(card) / 10.0;
        let (zero_cost, lock) = zero_cost_attack_profile(card);
        score += setup_zero_cost_bonus(zero_cost, lock);
        if PlayerContext::is_first_turn_evolver(card) && ctx.is_evolution_basic(card) {
            // 進化の加速はバトル場でしか働かない。前に出してすぐ進化させる
            score += SETUP_FIRST_TURN_EVOLVER_BONUS;
        } else if is_wall_card(ctx, card) && knockout_points(card) < 3.0 {
            // 壁で受けつつ削り、裏で主役を完成させる（ユーザーの指摘：ツボツボ ex 始動）。
            // 3 点のメガ ex は壁にならない（下の減点）
            score += SETUP_WALL_BONUS;
            if has_damage_reduction(card) {
                score += SETUP_WALL_REDUCTION_BONUS;
            }
        } else {
            match ctx.line_top_stage_of(card) {
                // 2 進化系は完成まで時間がかかる。裏で育てる
                2 => score -= SETUP_EVOLUTION_BASIC_PENALTY,
                // 1 進化系は前に出して次の番に進化させる。ただし壁がいるなら裏で進化させる
                1 if !hand_has_wall(ctx, state, me) => score += SETUP_ONE_STAGE_BASIC_BONUS,
                _ => {}
            }
        }
        // 倒されたら 3 点のメガ ex を初手から前に出すと、序盤に殴られて負け筋になる
        // （ユーザーの指摘：オトシドリがあるのにメガアブソル ex を前に出していた）
        score -= setup_points_penalty(knockout_points(card));
        // ベンチでも働く特性が売りのポケモンは、前に出さず裏に置く
        score -= setup_bench_ability_penalty(has_ability(card), needs_active_spot(card));
        return score - exposure;
    }

    let mut score = PLACE_VALUE + max_hp(card) / 20.0;
    if ctx.is_evolution_basic(card) {
        score += PLACE_EVOLUTION_BASIC_BONUS;
    }
    score - exposure
}

/// 同じ進化先が複数あるときの優先度。バトル場を優先し、エネルギーが多いほうを優先する。
///
/// 対戦ログ：前のリオルではなく、傷ついたベンチのリオルをメガルカリオに進化させ、
/// メガが裏で E0 のまま遊んでいた。
#[must_use]
pub fn evolve_target_bonus(in_play_idx: usize, remaining_hp: u32, energy: usize) -> f64 {
    let position = if in_play_idx == 0 { 15.0 } else { 0.0 };
    position + 5.0 * energy as f64 + f64::from(remaining_hp) / 20.0
}

/// バトル場のポケモンを進化させれば、相手の次の攻撃に耐えられるか。
///
/// 進化してもダメージは引き継ぐ。耐えられるなら、逃げるより進化するほうが良い。
#[must_use]
pub fn evolving_saves_active(new_max_hp: u32, damage_taken: u32, incoming: u32) -> bool {
    survives_next_attack(new_max_hp.saturating_sub(damage_taken), incoming)
}

/// 場の進化系統のポケモンを次の番に進化させるパーツが、手札にないか。
///
/// 2 進化系は、次の段が手札にあるか、最終進化とレアキャンディが揃っていれば足りている。
///
/// # 引数
///
/// - `top_stage`：系統の最終段数
/// - `has_next_stage_in_hand`：次の段のカードが手札にあるか
/// - `has_top_in_hand`：最終進化のカードが手札にあるか
/// - `has_candy`：レアキャンディが手札にあるか
#[must_use]
pub fn evolution_parts_missing(
    top_stage: u8,
    has_next_stage_in_hand: bool,
    has_top_in_hand: bool,
    has_candy: bool,
) -> bool {
    if top_stage == 0 || has_next_stage_in_hand {
        return false;
    }
    !(top_stage >= 2 && has_top_in_hand && has_candy)
}

/// 手札に、このポケモンから直接進化するカードがあるか。
fn has_direct_evolution_in_hand(state: &State, me: usize, name: &str) -> bool {
    state.hands[me].iter().any(|card| match card {
        Card::Pokemon(pokemon) => pokemon.evolves_from.as_deref() == Some(name),
        Card::Trainer(_) => false,
    })
}

/// バトル場でしか働かない、自分の進化を早める特性の効果文か。
///
/// 該当するたねは、2 進化系でも裏で育てず前に出す。
///
/// - イーブイ「進化の加速」：バトル場にいれば初ターンでも進化できる
/// - キャタピー「Quick Growth」：バトル場にいれば相手の番の終わりに山札から自動で進化する
#[must_use]
pub fn is_active_self_evolver_effect(effect: Option<&str>) -> bool {
    let Some(text) = effect else {
        return false;
    };
    let in_active = text.contains("in the Active Spot");
    let evolves_self = text.contains("can evolve during your first turn")
        || text.contains("evolves from this Pok");
    in_active && evolves_self
}

/// 初期配置で、0 コストのワザを持つたねをバトル場に出すときの加点。
///
/// 0 コストのワザは最初の番から使える。相手を眠らせる・まひさせる 0 コストのワザ
/// （ププリンの「すやすやソング」など）は、時間を稼ぐ繋ぎとして最も価値が高い。
///
/// # 引数
///
/// - `has_zero_cost_attack`：0 コストのワザを持つか
/// - `inflicts_lock`：そのワザが相手をねむり・まひにするか
#[must_use]
pub fn setup_zero_cost_bonus(has_zero_cost_attack: bool, inflicts_lock: bool) -> f64 {
    if !has_zero_cost_attack {
        return 0.0;
    }
    if inflicts_lock {
        SETUP_ZERO_COST_BONUS + SETUP_ZERO_COST_LOCK_BONUS
    } else {
        SETUP_ZERO_COST_BONUS
    }
}

/// カードの 0 コストのワザについて、（持つか, ねむり・まひを与えるか）を返す。
fn zero_cost_attack_profile(card: &Card) -> (bool, bool) {
    let Card::Pokemon(pokemon) = card else {
        return (false, false);
    };
    let zero_cost: Vec<&Attack> = pokemon
        .attacks
        .iter()
        .filter(|attack| attack.energy_required.is_empty())
        .collect();
    let inflicts_lock = zero_cost.iter().any(|attack| {
        attack.effect.as_deref().is_some_and(|text| {
            text.contains("opponent's Active Pok")
                && (text.contains("now Asleep") || text.contains("now Paralyzed"))
        })
    });
    (!zero_cost.is_empty(), inflicts_lock)
}

/// きぜつ後に前に出す候補の情報。
#[derive(Debug, Clone, Copy)]
// 前に出す判断に要る条件をまとめた引数用の構造体。位置引数を増やさないために持つ
#[allow(clippy::struct_excessive_bools)]
pub struct Promotion {
    /// 今すぐ撃てる最大ダメージ。
    pub ready_damage: u32,
    /// エネルギーが付いているか。
    pub has_energy: bool,
    /// 倒されたときに相手が得るポイント。
    pub points_if_ko: f64,
    /// 相手の現在のポイント。
    pub opponent_points: u8,
    /// 相手の次の攻撃に耐えられるか。
    pub survives: bool,
    /// 残り HP。
    pub remaining_hp: u32,
    /// 最大 HP。
    pub max_hp: u32,
    /// にげるコストを払えるか。
    pub can_retreat: bool,
    /// 特性が「バトル場にいること」を条件にしているか。
    ///
    /// 特性の働く場所で、前に出すかベンチに置くかが決まる（ユーザーの指摘）。
    /// イーブイの Boosted Evolution はバトル場でしか働かないので前に出す価値があり、
    /// ダークライの Bad Dreams は場にいれば働くので前に出す理由がない。
    pub ability_needs_active: bool,
}

/// その特性は「バトル場にいること」を条件にしているか。
///
/// 効果文で判断するので、カード名を並べる必要がない。
/// 「相手のバトルポケモン」を見ているだけの特性（ダークライの Bad Dreams）を
/// 取り違えないよう、`your`/`this Pokémon` 側の条件だけを見る。
#[must_use]
pub fn ability_needs_active_spot(effect: &str) -> bool {
    let lowered = effect.to_lowercase();
    [
        "this pokémon is in the active spot",
        "this pokemon is in the active spot",
    ]
    .iter()
    .any(|needle| lowered.contains(needle))
}

/// バトル場でしか働かない特性を持つポケモンを前に出す加点。
const ACTIVE_ABILITY_VALUE: f64 = 90.0;

/// 場所を問わない特性を持つポケモンを、初期配置で前に出すときの減点。
///
/// ユーザーの指摘：ダークライ（HP 100）は壁の条件を満たすので前に出していたが、
/// このデッキでの役割は特性 Bad Dreams で、**ベンチにいれば働く**。
/// 前に出すとイーブイの進化加速を捨てたうえ、ダークライ自身も何も得しない。
const SETUP_BENCH_ABILITY_PENALTY: f64 = 60.0;

/// 「特性を活かす」役割とみなすワザのコスト。これ以上なら、前に出しても当分撃てない。
const BENCH_ROLE_COST: usize = 3;

/// そのポケモンは「特性を活かす」役割か（＝ベンチに置くべきか）。
///
/// ユーザーの指摘：「ダークライという特性を活かすポケモンの扱い方を変えるべき」。
/// ダークライは特性 Bad Dreams（場所を問わず働く）＋ワザが無無無（3 エネ）で、
/// 前に出しても特性は変わらず働かず、撃つまでに 3 番かかる。
///
/// ププリン（0 エネで撃てる）やイーブイ（特性がバトル場条件）は該当しない。
///
/// # 引数
///
/// - `has_ability`：特性を持つか
/// - `ability_needs_active`：その特性が「バトル場にいること」を条件にしているか
/// - `cheapest_attack_cost`：最も安いワザのコスト（エネルギーの個数）
#[must_use]
pub fn is_bench_role(
    has_ability: bool,
    ability_needs_active: bool,
    cheapest_attack_cost: usize,
) -> bool {
    has_ability && !ability_needs_active && cheapest_attack_cost >= BENCH_ROLE_COST
}

/// 効果文が要求する自分のポケモンのタイプ。条件がなければ `None`。
///
/// ユーザーの指摘：探索がメガチルタリス（超）のデッキにコルニ（闘ポケモン限定の
/// ダメージバフ）を入れ、方策が 40 試合で 30 回も打っていた。
/// 該当するカードは 20 件（エリカ、カスミ、コルニなど 6 タイプ）。
///
/// `your [X] Pokémon` の形だけを見る。相手を指す記述（`your opponent's`）は対象外。
#[must_use]
pub fn required_type_of(effect: &str) -> Option<&'static str> {
    let lowered = effect.to_lowercase();
    let mut from = 0;
    while let Some(offset) = lowered[from..].find("your ") {
        let start = from + offset + "your ".len();
        from = start;
        // 「your opponent's [X]」は相手のポケモンなので条件ではない
        if lowered[start..].starts_with("opponent") {
            continue;
        }
        let rest = &lowered[start..];
        if !rest.starts_with('[') || rest.len() < 3 {
            continue;
        }
        let letter = rest.as_bytes()[1] as char;
        if rest.as_bytes()[2] != b']' {
            continue;
        }
        return match letter {
            'g' => Some("Grass"),
            'r' => Some("Fire"),
            'w' => Some("Water"),
            'l' => Some("Lightning"),
            'p' => Some("Psychic"),
            'f' => Some("Fighting"),
            'd' => Some("Darkness"),
            'm' => Some("Metal"),
            'n' => Some("Dragon"),
            'c' => Some("Colorless"),
            _ => None,
        };
    }
    None
}

/// いま撃てるかまで見た「特性を活かす」役割の判定。
///
/// エネルギーが乗って撃てるなら、それは特性要員ではなくアタッカーなので前に出してよい
/// （ユーザーの懸念：「すでにベンチで育っているアタッカーより逃げエネの少ない
/// ポケモンが優先されないか」）。
///
/// # 引数
///
/// - `attached_energy`：付いているエネルギーの個数
#[must_use]
pub fn is_bench_role_now(
    has_ability: bool,
    ability_needs_active: bool,
    cheapest_attack_cost: usize,
    attached_energy: usize,
) -> bool {
    is_bench_role(has_ability, ability_needs_active, cheapest_attack_cost)
        && attached_energy < cheapest_attack_cost
}

/// 初期配置で、場所を問わない特性を持つポケモンを前に出すときの減点。
///
/// # 引数
///
/// - `has_ability`：特性を持つか
/// - `needs_active`：その特性が「バトル場にいること」を条件にしているか
#[must_use]
pub fn setup_bench_ability_penalty(has_ability: bool, needs_active: bool) -> f64 {
    if has_ability && !needs_active {
        SETUP_BENCH_ABILITY_PENALTY
    } else {
        0.0
    }
}

/// そのカードが特性を持つか。
fn has_ability(card: &Card) -> bool {
    matches!(card, Card::Pokemon(pokemon) if pokemon.ability.is_some())
}

/// そのカードの特性が「バトル場にいること」を条件にしているか。
fn needs_active_spot(card: &Card) -> bool {
    match card {
        Card::Pokemon(pokemon) => pokemon
            .ability
            .as_ref()
            .is_some_and(|ability| ability_needs_active_spot(&ability.effect)),
        Card::Trainer(_) => false,
    }
}

/// 撃てず逃げられないポケモンを前に出すときの減点。耐える加点（100）を打ち消す大きさ。
const STUCK_PROMOTION_PENALTY: f64 = 120.0;

/// 前に出すポケモンのスコアに、残り HP の割合を加味する。
///
/// 動画：相手が眠りから覚めたときの返しに備え、フルヘルスのポケモンを前に出す。
#[must_use]
pub fn score_promotion_with_hp(promotion: Promotion) -> f64 {
    let Promotion {
        ready_damage,
        has_energy,
        points_if_ko,
        opponent_points,
        survives,
        remaining_hp,
        max_hp,
        can_retreat,
        ability_needs_active,
    } = promotion;
    let base = score_promotion(
        ready_damage,
        has_energy,
        points_if_ko,
        opponent_points,
        survives,
    );
    // 撃てず、にげるコストも払えないポケモンを前に出すと、次の番も動けない。
    // HP が高いだけの置物より、0 エネで撃てる駒を選ぶ（ユーザーの依頼で調べた
    // メガチルタリス戦：ダークライを E0 で前に出し、撃てない番の 69% を占めていた）
    let base = if ready_damage == 0 && !can_retreat {
        base - STUCK_PROMOTION_PENALTY
    } else {
        base
    };
    // バトル場にいることが条件の特性は、前に出さなければ働かない
    let base = if ability_needs_active {
        base + ACTIVE_ABILITY_VALUE
    } else {
        base
    };
    if max_hp == 0 {
        return base;
    }
    base + PROMOTION_HEALTH_VALUE * f64::from(remaining_hp) / f64::from(max_hp)
}

/// 相手に弱点を突かれる高得点のポケモンを場に出すときの減点。
///
/// # 引数
///
/// - `points`：倒されたときに相手が得るポイント
/// - `exposed`：相手の場に、このポケモンの弱点タイプの攻撃者がいるか
#[must_use]
pub fn exposure_penalty(points: f64, exposed: bool) -> f64 {
    if exposed && points >= 2.0 {
        EXPOSURE_PENALTY
    } else {
        0.0
    }
}

/// 相手の場に、このカードの弱点タイプのポケモンがいるか。
fn weakness_exposed(state: &State, opponent: usize, card: &Card) -> bool {
    let Some(weakness) = weakness_of(card) else {
        return false;
    };
    state
        .enumerate_in_play_pokemon(opponent)
        .any(|(_, pokemon)| energy_type_of(&pokemon.card) == Some(weakness))
}

/// 特性の効果文から、自分に与えるダメージを読み取る。
///
/// 「do N damage to this Pokémon」の文型だけを読む。
#[must_use]
pub fn ability_self_damage(effect: Option<&str>) -> u32 {
    let Some(text) = effect else {
        return 0;
    };
    text.split_once(" damage to this Pok")
        .and_then(|(head, _)| head.rsplit(' ').next())
        .and_then(|n| n.parse::<u32>().ok())
        .unwrap_or(0)
}

/// 自傷する特性のスコア。倒れる残り HP では使わない。
///
/// 対戦ログ：サザンドラが「Roar in Unison」（自分に 30）を残り HP 40 で連打し、自滅していた。
#[must_use]
pub fn score_self_damaging_ability(
    remaining_hp: u32,
    self_damage: u32,
    attaches_energy: bool,
    energy_needed: bool,
) -> f64 {
    if self_damage > 0 && remaining_hp <= self_damage {
        return SELF_KNOCKOUT_ABILITY_VALUE;
    }
    // エネルギーを付ける自傷の特性は、足りているときに使っても自傷するだけ
    // （ユーザーの指摘：E3 のサザンドラに Roar in Unison を重ねて 30 ずつ削っていた）
    if self_damage > 0 && attaches_energy && !energy_needed {
        return NOOP_VALUE - 1.0;
    }
    ABILITY_VALUE
}

/// 特性の効果文。
fn ability_effect(card: &Card) -> Option<&str> {
    match card {
        Card::Pokemon(pokemon) => pokemon
            .ability
            .as_ref()
            .map(|ability| ability.effect.as_str()),
        Card::Trainer(_) => None,
    }
}

/// 行動 1 つのスコア。大きいほど良い。
#[must_use]
pub fn score_action(ctx: &PlayerContext, state: &State, me: usize, action: &SimpleAction) -> f64 {
    match action {
        SimpleAction::Attack(attack) => score_attack(state, me, attack),
        SimpleAction::Evolve {
            evolution,
            in_play_idx,
            ..
        } => score_evolve(ctx, state, me, evolution, *in_play_idx),
        SimpleAction::Attach { attachments, .. } => score_attach(ctx, state, me, attachments),
        SimpleAction::Place(card, in_play_idx) => score_place(ctx, state, me, card, *in_play_idx),
        SimpleAction::UseAbility { in_play_idx } => state.in_play_pokemon[me][*in_play_idx]
            .as_ref()
            .map_or(ABILITY_VALUE, |pokemon| {
                let effect = ability_effect(&pokemon.card);
                let attaches_energy = effect.is_some_and(|t| t.contains("Energy"));
                let attacks = attack_requirements(&pokemon.card);
                let energy_needed = attacks
                    .iter()
                    .any(|required| energy_missing(required, &pokemon.attached_energy) > 0);
                score_self_damaging_ability(
                    pokemon.get_remaining_hp(),
                    ability_self_damage(effect),
                    attaches_energy,
                    energy_needed,
                )
            }),
        SimpleAction::Play { trainer_card } => score_play(ctx, state, me, trainer_card),
        SimpleAction::Retreat(to_in_play_idx) => score_retreat(ctx, state, me, *to_in_play_idx),
        SimpleAction::Activate {
            player,
            in_play_idx,
        } => score_activate(state, me, *player, *in_play_idx),
        SimpleAction::AttachTool {
            in_play_idx,
            tool_card,
        } => {
            let target = state.in_play_pokemon[me][*in_play_idx].as_ref();
            let is_main = target.is_some_and(|pokemon| ctx.is_main_line(&pokemon.card));
            // どうぐの効果には「たね限定」「水限定」などの条件が付いたものがある
            let (evolution_stage, energy) =
                target.map_or((0, None), |pokemon| match &pokemon.card {
                    Card::Pokemon(p) => (p.stage, Some(format!("{:?}", p.energy_type))),
                    Card::Trainer(_) => (0, None),
                });
            score_tool_target_for(
                &tool_card.get_name(),
                *in_play_idx == 0,
                wants_swap(ctx, state, me),
                is_main,
                evolution_stage,
                energy.as_deref(),
            )
        }
        SimpleAction::EndTurn => END_TURN_VALUE,
        // 「してもよい」効果を使わない選択。効果を使う側（既定 1.0 以上）より低くする。
        // 以前は同点で後ろの Noop を選び、マタドガス ex の特性を 33 回の進化で一度も使わなかった
        SimpleAction::Noop => NOOP_VALUE,
        // 相手のバトルポケモンに状態異常を与える選択（ボイラースモックなど）
        SimpleAction::ApplyStatusesToOpponentActive { conditions } => conditions
            .iter()
            .map(|condition| status_condition_value(*condition))
            .sum(),
        // 回復先。実際に減る分だけ価値がある（満タンのキモリにエリカを使っていた）
        SimpleAction::Heal {
            in_play_idx,
            amount,
            ..
        } => state.in_play_pokemon[me][*in_play_idx]
            .as_ref()
            .map_or(0.0, |pokemon| {
                let max_hp = match &pokemon.card {
                    Card::Pokemon(card) => card.hp,
                    Card::Trainer(_) => 0,
                };
                score_heal(
                    pokemon.get_remaining_hp(),
                    max_hp,
                    *amount,
                    *in_play_idx == 0,
                )
            }),
        // スタジアムの効果を使う。サーチやドローはドローソースより先に済ませる
        SimpleAction::UseStadium => STADIUM_VALUE,
        // スタジアムをトラッシュ。自分のものなら選ばない
        SimpleAction::DiscardActiveStadium => {
            score_discard_stadium(state.active_stadium_owner == Some(me))
        }
        // どうぐをトラッシュ。相手のものだけ
        SimpleAction::DiscardToolFromPokemon { player, .. } => {
            if *player == me {
                NOOP_VALUE - 1.0
            } else {
                TRAINER_UNKNOWN_VALUE
            }
        }
        // ベンチを犠牲にして追加ダメージ。倒しきれるときだけ選ぶ（選ばなければ Noop）
        SimpleAction::DiscardOwnBenchedThenDamage {
            damage,
            in_play_idx,
        } => score_sacrifice(ctx, state, me, *in_play_idx, *damage),
        // 効果の解決として積まれる細かい行動。ここでは順序を付けず、
        // 生成された順（deckgym の列挙順）で先頭を選ぶ
        _ => 1.0,
    }
}

/// 回復先のスコア。実際に回復できる量（減っている分と回復量の小さい方）で評価する。
/// 満タンなら「何もしない」（[`NOOP_VALUE`]）より低くする。同じ量ならバトル場を優先する。
#[must_use]
pub fn score_heal(remaining_hp: u32, max_hp: u32, amount: u32, is_active: bool) -> f64 {
    let damage = max_hp.saturating_sub(remaining_hp);
    let healed = damage.min(amount);
    if healed == 0 {
        return NOOP_VALUE - 1.0;
    }
    let base = f64::from(healed);
    if is_active { base + 5.0 } else { base }
}

/// 初期配置で、倒されたときのポイントが大きいポケモンを前に出す減点。
///
/// 1 点なら 0、ex（2 点）はわずか（ツボツボ ex のような壁は前に出せる）、
/// メガ ex（3 点）は壁の加点（60 + 20）を打ち消して余る大きさ。
/// 他に出せるたねがなければ減点があっても前に出る（バトル場を空にはできない）。
#[must_use]
pub fn setup_points_penalty(points: f64) -> f64 {
    if points >= 3.0 {
        150.0
    } else if points >= 2.0 {
        10.0
    } else {
        0.0
    }
}

/// 壁になるたねを前に出す加点。1 進化系のたねの加点（30）を上回る。
const SETUP_WALL_BONUS: f64 = 60.0;

/// 壁のうち「受けるダメージ −N」の特性を持つものの加点。同じ壁なら HP より優先する。
const SETUP_WALL_REDUCTION_BONUS: f64 = 20.0;

/// 壁と見なす HP の下限。
const WALL_MIN_HP: u32 = 100;

/// 壁になるたねか。
///
/// 進化系統に属さないたねで、HP が 100 以上、または「受けるダメージ −N」の特性を持つもの。
/// ユーザーの指摘：ビークイン ex + ツボツボ ex はツボツボで受けつつ削り、裏でビークインを完成させる。
#[must_use]
pub fn is_wall_basic(
    stage: u8,
    hp: u32,
    ability_effect: Option<&str>,
    in_evolution_line: bool,
) -> bool {
    if stage != 0 || in_evolution_line {
        return false;
    }
    hp >= WALL_MIN_HP || ability_effect.is_some_and(is_damage_reduction_text)
}

/// 「This Pokémon takes -20 damage from attacks.」のような特性文か。
fn is_damage_reduction_text(text: &str) -> bool {
    text.contains("takes -") && text.contains("damage from attacks")
}

fn has_damage_reduction(card: &Card) -> bool {
    match card {
        Card::Pokemon(pokemon) => pokemon
            .ability
            .as_ref()
            .is_some_and(|a| is_damage_reduction_text(&a.effect)),
        Card::Trainer(_) => false,
    }
}

fn is_wall_card(ctx: &PlayerContext, card: &Card) -> bool {
    match card {
        Card::Pokemon(pokemon) => is_wall_basic(
            pokemon.stage,
            pokemon.hp,
            pokemon.ability.as_ref().map(|a| a.effect.as_str()),
            ctx.is_evolution_basic(card),
        ),
        Card::Trainer(_) => false,
    }
}

/// 手札に壁になるたねがあるか（初期配置の判断に使う）。
fn hand_has_wall(ctx: &PlayerContext, state: &State, me: usize) -> bool {
    state.hands[me].iter().any(|card| is_wall_card(ctx, card))
}

/// ベンチを犠牲にして追加ダメージを出す選択のスコア。
fn score_sacrifice(
    ctx: &PlayerContext,
    state: &State,
    me: usize,
    in_play_idx: usize,
    damage: u32,
) -> f64 {
    let opponent = (me + 1) % 2;
    let (Some(attacker), Some(defender)) = (
        state.in_play_pokemon[me][0].as_ref(),
        state.in_play_pokemon[opponent][0].as_ref(),
    ) else {
        return NOOP_VALUE - 1.0;
    };
    let base = with_weakness(best_ready_damage(attacker), &attacker.card, &defender.card);
    let boosted = with_weakness(damage, &attacker.card, &defender.card);
    if !sacrifice_for_damage_is_worth(base, boosted, defender.get_remaining_hp()) {
        return NOOP_VALUE - 1.0;
    }
    let preference = state.in_play_pokemon[me][in_play_idx]
        .as_ref()
        .map_or(0.0, |p| {
            score_sacrifice_target(
                p.get_remaining_hp(),
                max_hp_points(&p.card),
                ctx.is_main_line(&p.card),
            )
        });
    KNOCKOUT_VALUE * knockout_points(&defender.card) + preference
}

/// ベンチを犠牲にするときの候補 1 匹のスコア（倒せる場面での比較用）。
///
/// 傷ついているほう、主役の系統でないほうを捨てる。
#[must_use]
pub fn score_sacrifice_target(remaining_hp: u32, max_hp: u32, is_main: bool) -> f64 {
    let health = if max_hp == 0 {
        0.0
    } else {
        f64::from(remaining_hp) / f64::from(max_hp)
    };
    -100.0 * health - if is_main { 200.0 } else { 0.0 }
}

/// 自分のスタジアムを出す・使う価値。ドロー（最大 128）より先に済ませる。
const STADIUM_VALUE: f64 = 130.0;

/// スタジアムを出す価値。
///
/// 毎ターン効果のあるスタジアム（サーチ・ドロー・回復）はドローソースより先に出して使う。
/// 同じスタジアムが既に自分のものとして出ているなら出し直しは無駄。
#[must_use]
pub fn score_stadium_play(effect: Option<&str>, already_mine: bool) -> f64 {
    if already_mine {
        return TRAINER_USELESS_VALUE;
    }
    let per_turn = effect.is_some_and(|t| t.contains("each player's turn"));
    if per_turn {
        STADIUM_VALUE
    } else {
        TRAINER_UNKNOWN_VALUE
    }
}

/// フィールドブロアーの価値。相手のどうぐか相手のスタジアムがなければ無駄
/// （ユーザーの指摘：自分のスタジアムをトラッシュしていた）。
#[must_use]
pub fn score_field_blower(opponent_has_tool: bool, opponent_stadium_in_play: bool) -> f64 {
    if opponent_has_tool || opponent_stadium_in_play {
        60.0
    } else {
        TRAINER_USELESS_VALUE
    }
}

/// 場のスタジアムをトラッシュする選択の価値。自分のものなら選ばない。
#[must_use]
pub fn score_discard_stadium(is_mine: bool) -> f64 {
    if is_mine {
        NOOP_VALUE - 1.0
    } else {
        TRAINER_UNKNOWN_VALUE
    }
}

/// ワザで捨てるエネルギー 1 個あたりの減点。
///
/// 捨てた分は次の番に付け直す必要があるが、倒せる・大きく削れる価値を上回らない程度にする。
const ENERGY_DISCARD_VALUE: f64 = 15.0;

/// 相手に与える状態異常 1 つの価値。
fn status_condition_value(condition: StatusCondition) -> f64 {
    match condition {
        StatusCondition::Asleep | StatusCondition::Paralyzed => STATUS_SLEEP_PARALYSIS_VALUE,
        StatusCondition::Confused => STATUS_CONFUSION_VALUE,
        StatusCondition::Poisoned | StatusCondition::Burned => STATUS_DAMAGE_OVER_TIME_VALUE,
    }
}

/// 評価の最も高い行動を返す。
fn best_of<'a>(candidates: &[(&'a Action, f64)]) -> Option<(&'a Action, f64)> {
    candidates
        .iter()
        .copied()
        .max_by(|(_, a), (_, b)| a.total_cmp(b))
}

/// ポケポケ向けの評価でその場の最善手を選ぶ方策。
pub struct PocketPlayer {
    /// この方策が使うデッキ。
    pub deck: Deck,
    /// デッキから導いた文脈。
    context: PlayerContext,
}

impl PocketPlayer {
    /// デッキから方策を組み立てる。
    #[must_use]
    pub fn new(deck: Deck) -> Self {
        let context = PlayerContext::from_deck(&deck);
        Self { deck, context }
    }
}

impl Player for PocketPlayer {
    fn decision_fn(
        &mut self,
        _rng: &mut StdRng,
        state: &State,
        possible_actions: &[Action],
    ) -> Action {
        let me = possible_actions[0].actor;
        self.context
            .active_energy_locked
            .set(active_energy_locked(state, me, possible_actions));

        let scored: Vec<(&Action, f64)> = possible_actions
            .iter()
            .map(|action| {
                (
                    action,
                    score_action(&self.context, state, me, &action.action),
                )
            })
            .collect();

        // ワザを使うとターンが終わるので、他にやることがなくなってから選ぶ
        let is_attack = |action: &Action| matches!(action.action, SimpleAction::Attack(_));
        let is_end_turn = |action: &Action| matches!(action.action, SimpleAction::EndTurn);
        let others: Vec<(&Action, f64)> = scored
            .iter()
            .copied()
            .filter(|(action, _)| !is_attack(action) && !is_end_turn(action))
            .collect();
        let attacks: Vec<(&Action, f64)> = scored
            .iter()
            .copied()
            .filter(|(action, _)| is_attack(action))
            .collect();

        let best_other = best_of(&others);
        if !attacks.is_empty() && !should_attack_now(best_other.map(|(_, score)| score)) {
            return best_other
                .map(|(action, _)| action.clone())
                .expect("価値のある行動があるはず");
        }

        best_of(&scored)
            .map(|(action, _)| action.clone())
            .expect("選べる行動が 1 つ以上あるはず")
    }

    fn get_deck(&self) -> Deck {
        self.deck.clone()
    }
}

impl Debug for PocketPlayer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "PocketPlayer")
    }
}
