#!/bin/sh
# Jadikan port 8080 public SETIAP KALI codespace start.
# (Visibilitas port Codespaces reset ke "private" setiap kali codespace
#  tidur/mati — skrip ini menyembuhkannya otomatis via gh CLI, yang di
#  dalam Codespaces sudah ter-autentikasi dengan GITHUB_TOKEN bawaan.)
LOG=/tmp/gh-vis.log
echo "=== public_port.sh $(date) ===" >> "$LOG"
i=0
while [ $i -lt 8 ]; do
  i=$((i + 1))
  sleep 8
  if gh codespace ports visibility 8080:public -c "$CODESPACE_NAME" >> "$LOG" 2>&1; then
    echo "port 8080 PUBLIC (percobaan $i) OK" >> "$LOG"
    exit 0
  fi
  echo "percobaan $i gagal, ulang..." >> "$LOG"
done
echo "GAGAL menjadikan port public setelah $i percobaan" >> "$LOG"
exit 1
