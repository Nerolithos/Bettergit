# source /absolute/path/bettergit.zsh
# Remove the old smart-edit source line and restart zsh before migrating.
typeset -g _BETTERGIT_DIR="${${(%):-%N}:A:h}"
bettergit() {
  command python3 "$_BETTERGIT_DIR/bettergit.py" "$@"
}
git() {
  if [[ "${BETTERGIT_ENABLED:-1}" == 0 ]]; then
    command git "$@"
  else
    bettergit run -- "$@"
  fi
}
