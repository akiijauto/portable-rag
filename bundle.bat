@echo off
cd /d %~dp0
python bundle_app.py
rem 正常終了（画面の「終了」ボタン / Ctrl+C）なら窓をそのまま閉じる。
rem エラーで落ちたときだけ止めて、内容を読めるようにする。
if errorlevel 1 (
  echo.
  echo [エラー] 異常終了しました。上の内容を確認してください。
  pause
)
