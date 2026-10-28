# frame-slicer 仕様

> 対象: コマンドラインツール `frame-slicer`（0.1.0）
> 位置づけ: 振る舞いの取り決め。選択の理由は [ADR](adr/) に残す。

## 1. 目的

フレーム範囲をスライスに分けて、1 スライスを 1 ジョブとして QueueWatch に投入する。
失敗したスライスだけを、同じジョブのまま戻せるようにする。
前段（キャッシュ）の完了を待って、後段（レンダー）を投入できるようにする。

## 2. 用語

| 用語 | 意味 |
| --- | --- |
| 範囲 | `開始-終了`（両端を含む）。`開始-終了x刻み` は、刻みごとのフレームだけ。カンマで複数 |
| スライス | 範囲を切った 1 つ。1 つのジョブになる |
| マニフェスト | 投入の結果を残す JSON。スライスごとの id・状態・失敗の理由 |
| 前段 / 後段 | `--depends-on` で結ぶ 2 つの投入。後段は、同じフレームの前段が成功してから送る |

## 3. コマンド

| コマンド | 入力 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `split FRAMES` | 範囲、切り方 | スライスの一覧（標準出力） | なし |
| `submit FRAMES` | 範囲、切り方、`--name`、任意で `--scene` `--priority` `--manifest` `--depends-on` | 投入したジョブの一覧と件数 | QueueWatch にジョブを作る。マニフェストを書く |
| `retry MANIFEST` | マニフェスト | 各ジョブの扱い（送信 / 再投入 / そのまま） | 未送信を送る。`dead` / `cancelled` を `requeue` する。マニフェストを更新する |
| `status MANIFEST` | マニフェスト | 各ジョブの今の状態 | なし（QueueWatch を読むだけ） |
| `commands FRAMES` | 範囲、切り方、`--tool`、`--scene` | スライスごとのコマンド行 | なし（表示だけ。実行しない） |

### 3.1 切り方

- `--slices N`: 1 つの範囲を N 個に等分する。端数は、前のスライスから 1 フレームずつ配る（大きさの差は最大 1）。
- `--chunk-size N`: 各範囲を N フレームずつに切る。最後だけ小さくなる。
- どちらか一方だけを指定する。複数の範囲に `--slices` は使えない。
- スライス数は、全体で 1〜1000。
- `--heavy 範囲`: 重いフレームを含むスライスを先頭に置く。残りの順序は変えない。

### 3.2 ジョブ

- 名前は `NAME_開始-終了`（刻みがあれば `NAME_開始-終了x刻み`）。1〜100 文字。制御文字は拒否する。
- 本文は `name` `priority` `frame_start` `frame_end`、指定があれば `scene`。
- 刻みのあるスライスは、`submit` で拒否する（QueueWatch は連続範囲しか持たない。[ADR-0005](adr/0005-print-only-commands-and-no-step-submit.md)）。

### 3.3 通信

- アドレスは `--api-base-url` > 環境変数 `QUEUEWATCH_API_URL` > `http://localhost:8000`。`http` と `https` だけ。
- トークンは環境変数 `QUEUEWATCH_API_TOKEN`（`Authorization: Bearer`）。`https` か localhost 宛てにだけ送る。画面にもエラーにも出さない。
- タイムアウトは 5 秒。応答は 1MB まで。リダイレクトは辿らない。
- 再送の条件と待ち方は [ADR-0003](adr/0003-retry-only-failures-that-leave-no-job.md)。

## 4. 終了コード

| コード | 意味 | 例 |
| --- | --- | --- |
| 0 | 成功 | 全ジョブを投入できた |
| 1 | 実行中の失敗 | QueueWatch に届かない / 拒否された / 前段が `dead` / 途中で止まった（投入できた件数を表示し、マニフェストが残っていれば `retry` を案内する）/ `status` がマニフェストやキューを読めない |
| 2 | 使い方の誤り | 範囲が不正 / スライス数が範囲より多い / 名前・優先度・アドレスが不正 / マニフェストがすでにある / `retry` や `--depends-on` のマニフェストを読めない |

エラーは標準エラーへ、成功した行は標準出力へ出す。

## 5. マニフェスト

- JSON。`version`（今は 1）、`api_base_url`、`name`、`priority`、`scene`、`jobs`。
- `jobs` の各要素は `name` `start` `end` `step` `id` `status` `error`。
- 1 件送るたびに書き直す。書き込みは一時ファイルを作ってから置き換えるので、途中で壊れない。
- すでにあるファイルには `submit` で書かない（実行をまたぐ二重投入を防ぐ）。
- トークンは書かない。

## 6. 状態の扱い（QueueWatch の状態）

| 状態 | `retry` の扱い | `--depends-on` の扱い |
| --- | --- | --- |
| `pending` `running` `retrying` `paused` | そのまま | 待つ |
| `success` | そのまま | 次へ進む |
| `dead` `cancelled` | `requeue`（同じ id） | 止める（終了コード 1） |

## 7. 制限

- QueueWatch は `Idempotency-Key` を解釈しない。マニフェストなしで同じ `submit` を 2 回実行すると、ジョブが二重になる。
- `retry` は、前段の完了を待たない。
- Blender の実ワーカーでだけ検証した。husk と hbatch のコマンドは、実行していない。
- シミュレーションのように、前のフレームに依存する処理は、フレームで分けられない。

## 8. 品質の基準

- テストのカバレッジは 100%。mypy strict、flake8、black、isort、vulture が通る。
- 3 OS（Ubuntu / macOS / Windows）× Python 3.11〜3.13 の CI。
- 実ワーカーでの通し検証は [docs/verification/](verification/real-render-blender-4.5.3.md)。
