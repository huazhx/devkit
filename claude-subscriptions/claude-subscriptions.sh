# ─── Claude Code subscription launcher ───────────────────────────────
# One-time setup — source this file once:
#
#     source /path/to/claude-subscriptions/claude-subscriptions.sh
#
# That defines the `cc` command in the current shell AND appends a
# `source` line to your shell rc (~/.bashrc by default) so every future
# shell gets `cc` too. It is idempotent — sourcing again never duplicates.
# After setup you only ever touch the config:  `cc edit`.
#
#   cc <name> [claude args…]   launch Claude Code on that subscription
#   cc list                    show subscriptions (tokens masked)
#   cc edit                    open the config in $EDITOR
#   cc install | uninstall     add / remove the rc line by hand
#
# Opt out of auto-install:   CC_NO_AUTOINSTALL=1 source …/claude-subscriptions.sh
# Install into another rc:   CC_RC_FILE=~/.zshrc  source …/claude-subscriptions.sh
#
# `cc` shadows the C compiler alias in interactive shells. If you build
# C on this box, pick another name before sourcing:
#   CC_CMD=ccx source /path/to/claude-subscriptions.sh

: "${CC_SUBS_FILE:=$HOME/.config/claude/subscriptions.conf}"
: "${CC_CMD:=cc}"
: "${CC_RC_FILE:=$HOME/.bashrc}"

_CC_MARKER='# >>> claude-subscriptions (cc) >>>'
_CC_MARKER_END='# <<< claude-subscriptions (cc) <<<'

