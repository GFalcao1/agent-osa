#!/usr/bin/env bash
# Restauração de um dump do OSA num banco VAZIO (TASK-112), com a conferência da auditoria.
#
# Uso: OSA_RESTORE_URL=postgresql://.../banco_vazio scripts/restore.sh /caminho/osa-AAAA.dump
# Com OSA_DATABASE_URL (o banco de origem) também confere a auditoria: como ela só cresce, cada
# evento restaurado tem de ser idêntico ao da origem com o mesmo id. OSA_PYTHON: o Python da
# instalação (com o pacote osa). Tudo numa transação: uma falha não deixa nada pela metade.
set -euo pipefail
umask 077

fail() { echo "erro: $*" >&2; exit 1; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python="${OSA_PYTHON:-python3}"

[[ $# -eq 1 ]] || fail "uso: restore.sh ARQUIVO.dump"
dump="$1"
[[ -f "$dump" && ! -L "$dump" ]] || fail "dump inexistente: $dump"
: "${OSA_RESTORE_URL:?OSA_RESTORE_URL não definido (o banco vazio de destino)}"
if [[ -f "$dump.sha256" ]]; then
  expected="$(cut -d' ' -f1 < "$dump.sha256")"
  actual="$("$python" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$dump")"
  [[ "$expected" == "$actual" ]] || fail "o dump não confere com o seu .sha256"
fi

"$python" "$here/_osaenv.py" check-empty "$OSA_RESTORE_URL" || exit 1
(
  unset PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE PGSERVICE PGSERVICEFILE PGPASSFILE PGOPTIONS
  exports="$("$python" "$here/_osaenv.py" pg-env "$OSA_RESTORE_URL")" || exit 1
  eval "$exports"
  pg_restore --no-owner --no-privileges --exit-on-error --single-transaction \
    --dbname="$PGDATABASE" < "$dump"
) || fail "pg_restore falhou; o banco de destino ficou como estava"
"$python" "$here/_osaenv.py" check-restored "$OSA_RESTORE_URL" || exit 1

if [[ -n "${OSA_DATABASE_URL:-}" ]]; then
  "$python" "$here/_osaenv.py" audit-compare "$OSA_RESTORE_URL" "$OSA_DATABASE_URL" || exit 1
else
  echo "auditoria não conferida com a origem (OSA_DATABASE_URL não definido)"
fi
