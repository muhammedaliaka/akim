#!/usr/bin/env sh
# Akım'ı Termux'ta çalıştırır: uykuyu engeller, hata ile durursa 15 sn sonra yeniden başlatır.
# Ctrl+C (temiz kapanış, çıkış kodu 0) yeniden başlatmaz.  Kullanım:  sh deploy/termux/run.sh
cd "$(dirname "$0")/../.." || exit 1

if command -v termux-wake-lock >/dev/null 2>&1; then
  termux-wake-lock || true
fi

while true; do
  .venv/bin/akim -c config.yaml run && break
  echo "Akım hata ile durdu (kod $?); 15 sn sonra yeniden başlatılıyor. Durdurmak için Ctrl+C." >&2
  sleep 15
done

if command -v termux-wake-unlock >/dev/null 2>&1; then
  termux-wake-unlock || true
fi
