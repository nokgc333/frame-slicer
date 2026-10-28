# ADR（設計判断の記録）

| 番号 | 題 |
| --- | --- |
| [0001](0001-use-urllib-from-the-standard-library.md) | HTTP に標準ライブラリの `urllib` を使う |
| [0002](0002-spread-the-remainder-over-the-first-slices.md) | 等分の端数を、前のスライスから 1 フレームずつ配る |
| [0003](0003-retry-only-failures-that-leave-no-job.md) | 再送は「ジョブが作られていない」失敗だけにする |
| [0004](0004-requeue-instead-of-resubmitting.md) | 失敗したジョブは、新規ではなく `requeue` で戻す |
| [0005](0005-print-only-commands-and-no-step-submit.md) | レンダーコマンドは表示だけにし、間引きは `submit` で拒否する |

形式: ステータス / 背景 / 決定 / 結果 / 検討した代案。
