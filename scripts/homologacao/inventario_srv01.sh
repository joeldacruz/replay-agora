#!/usr/bin/env bash
# HOMOLOGAÇÃO FASE 1 — inventário SOMENTE LEITURA do replayagora-srv01 (servidor da arena).
# NÃO reinicia/parar serviços, NÃO aciona botoeira, NÃO abre a serial, NÃO lê RTSP, NÃO imprime credenciais.
# Uso: bash inventario_srv01.sh | tee /tmp/inventario-srv01-$(date +%Y%m%d-%H%M%S).txt
STAGE="${STAGE:-/opt/replayagora-stage}"; PY="${PY:-python3}"
sec() { printf '\n## %s\n' "$*"; }
sec "host/carga"; hostname; date -u +%FT%TZ; uptime; nproc; free -m | head -2
echo "vmstat (3 amostras, 1 s):"; vmstat 1 3 2>&1 | tail -4
sec "disco"; df -h / /srv /opt 2>&1 | sort -u; for d in /srv/replayagora "$STAGE"; do [ -d "$d" ] && du -sh "$d" 2>&1; done
sec "serviços replayagora*"; systemctl list-units 'replayagora*' --all --no-pager --no-legend 2>&1
for u in $(systemctl list-units 'replayagora*' --all --no-pager --no-legend 2>/dev/null | awk '{print $1}' | grep -E '\.service$'); do
  printf '%-45s active=%s enabled=%s restarts=%s since=%s\n' "$u" "$(systemctl is-active "$u")" "$(systemctl is-enabled "$u" 2>&1)" "$(systemctl show "$u" -p NRestarts --value)" "$(systemctl show "$u" -p ActiveEnterTimestamp --value)"
done
sec "FFmpeg"; ffmpeg -version 2>&1 | head -1; ffprobe -version 2>&1 | head -1; ffmpeg -hide_banner -hwaccels 2>&1 | tail -n +2 | tr '\n' ' '; echo; ls -l /dev/dri 2>&1 | head -4
sec "processos ffmpeg em execução (captura/buffer) — leitura"; ps -eo pid,ni,pcpu,pmem,etime,args --sort=-pcpu 2>/dev/null | grep -E '[f]fmpeg' | sed -E 's#rtsp://[^ ]*#rtsp://<oculto>#g; s#(password|pass|pwd)=[^ ]*#\1=<oculto>#Ig' | cut -c1-200 | head -10
sec "portas em escuta (relay/agente)"; ss -ltnp 2>/dev/null | awk 'NR==1 || /replay|python|relay|ffmpeg/' | cut -c1-140
sec "versão do agente e árvore de código"
for d in "$STAGE" /srv/replayagora/quadra01/app /srv/replayagora/quadra02/app; do [ -d "$d" ] && { echo "$d:"; ls -la --time-style=long-iso "$d" 2>&1 | head -12; }; done
AGDIR="$(find "$STAGE" -maxdepth 3 -type d -name replay_agent 2>/dev/null | head -1)"
if [ -n "$AGDIR" ]; then echo "hash da árvore replay_agent (*.py) em $AGDIR (esperado = f0d2991d44608e79 se igual ao repositório):"; (cd "$(dirname "$AGDIR")" && find replay_agent -name '*.py' -not -path '*/__pycache__/*' | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(sha256sum "$f" | cut -d' ' -f1)" "$f"; done | sha256sum | cut -c1-16); else echo "diretório replay_agent não encontrado em $STAGE"; fi
sec "catálogo local do agente (somente leitura)"
for db in $(find "$STAGE" -maxdepth 4 -name '*.db' 2>/dev/null | head -5); do
  echo "$db"; DBF="$db" "$PY" - <<'PYEOF'
import os, sqlite3
try:
    c = sqlite3.connect(f"file:{os.environ['DBF']}?mode=ro", uri=True, timeout=5); q = lambda s: c.execute(s).fetchall()
    t = {r[0] for r in q("select name from sqlite_master where type='table'")}; print("  tabelas:", sorted(t))
    if "replays" in t:
        print("  replays por quadra/sync:", q("select court_key, sync_state, deleted, count(*), sum(bytes) from replays group by 1,2,3"))
        print("  últimos 5 (sem caminho):", q("select local_id, court_key, captured_at, round(duration,1), bytes, has_audio, sync_state from replays order by created_at desc limit 5"))
    if "jobs" in t: print("  jobs por estado:", q("select state, count(*) from jobs group by 1"))
    if "renders" in t: print("  renders:", q("select state, purpose, count(*) from renders group by 1,2"))
except Exception as e: print("  não foi possível ler:", e)
PYEOF
done
sec "MP4 existentes (tamanho, data, ffprobe e sha256) — leitura; NÃO copia, NÃO move, NÃO altera"
find /srv/replayagora "$STAGE" -type f -name '*.mp4' -printf '%s\t%TY-%Tm-%Td %TH:%TM\t%p\n' 2>/dev/null | sort -k2 | tail -20 > /tmp/.mp4_list.$$
cat /tmp/.mp4_list.$$ | cut -c1-200
while IFS=$'\t' read -r sz dt path; do
  echo "--- $path"; ffprobe -v error -show_entries stream=codec_type,codec_name,width,height,avg_frame_rate,sample_rate -show_entries format=duration -of compact=p=0 "$path" 2>&1 | head -4
  sha256sum "$path" | cut -c1-64
done < /tmp/.mp4_list.$$; rm -f /tmp/.mp4_list.$$
sec "fim"
