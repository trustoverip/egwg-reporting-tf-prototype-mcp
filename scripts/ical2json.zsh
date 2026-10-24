#!/usr/bin/env zsh
# Convert an iCalendar (RFC 5545) file to JSON.
#
# Usage: ical2json.zsh [-c] <input.ical> [output.json]
#        ical2json.zsh [-c] <input.ical> -      # write to stdout
#
#   -c   compact output (no indentation or newlines)
#
# With no output path, writes alongside the input with a .json extension.
#
# Output is a JSON array of top-level components (normally one VCALENDAR):
#
#   [ { "type": "VCALENDAR",
#       "properties": {
#         "VERSION": [ { "value": "2.0", "params": {} } ],
#         "RDATE":   [ { "value": "...", "params": {} }, ... ]
#       },
#       "components": [ { "type": "VEVENT", ... } ] } ]
#
# Every property is an array, because iCalendar allows a property to repeat
# within a component (RDATE, EXDATE, ATTENDEE). Property order within a
# component is preserved. Parameter values are joined with "," when a
# parameter is multi-valued.

emulate -L zsh
setopt err_return no_unset pipe_fail extended_glob

local prog=${0:t}

die() { print -ru2 -- "$prog: $*"; return 1 }

local compact=0
while [[ ${1-} == -* && ${1-} != - ]]; do
  case $1 in
    -c|--compact) compact=1; shift ;;
    -h|--help) sed -n '2,/^[^#]/s/^# \{0,1\}//p' $0; return 0 ;;
    --) shift; break ;;
    *) die "unknown option: $1" ;;
  esac
done

