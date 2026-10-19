# FrameSlicer

フレーム範囲を等分して、1 スライスごとに 1 ジョブとして QueueWatch に投入する CLI ツールです。

## 目的・背景

キャッシュの書き出しのように、フレーム同士が独立した処理は、フレーム範囲を分けて並列に流せます。
FrameSlicer は、その分割（`split`）と、疑似レンダーファーム QueueWatch への投入（`submit`）を、1 つのコマンドにまとめます。

## 使えるもの、使えないもの

- **使える:** フレーム同士が独立した処理（キャッシュの書き出し、レンダーなど）。
- **使えない:** シミュレーション本体。シミュレーションは、前のフレームの結果に依存するため、フレームで分けると結果が変わります。
- QueueWatch は疑似レンダーファームです（実際のレンダーはしません）。ジョブには「名前」と「優先度」だけが保存され、フレーム範囲そのものは解釈されません。そのため、範囲はジョブ名（`NAME_開始-終了`）に入れます。

## 使用方法

範囲は `開始-終了`（両端を含む。Houdini の流儀）で指定します。

### split: スライスを表示する

```bash
$ frame-slicer split 1001-1240 --slices 8
slice 1/8  1001-1030  (30 frames)
slice 2/8  1031-1060  (30 frames)
slice 3/8  1061-1090  (30 frames)
slice 4/8  1091-1120  (30 frames)
slice 5/8  1121-1150  (30 frames)
slice 6/8  1151-1180  (30 frames)
slice 7/8  1181-1210  (30 frames)
slice 8/8  1211-1240  (30 frames)
```

割り切れないときは、余りを前のスライスに 1 フレームずつ配ります（スライスの大きさの差は最大 1 です）。

```bash
$ frame-slicer split 1001-1010 --slices 3
slice 1/3  1001-1004  (4 frames)
slice 2/3  1005-1007  (3 frames)
slice 3/3  1008-1010  (3 frames)
```

### submit: QueueWatch に投入する

```bash
# 送らずに、何を送るかを確認する
$ frame-slicer submit 1001-1240 --slices 8 --name SHOT010_cache --dry-run
[dry-run] SHOT010_cache_1001-1030 (priority=normal)
...
[dry-run] 8 jobs would be submitted to http://localhost:8000

# 実際に投入する
$ frame-slicer submit 1001-1240 --slices 8 --name SHOT010_cache
submitted SHOT010_cache_1001-1030 (id=1, status=pending)
submitted SHOT010_cache_1031-1060 (id=2, status=pending)
...
submitted SHOT010_cache_1211-1240 (id=8, status=pending)
8 of 8 jobs submitted
```

オプション:

- `--name`: ジョブ名の前半（必須）。スライスの範囲が、末尾に付きます。
- `--priority`: `high` / `normal` / `low`（既定は `normal`）。
- `--api-base-url`: QueueWatch の API のアドレス（既定は `http://localhost:8000`）。
- `--dry-run`: 送らずに、内容だけを表示します。

QueueWatch に届かないときは、終了コード 1 で、投入できた件数を表示します。

```bash
$ frame-slicer submit 1001-1240 --slices 8 --name SHOT010_cache --api-base-url http://localhost:8999
Error: could not reach http://localhost:8999/jobs: [Errno 61] Connection refused
0 of 8 jobs submitted
```

引数の誤りは、終了コード 2 で知らせます。

```bash
$ frame-slicer split 1-3 --slices 5
Usage: frame-slicer split [OPTIONS] FRAMES
Try 'frame-slicer split --help' for help.

Error: more slices (5) than frames (3) in 1-3
```

## 技術スタック

- Python 3.11+
- [Click](https://click.palletsprojects.com/) 8.x（CLI フレームワーク。実行時の依存は、これだけです）
- HTTP は標準ライブラリの `urllib` を使います
- pytest / pytest-cov / mypy(strict) / black / isort / flake8 / pip-audit

## インストール

```bash
git clone <this-repo>
cd frame_slicer
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## テスト

```bash
pytest
black --check . && isort --check . && flake8 frame_slicer/ tests/ && mypy frame_slicer/
```

QueueWatch への投入は、ローカルの簡易サーバーに対する実際の通信で検査しています（応答の形、HTTP エラー、リダイレクト、遅い応答、巨大な応答）。

## 設計メモ

- 範囲の解析と分割（`ranges.py`）は、純関数です。外部に依存せず、単体でテストできます。
- 投入（`queuewatch.py`）は、送信の関数を差し替えられます。CLI のテストでは、実際の通信をしません。
- 安全のため、次の制限を設けています。
  - アドレスは `http` と `https` だけを受け付けます（`file:` などは拒否）。
  - リダイレクトは辿りません（送ったデータが、別の場所へ行かないようにするため）。
  - 通信のタイムアウトは 5 秒、応答の読み込みは 1MB までです。
  - スライス数は、最大 1000 です。
  - ジョブ名は、QueueWatch の制限（1〜100 文字）に合わせ、制御文字を拒否します。

## 既知の制限事項

- 対応するのは、連続したフレーム範囲の等分だけです（間引きや、可変の大きさは未対応）。
- 途中で投入に失敗しても、すでに投入したジョブは取り消しません（件数を表示して終了します）。
- フレーム範囲は、ジョブ名にだけ入ります。QueueWatch は、範囲を使って処理を分けることはしません。
- Windows での動作は、確認していません。

## ライセンス

MIT
