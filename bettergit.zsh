# source /absolute/path/bettergit.zsh
# Remove the old smart-edit source line and restart zsh before migrating.
# Embed the absolute path in the function body: tools that snapshot shell
# functions without globals (e.g. Claude Code) would otherwise call /bettergit.py.
eval "bettergit() { command python3 ${(q)${${(%):-%N}:A:h}}/bettergit.py \"\$@\"; }"
git() {
  if [[ "${BETTERGIT_ENABLED:-1}" == 0 ]]; then
    command git "$@"
  else
    bettergit run -- "$@"
  fi
}
