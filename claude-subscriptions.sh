# ─── Claude Code subscription launcher ───────────────────────────────
# Sourced from the shell rc. Subscriptions are read from $CC_SUBS_FILE
# on every launch, so config edits take effect immediately.
#
#   cc <name> [claude args…]   launch Claude Code on that subscription
#   cc list                    show subscriptions (tokens masked)
#   cc edit                    open the config in $EDITOR
#
# `cc` shadows the C compiler alias in interactive shells. If you build
# C on this box, pick another name before the source line in your rc:
#   CC_CMD=ccx source ~/scripts/claude-subscriptions.sh

: "${CC_SUBS_FILE:=$HOME/.config/claude/subscriptions.conf}"
: "${CC_CMD:=cc}"

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

_cc_help() {
  echo "Usage: $CC_CMD <name> [claude args…]   launch Claude Code on a subscription"
  echo "       $CC_CMD list                    show subscriptions (tokens masked)"
  echo "       $CC_CMD edit                    edit $CC_SUBS_FILE"
}

_cc_main() {
  case "${1:-}" in
    ""|list|ls)     _cc_list ;;
    edit)           _cc_ensure_config && "${EDITOR:-vi}" "$CC_SUBS_FILE" ;;
    help|-h|--help) _cc_help ;;
    *)              _cc_launch "$@" ;;
  esac
}

eval "${CC_CMD}() { _cc_main \"\$@\"; }"

if [[ -n "${BASH_VERSION:-}" ]]; then
  _cc_completions() {
    COMPREPLY=($(compgen -W "list edit help $(_cc_names)" -- "${COMP_WORDS[COMP_CWORD]}"))
  }
  complete -F _cc_completions "$CC_CMD"
fi