(( $# >= 1 && $# <= 2 )) || die "usage: $prog [-c] <input.ical> [output.json]"

local in=$1
local out=${2-${in:r}.json}
[[ -r $in ]] || die "cannot read $in"

# ---------------------------------------------------------------- escaping ---

# JSON-escape a string into the variable named by $1.
json_escape() {
  local __var=$1 __s=$2
  __s=${__s//\\/\\\\}
  __s=${__s//\"/\\\"}
  __s=${__s//$'\n'/\\n}
  __s=${__s//$'\r'/\\r}
  __s=${__s//$'\t'/\\t}
  __s=${__s//$'\b'/\\b}
  __s=${__s//$'\f'/\\f}
  # Remaining C0 control characters have no shorthand; use \u00XX.
  if [[ $__s == *[$'\x01'-$'\x1f']* ]]; then
    local __c __i
    for __i in {1..31}; do
      __c=${(#)__i}
      [[ $__s == *$__c* ]] && __s=${__s//$__c/$(printf '\\u%04x' $__i)}
    done
  fi
  typeset -g $__var=$__s
}

# Undo RFC 6868 parameter-value escaping (^n ^^ ^') into the variable named
# by $1. Parameter values do not use the backslash escaping that TEXT values
# do, so a literal backslash in a parameter is left alone.
param_unescape() {
  local __var=$1 __s=$2
  local -a __parts=("${(@ps:^^:)__s}")
  local __p __o="" __first=1
  for __p in $__parts; do
    (( __first )) || __o+='^'
    __first=0
    __p=${__p//'^n'/$'\n'}
    __p=${__p//"^'"/'"'}
    __o+=$__p
  done
  typeset -g $__var=$__o
}

# Undo RFC 5545 TEXT escaping (\n \N \, \; \\) into the variable named by $1.
ical_unescape() {
  local __var=$1 __s=$2
  # Split on backslash pairs so a literal "\\" is not reinterpreted.
  local -a __parts=("${(@ps:\\\\:)__s}")
  local __p __o=""
  local __first=1
  for __p in $__parts; do
    (( __first )) || __o+='\'
    __first=0
    __p=${__p//'\n'/$'\n'}
    __p=${__p//'\N'/$'\n'}
    __p=${__p//'\,'/,}
    __p=${__p//'\;'/;}
    __o+=$__p
  done
  typeset -g $__var=$__o
}

# ------------------------------------------------------------------ unfold ---

# Read the file and unfold continuation lines (CRLF + single space or tab).
local -a lines=()
local raw n=0
while IFS= read -r raw || [[ -n $raw ]]; do
  raw=${raw%$'\r'}
  if [[ $raw == (' '|$'\t')* && n -gt 0 ]]; then
    lines[n]+=${raw:1}
  else
    [[ -n $raw ]] || continue
    lines+=("$raw")
    (( ++n ))
  fi
done < $in

(( n > 0 )) || die "$in: no content lines found"

# ------------------------------------------------------------------- parse ---

# Parse one content line. Sets p_name, p_value and p_params (name -> value).
local p_name p_value
typeset -A p_params
parse_line() {
  local line=$1 rest pname pval seg
  p_name=""; p_value=""; p_params=()

  if [[ $line != (#b)([A-Za-z0-9-]##)([:\;]*) ]]; then
    print -ru2 -- "$prog: skipping malformed line: ${line:0:60}"
    return 1
  fi
  p_name=${(U)match[1]}
  rest=$match[2]

  while [[ $rest == ';'* ]]; do
    rest=${rest#;}
    # Parameter name runs to '=', but stop at ';' or ':' if '=' is missing.
    seg=${rest%%[=;:]*}
    pname=${(U)seg}
    rest=${rest:${#seg}}
    pval=""
    if [[ $rest == '='* ]]; then
      rest=${rest#=}
      while true; do
        if [[ $rest == '"'* ]]; then          # quoted value: verbatim to quote
          rest=${rest#\"}
          pval+=${rest%%\"*}
          rest=${rest#*\"}
        else                                   # bare value: to , ; or :
          seg=${rest%%[;:,]*}
          pval+=$seg
          rest=${rest:${#seg}}
        fi
        [[ $rest == ','* ]] || break           # multi-valued param
        pval+=,
        rest=${rest#,}
      done
    fi
    param_unescape pval "$pval"
    p_params[$pname]=$pval
  done

  rest=${rest#:}
  ical_unescape p_value "$rest"
}

# ------------------------------------------------------------------- emit ---

local nl=$'\n' ind="  "
if (( compact )); then nl=""; ind=""; fi
pad() { (( compact )) || printf '%*s' $(( $1 * 2 )) ""; }

local -a outbuf=()
emit() { outbuf+=("$1") }

# Render the component whose BEGIN line is at index $idx (global cursor),
# at indentation depth $2. Advances idx past the matching END line.
local idx=1
emit_component() {
  local ctype=$1 depth=$2
  local -a names=() values=() paramsets=()
  local -a kids_pending=()
  local line key esc pk pv pjson first
  local -A seen=()

  # Collect this component's own properties, recursing into nested
  # components. Properties are buffered so they can be printed before the
  # children even when the source interleaves them.
  local -a child_types=() child_starts=()
  while (( idx <= n )); do
    line=$lines[idx]
    if [[ $line == 'END:'* ]]; then
      [[ ${(U)line#END:} == $ctype ]] || print -ru2 -- \
        "$prog: mismatched END:${line#END:} inside $ctype"
      (( ++idx ))
      break
    fi
    if [[ $line == 'BEGIN:'* ]]; then
      child_types+=("${(U)line#BEGIN:}")
      (( ++idx ))
      child_starts+=($idx)
      skip_component "${child_types[-1]}"
      continue
    fi
    if parse_line "$line"; then
      names+=("$p_name"); values+=("$p_value")
      pjson=""
      first=1
      for pk in ${(ko)p_params}; do
        json_escape esc "$pk"; key=$esc
        json_escape esc "$p_params[$pk]"
        (( first )) || pjson+=","
        first=0
        pjson+="\"$key\":\"$esc\""
      done
      paramsets+=("$pjson")
    fi
    (( ++idx ))
  done

  # type
  emit "$(pad $depth){$nl"
  json_escape esc "$ctype"
  emit "$(pad $((depth+1)))\"type\":$([[ -n $nl ]] && print -n ' ')\"$esc\",$nl"

  # properties, grouped by name in order of first appearance
  emit "$(pad $((depth+1)))\"properties\":$([[ -n $nl ]] && print -n ' '){"
  local -a order=()
  local i name
  for (( i = 1; i <= ${#names}; i++ )); do
    name=$names[i]
    (( ${+seen[$name]} )) || { order+=("$name"); seen[$name]=1 }
  done
  local firstprop=1 firstval
  for name in $order; do
    (( firstprop )) || emit ","
    firstprop=0
    emit "$nl"
    json_escape esc "$name"
    emit "$(pad $((depth+2)))\"$esc\":$([[ -n $nl ]] && print -n ' ')[$nl"
    firstval=1
    for (( i = 1; i <= ${#names}; i++ )); do
      [[ $names[i] == $name ]] || continue
      (( firstval )) || emit ",$nl"
      firstval=0
      json_escape esc "$values[i]"
      emit "$(pad $((depth+3)))"
      if (( compact )); then
        emit "{\"value\":\"$esc\",\"params\":{$paramsets[i]}}"
      else
        emit "{ \"value\": \"$esc\", \"params\": {$paramsets[i]} }"
      fi
    done
    emit "$nl$(pad $((depth+2)))]"
  done
  (( firstprop )) || emit "$nl$(pad $((depth+1)))"
  emit "},$nl"

  # nested components
  emit "$(pad $((depth+1)))\"components\":$([[ -n $nl ]] && print -n ' ')["
  local saved=$idx c
  local firstkid=1
  for (( c = 1; c <= ${#child_types}; c++ )); do
    (( firstkid )) || emit ","
    firstkid=0
    emit "$nl"
    idx=$child_starts[c]
    emit_component "$child_types[c]" $((depth+2))
  done
  idx=$saved
  (( firstkid )) || emit "$nl$(pad $((depth+1)))"
  emit "]$nl"

  emit "$(pad $depth)}"
}

# Advance idx past the component of type $1 whose body starts at idx.
skip_component() {
  local ctype=$1 line
  while (( idx <= n )); do
    line=$lines[idx]
    if [[ $line == 'BEGIN:'* ]]; then
      (( ++idx ))
      skip_component "${(U)line#BEGIN:}"
      continue
    fi
    if [[ $line == 'END:'* ]]; then
      (( ++idx ))
      return 0
    fi
    (( ++idx ))
  done
}

# ------------------------------------------------------------------- drive ---

local -a top_types=() top_starts=()
while (( idx <= n )); do
  if [[ $lines[idx] == 'BEGIN:'* ]]; then
    top_types+=("${(U)lines[idx]#BEGIN:}")
    (( ++idx ))
    top_starts+=($idx)
    skip_component "${top_types[-1]}"
  else
    print -ru2 -- "$prog: ignoring line outside any component: ${lines[idx]:0:60}"
    (( ++idx ))
  fi
done

(( ${#top_types} > 0 )) || die "$in: no iCalendar components found"

emit "["
local t firsttop=1
for (( t = 1; t <= ${#top_types}; t++ )); do
  (( firsttop )) || emit ","
  firsttop=0
  emit "$nl"
  idx=$top_starts[t]
  emit_component "$top_types[t]" 1
done
emit "$nl]$nl"

if [[ $out == - ]]; then
  print -rn -- "${(j::)outbuf}"
else
  print -rn -- "${(j::)outbuf}" > $out
  print -ru2 -- "$in: ${#top_types} component(s), $n content lines -> $out"
fi
