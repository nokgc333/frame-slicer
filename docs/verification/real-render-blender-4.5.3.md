# 実ワーカーでの実行記録（Blender 4.5.3）

> 目的: 分割 → 投入 → 失敗 → 再投入 → 依存ステージまでを、疑似ではなく、実際の QueueWatch ワーカーと実 Blender で通した記録。
> 実施日: 2026-10-28
> 環境: macOS（Apple Silicon）/ Blender 4.5.3 LTS / Redis 8.10.1 / QueueWatch（RENDERER=blender、トークン認証あり）
> 再現: `QUEUEWATCH_DIR=<queuewatch> QW_PYTHON=<Python 3.12> BLENDER_APP=<Blender.app> python scripts/verify_real_render.py`

## 構成

- Redis、QueueWatch の API、Celery のワーカーを、一時フォルダの中で起動する（本番のデータには触れない）。
- API はトークン認証を有効にする。frame-slicer は `QUEUEWATCH_API_TOKEN`（実行ごとに生成）で Bearer を送る。トークンは出力にも記録にも出ない。
- ワーカーは `RENDERER=blender`。サンプルシーン（立方体、Workbench、64x64）を実際にレンダーし、PNG を書き出す。
- **故障の注入**: `BLENDER_PATH` に小さなラッパーを置き、旗のファイルがある間だけ、ジョブ 3 の出力先に対する Blender の起動を異常終了（終了コード 139）させる。壊れた GPU ドライバーを持つ描画ノードの代わり。それ以外は、実 Blender をそのまま実行する。
- ワーカーは `--pool=solo`（1 プロセス）。macOS では Celery の既定（prefork）が起動できないため。Docker の構成（Linux）は prefork のまま。

## 結果（10/10 PASS）

| 項目 | 内容 |
| --- | --- |
| submit-all-slices | 12 フレームを 4 スライスに分け、4 件とも投入できた |
| one-slice-died | 注入した故障で、ジョブ 3 が `dead`（QueueWatch の自動リトライを使い切った）。他の 3 件は `success` |
| good-slices-have-frames | 成功した 3 件が、それぞれ 3 フレームの PNG を書き出した |
| dependent-stage-blocked | 後段（`--depends-on`）は、`dead` の前段を待たずに止まり、終了コード 1 |
| only-ready-slices-were-sent | 後段で投入されたのは、前段が成功した 2 件だけ |
| retry-requeued-the-dead-slice | `retry` が、`dead` の 1 件だけを再投入した（同じ id のまま） |
| retried-slice-has-frames | 再投入したスライスも、3 フレームを書き出した |
| no-duplicate-jobs | キューのジョブ総数が 6（前段 4 + 後段 2）。再投入で増えていない |
| dependent-stage-completed | 故障を直したあと、後段の `retry` で残り 2 件が投入できた |
| every-job-wrote-its-frames | 8 件すべてが 3 フレームずつ（計 24 フレーム）を書き出した |

## 実行の全文

```text
BLENDER_VERSION Blender 4.5.3 LTS (hash 67807e1800cc built 2025-09-09 01:33:39)
QUEUEWATCH_COMMIT 443c9ab

== 1. slice and submit; one slice will fail on purpose ==

$ frame-slicer submit 1-12 --slices 4 --scene cube.blend --name cube --manifest <work>/cache.json
submitted cube_1-3 (id=1, status=pending)
submitted cube_4-6 (id=2, status=pending)
submitted cube_7-9 (id=3, status=pending)
submitted cube_10-12 (id=4, status=pending)
4 of 4 jobs submitted
[exit code 0]
CHECK submit-all-slices PASS
CHECK one-slice-died PASS ['dead', 'success', 'success', 'success']
CHECK good-slices-have-frames PASS

== 2. a dependent stage waits for the cache and stops at the dead slice ==

$ frame-slicer submit 1-12 --slices 4 --scene cube.blend --name lit --depends-on <work>/cache.json --wait-timeout 30 --manifest <work>/render.json
submitted lit_1-3 (id=5, status=pending)
submitted lit_4-6 (id=6, status=pending)
[stderr] Error: the dependency job cube_7-9 is dead
2 of 4 jobs submitted
The manifest <work>/render.json keeps the state; run: frame-slicer retry <work>/render.json
[exit code 1]
CHECK dependent-stage-blocked PASS
CHECK only-ready-slices-were-sent PASS

== 3. the fault is fixed; retry brings back only the dead slice ==

$ frame-slicer retry <work>/cache.json
kept cube_1-3 (id=1, status=success)
kept cube_4-6 (id=2, status=success)
requeued cube_7-9 (id=3)
kept cube_10-12 (id=4, status=success)
4 of 4 jobs submitted
[exit code 0]
CHECK retry-requeued-the-dead-slice PASS
CHECK retried-slice-has-frames PASS
CHECK no-duplicate-jobs PASS total=6

== 4. the dependent stage continues now that the cache is whole ==

$ frame-slicer retry <work>/render.json
kept lit_1-3 (id=5, status=success)
kept lit_4-6 (id=6, status=success)
submitted lit_7-9 (id=7, status=pending)
submitted lit_10-12 (id=8, status=pending)
4 of 4 jobs submitted
[exit code 0]
CHECK dependent-stage-completed PASS
CHECK every-job-wrote-its-frames PASS [3, 3, 3, 3, 3, 3, 3, 3]

== 5. the same range as render commands (printed, not run) ==

$ frame-slicer commands 1-12 --slices 2 --tool blender --scene cube.blend
blender -b cube.blend -noaudio -s 1 -e 6 -a
blender -b cube.blend -noaudio -s 7 -e 12 -a
[exit code 0]
```

## 分かったこと・制限

- 再投入は、新しいジョブを作らず、QueueWatch の `requeue`（`dead` / `cancelled` 向け）を使う。そのため、同じスライスが二重に作られない。
- `retry` は、前段の完了を待たない（待つのは、`submit --depends-on` の間だけ）。上の記録の手順 4 は、前段が全部成功したあとなので問題ないが、前段が未完のまま `retry` すると、後段が先に流れる。
- 故障は注入したもの。実際の GPU ドライバーの故障ではない。再投入の経路（`dead` → `requeue` → `success`）は、実ワーカーで本当に通した。
- 検証した Blender は 4.5.3 のみ。QueueWatch 側は、3.6.0 / 4.0.0 / 4.5.3 / 5.0.0 で検証済み（[記録](https://github.com/nokgc333/queuewatch)の `docs/verification/`）。
- 出力に出る `<work>` は、一時フォルダの代わりに置いた表記。