_cc_trim() { local s="$1"; s="${s#"${s%%[![:space:]]*}"}"; printf '%s' "${s%"${s##*[![:space:]]}"}"; }

_cc_ensure_config() {
  [[ -r "$CC_SUBS_FILE" ]] && return 0
  mkdir -p "$(dirname "$CC_SUBS_FILE")"
  cat > "$CC_SUBS_FILE" << 'EOF'
# Claude Code subscriptions. DO NOT COMMIT. chmod 600.
# Format: name | base_url | auth_var | token | model | small_fast_model
# "-" = leave unset. An all "-" entry launches plain claude (official /login).
#
official|-|-|-|-|-
EOF
  chmod 600 "$CC_SUBS_FILE"
}

_cc_names() {
  [[ -r "$CC_SUBS_FILE" ]] || return 0
  awk -F'|' '{ sub(/#.*/,"") } NF { gsub(/[[:space:]]/,"",$1); if ($1) print $1 }' "$CC_SUBS_FILE"
}

_cc_launch() {
  local want="$1"; shift
  _cc_ensure_config
  # Read the config on fd 3, NOT stdin: claude inherits the loop's stdin,
  # and a `done < file` redirect would feed the config file into the session.
  local line name base authvar token model small _rest
  while IFS= read -r -u 3 line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    [[ -z "${line//[[:space:]]/}" ]] && continue
    IFS='|' read -r name base authvar token model small _rest <<< "$line"
    name=$(_cc_trim "$name")
    [[ "$name" == "$want" ]] || continue
    base=$(_cc_trim "$base"); authvar=$(_cc_trim "$authvar"); token=$(_cc_trim "$token")
    model=$(_cc_trim "$model"); small=$(_cc_trim "$small")

    (   # ── subshell: every export below dies when claude exits ──
      # Clean slate so inherited ANTHROPIC_* vars can't bleed in.
      unset ANTHROPIC_BASE_URL ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN \
            ANTHROPIC_MODEL ANTHROPIC_SMALL_FAST_MODEL

      [[ -n "$base"  && "$base"  != "-" ]] && export ANTHROPIC_BASE_URL="$base"
      [[ -n "$model" && "$model" != "-" ]] && export ANTHROPIC_MODEL="$model"
      [[ -n "$small" && "$small" != "-" ]] && export ANTHROPIC_SMALL_FAST_MODEL="$small"

      if [[ -n "$authvar" && "$authvar" != "-" ]]; then
        if [[ -z "$token" || "$token" == "-" ]]; then
          >&2 echo "cc: subscription '$want' has no token in $CC_SUBS_FILE"
          exit 1
        fi
        export "$authvar=$token"
      fi

      exec claude "$@" 3<&-
    )
    return
  done 3< "$CC_SUBS_FILE"
  >&2 echo "cc: unknown subscription '$want' (try: $CC_CMD list)"
  return 1
}

_cc_list() {
  _cc_ensure_config
  local line name base authvar token model small _rest masked first=1
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    [[ -z "${line//[[:space:]]/}" ]] && continue
    IFS='|' read -r name base authvar token model small _rest <<< "$line"
    name=$(_cc_trim "$name"); base=$(_cc_trim "$base"); authvar=$(_cc_trim "$authvar")
    token=$(_cc_trim "$token"); model=$(_cc_trim "$model"); small=$(_cc_trim "$small")
    [[ -z "$name" ]] && continue

    masked="(none)"
    if [[ -n "$token" && "$token" != "-" ]]; then
      if (( ${#token} > 12 )); then masked="${token:0:6}…${token: -4}"; else masked="(set)"; fi
    fi

    (( first )) || echo
    first=0
    echo "$name"
    echo "  BASE_URL  ${base:--}"
    echo "  AUTH      ${authvar:--} = $masked"
    echo "  MODEL     ${model:--}"
    echo "  SMALL     ${small:--}"
  done < "$CC_SUBS_FILE"
  if (( first )); then
    echo "No subscriptions configured. Run: $CC_CMD edit"
  fi
}

# Absolute path to this script, whether sourced or executed.
_cc_self_path() {
  local src="${BASH_SOURCE[0]:-$0}"
  case "$src" in
    /*) printf '%s\n' "$src" ;;
    *)  local dir
        dir="$(cd "$(dirname -- "$src")" >/dev/null 2>&1 && pwd)" || return 1
        printf '%s/%s\n' "$dir" "$(basename -- "$src")" ;;
  esac
}

# Append a `source <self>` line to the shell rc, once. Sets
# _CC_INSTALLED_NOW=1 only when it actually wrote the line.
_cc_install() {
  _CC_INSTALLED_NOW=
  local rc="$CC_RC_FILE" self
  self="$(_cc_self_path)"
  if [[ -z "$self" || ! -r "$self" ]]; then
    >&2 echo "cc: can't resolve this script's path — add 'source <path>' to $rc by hand"
    return 1
  fi
  if [[ -f "$rc" ]] && grep -qF "$_CC_MARKER" "$rc"; then
    return 0   # already installed
  fi
  {
    printf '\n%s\n'      "$_CC_MARKER"
    printf 'source %q\n' "$self"
    printf '%s\n'        "$_CC_MARKER_END"
  } >> "$rc" || { >&2 echo "cc: failed to write $rc"; return 1; }
  _CC_INSTALLED_NOW=1
  >&2 echo "cc: launcher added to $rc"
  return 0
}

# Remove the marker block from the shell rc.
_cc_uninstall() {
  local rc="$CC_RC_FILE"
  if [[ ! -f "$rc" ]] || ! grep -qF "$_CC_MARKER" "$rc"; then
    >&2 echo "cc: launcher not found in $rc"
    return 0
  fi
  local tmp
  tmp="$(mktemp)" || return 1
  awk -v s="$_CC_MARKER" -v e="$_CC_MARKER_END" '
    $0==s { skip=1; next }
    skip && $0==e { skip=0; next }
    !skip { print }
  ' "$rc" > "$tmp" && cat "$tmp" > "$rc"
  rm -f "$tmp"
  >&2 echo "cc: launcher removed from $rc (open a new shell to finish)"
}

_cc_help() {
  echo "Usage: $CC_CMD <name> [claude args…]   launch Claude Code on a subscription"
  echo "       $CC_CMD list                    show subscriptions (tokens masked)"
  echo "       $CC_CMD edit                    edit $CC_SUBS_FILE"
  echo "       $CC_CMD install | uninstall     add / remove the $CC_RC_FILE source line"
}

_cc_main() {
  case "${1:-}" in
    ""|list|ls)     _cc_list ;;
    edit)           _cc_ensure_config && "${EDITOR:-vi}" "$CC_SUBS_FILE" ;;
    install)        _cc_install ;;
    uninstall)      _cc_uninstall ;;
    help|-h|--help) _cc_help ;;
    *)              _cc_launch "$@" ;;
  esac
}

# Define the `cc` command in the current shell.
eval "${CC_CMD}() { _cc_main \"\$@\"; }"

if [[ -n "${BASH_VERSION:-}" ]]; then
  _cc_completions() {
    COMPREPLY=($(compgen -W "list edit install uninstall help $(_cc_names)" -- "${COMP_WORDS[COMP_CWORD]}"))
  }
  complete -F _cc_completions "$CC_CMD"
fi

# ── One-time setup ────────────────────────────────────────────────────
# Persist ourselves to the shell rc so future shells get `cc` for free.
# Idempotent; opt out with CC_NO_AUTOINSTALL=1.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then _cc_sourced=1; else _cc_sourced=0; fi

if [[ -z "${CC_NO_AUTOINSTALL:-}" ]]; then
  _cc_install
  if [[ -n "${_CC_INSTALLED_NOW:-}" ]]; then
    if (( _cc_sourced )); then
      >&2 echo "cc: ready — run '$CC_CMD edit' to add subscriptions, then '$CC_CMD <name>'."
    else
      >&2 echo "cc: installed — run 'source $CC_RC_FILE' (or open a new shell), then '$CC_CMD edit'."
    fi
  fi
  unset _CC_INSTALLED_NOW
fi
unset _cc_sourced
