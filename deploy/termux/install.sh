#!/usr/bin/env sh
# Akım'ı Termux'ta (Android) kurar. Depo klasöründe:  sh deploy/termux/install.sh
# Derleyici gerekmez: C uzantılı bağımlılıklar saf-Python modunda kurulur.
set -eu
cd "$(dirname "$0")/../.."

if command -v pkg >/dev/null 2>&1; then
  pkg update -y
  pkg install -y python git tmux nano
fi

PY=$(command -v python3 || command -v python || true)
[ -n "$PY" ] || { echo "Python bulunamadı (Termux'ta: pkg install python)" >&2; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Python 3.10 veya üstü gerekli" >&2; exit 1; }

# Android'de derleyici yok; bu değişkenler aiohttp ve bağımlılıklarının C uzantısı derlemesini atlatır.
export AIOHTTP_NO_EXTENSIONS=1 MULTIDICT_NO_EXTENSIONS=1 YARL_NO_EXTENSIONS=1
export FROZENLIST_NO_EXTENSIONS=1 PROPCACHE_NO_EXTENSIONS=1

[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -e .

[ -f config.yaml ] || { cp config.example.yaml config.yaml; echo "config.yaml oluşturuldu"; }
[ -f .env ] || { cp .env.example .env; echo ".env oluşturuldu"; }

.venv/bin/akim --version
cat <<'MSG'

Kurulum tamam. Sıradaki adımlar:
  1. nano .env                      (AKIM_NTFY_TOPIC=akim-<tahmin edilemez bir sey>  veya Telegram bilgileri)
  2. .venv/bin/akim test-notify     (telefona bildirim düşmeli)
  3. tmux new -s akim               (oturum kapansa da sürsün)
  4. sh deploy/termux/run.sh        (tmux'tan çıkmak için Ctrl+B, sonra D)
Ayrıntılar: docs/termux.md
MSG
