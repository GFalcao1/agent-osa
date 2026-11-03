#!/usr/bin/env bash
# Backup noturno do banco do OSA (SPEC §11, TASK-112): pg_dump para a pasta de backup do share,
# retenção de OSA_BACKUP_RETENTION_DAYS dias (30). Nunca no disco desta máquina nem dentro de
# uma raiz: o índice se reconstrói do acervo, a auditoria não.
#
# Ambiente: OSA_ENV_FILE (o .env da instalação, lido como o app lê) ou OSA_DATABASE_URL e
# OSA_BACKUP_DIR diretos; OSA_PYTHON (o Python da instalação). OSA_BACKUP_ALLOW_LOCAL=1 só para
# ensaio. A senha vai por PGPASSWORD, nunca na linha de comando. Saída: o caminho do dump.
set -euo pipefail
umask 077

fail() { echo "erro: $*" >&2; exit 1; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python="${OSA_PYTHON:-python3}"
retention="${OSA_BACKUP_RETENTION_DAYS:-30}"
[[ "$retention" =~ ^[0-9]+$ && "$retention" -ge 1 ]] || fail "OSA_BACKUP_RETENTION_DAYS inválido"

unset PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE PGSERVICE PGSERVICEFILE PGPASSFILE PGOPTIONS
exports="$("$python" "$here/_osaenv.py" backup-env)" || exit 1
eval "$exports"
dir="$OSA_BACKUP_DIR"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
partial="$dir/.osa-backup-$stamp.partial"
final="$dir/osa-$stamp.dump"
trap 'rm -f -- "$partial"' EXIT

pg_dump --format=custom --no-owner --no-privileges > "$partial"
# A full read of the archive (not only its table of contents) before it gets its name.
pg_restore --file=/dev/null < "$partial" || fail "o dump gerado não é legível"
[[ ! -e "$final" ]] || fail "já existe um dump com este nome: $final"
mv -n -- "$partial" "$final"
[[ -e "$final" && ! -e "$partial" ]] || fail "não foi possível dar o nome final ao dump"
trap - EXIT
"$python" -c 'import hashlib,sys; p=sys.argv[1]; print(hashlib.sha256(open(p,"rb").read()).hexdigest() + "  " + p.rsplit("/",1)[-1])' \
  "$final" > "$final.sha256"

# Retention: only this script's own dumps (exact name), only in this folder. A failure here is
# reported but never hides the new dump.
find "$dir" -maxdepth 1 -type f \
  \( -name 'osa-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.dump' \
     -o -name 'osa-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z.dump.sha256' \) \
  -mtime +"$retention" -print -delete 2>&1 | sed 's/^/retenção: /' >&2 || echo "aviso: retenção não aplicada" >&2
echo "$final"
