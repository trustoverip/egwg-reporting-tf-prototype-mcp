#!/usr/bin/env zsh
#
# fetch-zoom-assets.zsh — download LFX Meetings / Zoom assets for a project.
#
# Usage: fetch-zoom-assets.zsh [options]
#
#   --slug <slug>     LFX calendar slug (default: ToIP)
#   --from <date>     first day to include, YYYY-MM-DD (default: 2026-07-01)
#   --to <date>       last day to include, YYYY-MM-DD (default: today, UTC)
#   --out <dir>       output root (default: mcp_data_generation/data/raw/zoom)
#   --jobs <n>        meetings fetched in parallel, 1-16 (default: 4)
#   --manifest <f>    report path (default: <out>/fetch-manifest.tsv)
#   --force           re-fetch assets already present on disk
#   --list-only       list the meetings that would be fetched, then stop
#   -h, --help        this text
#
# For each past meeting it writes, under <out>/<project-slug>/<series-slug>/:
#
# The project slug is the one LFX uses in its own calendar URL, so a meeting
# shown at .../meetings/besu lands in <out>/besu/. A parent calendar such as
# lf-decentralized-trust therefore fans out into one directory per project.
#
#   <stem>.transcript.vtt   Zoom transcript
#   <stem>.cc.vtt           Zoom closed captions
#   <stem>newChat.txt       in-meeting chat, when Zoom kept one
#   <stem>.summary.md       LFX AI summary rendered as markdown
#   metadata.json           project / organizational_unit / title
#
# <stem> is Zoom's own recording prefix (e.g. GMT20261021-160053_Recording) when
# a Zoom asset is retrieved, otherwise GMT<date>-<time> built from the meeting's
# scheduled start so that summary-only meetings still get a stable name.
#
# EVERY ASSET IS OPTIONAL. A meeting may have no recording, no transcript, no
# captions, no chat, or no AI summary; that is normal and is not an error. Each
# asset gets one of these states in the manifest:
#
#   ok           fetched this run, or already on disk (without --force)
#   missing      the source does not offer this asset for this meeting
#   error        the source should have had it but the fetch failed
#
# All endpoints used are the public ones; no login or API token is required.
# The run is resumable: re-running skips assets already on disk unless --force.
#
# Requires: curl, jq, date (BSD or GNU).

emulate -L zsh
setopt no_unset pipe_fail extended_glob

typeset -g PROG=${0:t}
typeset -g SELF=${0:A}
typeset -g UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36'
typeset -g LFX='https://pcc-bff.platform.linuxfoundation.org/production/api/v2/itx-services'
typeset -g RETRIES=3

die()  { print -ru2 -- "$PROG: $*"; exit 1 }
warn() { print -ru2 -- "$PROG: $*" }

# curl with retry/backoff. Usage: fetch <outfile> <url> [extra curl args...]
# Returns 0 only when curl succeeded and the response body is non-empty.
fetch() {
  local out=$1 url=$2; shift 2
  local attempt=1 rc=0
  while (( attempt <= RETRIES )); do
    rc=0
    curl -sS -A "$UA" --max-time 90 --connect-timeout 20 \
         -H 'Referer: https://zoom.us/' "$@" -o "$out" "$url" 2>/dev/null || rc=$?
    if (( rc == 0 )) && [[ -s $out ]]; then
      return 0
    fi
    (( attempt == RETRIES )) && break
    sleep $(( attempt * 2 ))
    (( attempt += 1 ))
  done
  return 1
}

# Extract a single-quoted JS value, e.g. js_value fileId file.html
js_value() {
  local key=$1 file=$2
  [[ -r $file ]] || return 1
  grep -oE "${key}:[[:space:]]*'[^']+'" "$file" 2>/dev/null \
    | head -1 | sed -E "s/.*'([^']+)'.*/\1/"
}

# Add one month to a YYYY-MM-DD date, portably (BSD/macOS or GNU date).
month_after() {
  local d=$1 r
  r=$(date -u -j -v+1m -f %Y-%m-%d "$d" +%Y-%m-%d 2>/dev/null) && { print -r -- "$r"; return 0 }
  r=$(date -u -d "$d +1 month" +%Y-%m-%d 2>/dev/null) && { print -r -- "$r"; return 0 }
  return 1
}

# Add one day, portably. The calendar endpoint treats end_date as exclusive,
# so the last day the caller asked for has to be requested as end_date+1.
day_after() {
  local d=$1 r
  r=$(date -u -j -v+1d -f %Y-%m-%d "$d" +%Y-%m-%d 2>/dev/null) && { print -r -- "$r"; return 0 }
  r=$(date -u -d "$d +1 day" +%Y-%m-%d 2>/dev/null) && { print -r -- "$r"; return 0 }
  return 1
}

today_utc() {
  local r
  r=$(date -u +%Y-%m-%d 2>/dev/null) && { print -r -- "$r"; return 0 }
  return 1
}

# True when $1 is a real calendar date, not just YYYY-MM-DD shaped.
valid_date() {
  local d=$1
  [[ $d == [0-9](#c4)-[0-9](#c2)-[0-9](#c2) ]] || return 1
  date -u -j -f %Y-%m-%d "$d" +%Y-%m-%d >/dev/null 2>&1 && return 0
  date -u -d "$d" +%Y-%m-%d >/dev/null 2>&1 && return 0
  return 1
}

# Render the LFX summary JSON (an array) as markdown on stdout.
summary_markdown() {
  jq -r '
    # null-only coalesce; a plain // would also swallow false and 0
    def nz(x): if x == null then "" else (x | tostring) end;
    if (type == "array" and length > 0) then .[0] else empty end
    | . as $s
    | ( [ "# " + ( nz($s.summary_title) | if . == "" then "Meeting summary" else . end ),
          "",
          "- **Meeting:** "         + nz($s.topic),
          "- **Start (UTC):** "     + nz($s.pastMeetingStartTime),
          "- **Duration (min):** "  + nz($s.pastMeetingDuration),
          "- **Meeting ID:** "      + nz($s.meeting_and_occurrence_id),
          "- **Zoom UUID:** "       + nz($s.zoom_meeting_uuid),
          "- **Approved:** "        + nz($s.approved),
          "" ]
      + ( if nz($s.agenda) != "" then [ "## Agenda", "", nz($s.agenda), "" ] else [] end )
      + ( ( if nz($s.edited_summary_overview) != ""
            then nz($s.edited_summary_overview)
            else nz($s.summary_overview) end ) as $ov
          | if $ov != "" then [ "## Overview", "", $ov, "" ] else [] end )
      + ( if (($s.summary_details // []) | length) > 0
          then [ "## Details", "" ]
               + [ ($s.summary_details // [])[]
                   | "### " + nz(.label) + "\n\n" + nz(.summary) + "\n" ]
          else [] end )
      + ( ( if (($s.edited_next_steps // []) | length) > 0
            then $s.edited_next_steps else ($s.next_steps // []) end ) as $ns
          | if ($ns | length) > 0
            then [ "## Next steps", "" ] + [ $ns[] | "- " + (. | tostring) ] + [ "" ]
            else [] end )
      ) | join("\n")
  '
}

# =========================================================== worker mode ====
# Internal: process one meeting. Args: _one <meeting.json> <out> <force>
if [[ ${1-} == _one ]]; then
  (( $# == 4 )) || die "internal: _one needs 3 arguments"
  local payload out force tmp
  [[ -r $2 ]] || die "internal: cannot read meeting file $2"
  payload=$(<"$2") || die "internal: cannot read meeting file $2"
  [[ -n $payload ]] || die "internal: empty meeting file $2"
  out=$3
  force=$4
  [[ -n $out ]] || die "internal: empty out dir"

  tmp=$(mktemp -d) || die "cannot create temp dir"
  trap '[[ -n ${tmp:-} && -d ${tmp:?} ]] && rm -rf -- "${tmp:?}"' EXIT INT TERM

  local title start mid occ rec sumurl series pslug pname
  title=$(print -r -- "$payload"  | jq -r '.title // ""')
  start=$(print -r -- "$payload"  | jq -r '.start // ""')
  mid=$(print -r -- "$payload"    | jq -r '.meeting_id // ""')
  occ=$(print -r -- "$payload"    | jq -r '.occurrence_id // ""')
  rec=$(print -r -- "$payload"    | jq -r '.recording // ""')
  sumurl=$(print -r -- "$payload" | jq -r '.ai_summary_url // ""')
  series=$(print -r -- "$payload" | jq -r '.series // ""')
  pslug=$(print -r -- "$payload"  | jq -r '.project_slug // ""')
  pname=$(print -r -- "$payload"  | jq -r '.project_name // ""')
  [[ -n $series ]] || series=unknown-meeting
  [[ -n $pslug ]]  || pslug=unknown-project
  [[ -n $pname ]]  || pname=$pslug

  # Meetings on a parent calendar belong to individual projects, so each lands
  # under its own project directory: <out>/<project-slug>/<series-slug>/.
  # The slug is the one LFX uses in its own calendar URL
  # (.../meetings/besu -> besu), lowercased for a stable directory name.
  local dir=$out/$pslug/$series
  mkdir -p -- "$dir" || die "cannot create $dir"

  # Fallback stem from the scheduled start; replaced by Zoom's own when known.
  local stem="GMT${${start:0:10}//-/}-${${start:11:8}//:/}_Recording"
  local st_tr=missing st_cc=missing st_chat=missing st_sum=missing
  local note=""

  # ---- Zoom chain: share link -> share-info -> play page -> play/info ------
  if [[ -n $rec ]]; then
    local jar=$tmp/jar
    if fetch "$tmp/share.html" "$rec" -L -c "$jar" -b "$jar"; then
      local shtok redirect fid
      shtok=$(js_value meetingId "$tmp/share.html") || shtok=""
      redirect=""
      if [[ -n $shtok ]]; then
        if fetch "$tmp/shareinfo.json" \
             "https://zoom.us/nws/recording/1.0/play/share-info/$shtok" -b "$jar" -c "$jar"; then
          redirect=$(jq -r '.result.redirectUrl // empty' "$tmp/shareinfo.json" 2>/dev/null) || redirect=""
        fi
      fi
      fid=""
      if [[ -n $redirect ]]; then
        if fetch "$tmp/play.html" \
             "https://zoom.us${redirect}?accessLevel=meeting&canPlayFromShare=true&from=share_recording_detail&continueMode=true&oldStyle=true&componentName=rec-play" \
             -b "$jar" -c "$jar"; then
          fid=$(js_value fileId "$tmp/play.html") || fid=""
        fi
      fi

      if [[ -z $fid ]]; then
        st_tr=error; st_cc=error; note="zoom-play-token-unavailable"
      elif ! fetch "$tmp/info.json" \
              "https://zoom.us/nws/recording/1.0/play/info/$fid" -b "$jar" -c "$jar"; then
        st_tr=error; st_cc=error; note="zoom-play-info-failed"
      elif ! jq -e '.status == true' "$tmp/info.json" >/dev/null 2>&1; then
        st_tr=error; st_cc=error
        note=$(jq -r '.errorMessage // "zoom-play-info-rejected"' "$tmp/info.json" 2>/dev/null)
        note=${note// /-}
      else
        local turl curl_url
        turl=$(jq -r '.result.transcriptUrl // empty' "$tmp/info.json" 2>/dev/null) || turl=""
        curl_url=$(jq -r '.result.ccUrl // empty' "$tmp/info.json" 2>/dev/null) || curl_url=""

        # Transcript and captions. The server names the file, which yields the stem.
        local kind url fname
        for kind url in transcript "$turl" cc "$curl_url"; do
          if [[ -z $url ]]; then
            continue                     # Zoom offers no such asset: stays "missing"
          fi
          [[ $url == http* ]] || url="https://zoom.us$url"
          if ! fetch "$tmp/f.$kind" "$url" -b "$jar" -D "$tmp/h.$kind"; then
            [[ $kind == transcript ]] && st_tr=error || st_cc=error
            continue
          fi
          fname=$(grep -i '^content-disposition' "$tmp/h.$kind" 2>/dev/null \
                  | sed -E 's/.*filename=\"?([^\";[:space:]]+)\"?.*/\1/' | tr -d '\r')
          if [[ -z $fname ]]; then
            # No filename offered: fall back to the computed stem.
            [[ $kind == transcript ]] && fname=$stem.transcript.vtt || fname=$stem.cc.vtt
          fi
          # Trust only the basename from the remote header.
          fname=${fname:t}
          case $fname in
            (*.transcript.vtt) stem=${fname%.transcript.vtt} ;;
            (*.cc.vtt)         stem=${fname%.cc.vtt} ;;
          esac
          if [[ -s $dir/$fname && $force != 1 ]]; then
            [[ $kind == transcript ]] && st_tr=ok || st_cc=ok
            continue
          fi
          if mv -f -- "$tmp/f.$kind" "$dir/$fname"; then
            [[ $kind == transcript ]] && st_tr=ok || st_cc=ok
          else
            [[ $kind == transcript ]] && st_tr=error || st_cc=error
          fi
        done

        # In-meeting chat. play/info carries it as xmppList (older tenants use
        # meetingChatList / chatList). Threaded replies live in .comments on the
        # parent message, so they are flattened in and re-sorted by timestamp.
        # Output matches Zoom's own newChat.txt layout:
        #   HH:MM:SS<TAB>Sender Name:<TAB>message
        local chat_target=$dir/${stem}newChat.txt
        if [[ -s $chat_target && $force != 1 ]]; then
          st_chat=ok
        elif jq -e '
               ((.result.xmppList // .result.meetingChatList // .result.chatList // [])
                | length) > 0' "$tmp/info.json" >/dev/null 2>&1; then
          if jq -r '
                 [ (.result.xmppList // .result.meetingChatList // .result.chatList // [])[]
                   | ., ((.comments // [])[]) ]
                 | map(select((.deleted // false) | not))
                 | map(select(((.content // .message // .text // "") | tostring) != ""))
                 | sort_by(.time // 0)
                 | .[]
                 | ((.timeFromMeetingStart // "") | tostring)
                   + "\t" + ((.senderName // .name // .sender // "Unknown") | tostring) + ":"
                   + "\t" + ((.content // .message // .text) | tostring | gsub("[\r\n]+"; " "))
                 ' "$tmp/info.json" > "$tmp/chat.txt" 2>/dev/null && [[ -s $tmp/chat.txt ]]; then
            if mv -f -- "$tmp/chat.txt" "$chat_target"; then st_chat=ok; else st_chat=error; fi
          else
            st_chat=error
          fi
        fi
      fi
    else
      st_tr=error; st_cc=error; note="zoom-share-link-unreachable"
    fi
  fi

  # ---- LFX AI summary -> markdown -----------------------------------------
  if [[ -n $sumurl ]]; then
    local sum_target=$dir/$stem.summary.md
    if [[ -s $sum_target && $force != 1 ]]; then
      st_sum=ok
    else
      local pw=""
      [[ $sumurl == *password=* ]] && pw=${${sumurl##*password=}%%[&#]*}
      if [[ -z $pw || -z $mid || -z $occ ]]; then
        st_sum=error; [[ -n $note ]] || note="summary-url-unparsable"
      elif fetch "$tmp/sum.json" "$LFX/public/past_meetings/$mid-$occ/summaries?password=$pw"; then
        if jq -e 'type == "array" and length > 0' "$tmp/sum.json" >/dev/null 2>&1; then
          if summary_markdown < "$tmp/sum.json" > "$tmp/sum.md" 2>/dev/null && [[ -s $tmp/sum.md ]]; then
            if mv -f -- "$tmp/sum.md" "$sum_target"; then st_sum=ok; else st_sum=error; fi
          else
            st_sum=error
          fi
        else
          st_sum=missing          # endpoint answered, but holds no summary yet
        fi
      else
        st_sum=error
      fi
    fi
  fi

  # Series-level metadata.json, written once per series.
  local purl="https://zoom-lfx.platform.linuxfoundation.org/meetings/$pslug"
  if [[ ! -f $dir/metadata.json ]]; then
    jq -n --arg t "$title" \
      --arg p "$pname" --arg u "$purl" \
      '{project:$p, organizational_unit:[$t], title:$t, source_url:$u}' \
      > "$tmp/metadata.json" 2>/dev/null \
      && mv -f -- "$tmp/metadata.json" "$dir/metadata.json" 2>/dev/null || true
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$start" "$mid-$occ" "$pslug" "$series" "$stem" \
    "$st_tr" "$st_cc" "$st_chat" "$st_sum" "$note" "$title"
  print -ru2 -- "  ${start:0:16}  transcript=$st_tr cc=$st_cc chat=$st_chat summary=$st_sum  ${pslug}/${title:0:38}"
  exit 0
fi

# ================================================================ driver ====
local slug=ToIP from=2026-07-01 to="" out="" jobs=4 manifest="" force=0 list_only=0
while (( $# )); do
  case $1 in
    --slug)     (( $# >= 2 )) || die "--slug needs a value";     slug=$2;     shift 2 ;;
    --from)     (( $# >= 2 )) || die "--from needs a value";     from=$2;     shift 2 ;;
    --to)       (( $# >= 2 )) || die "--to needs a value";       to=$2;       shift 2 ;;
    --out)      (( $# >= 2 )) || die "--out needs a value";      out=$2;      shift 2 ;;
    --jobs)     (( $# >= 2 )) || die "--jobs needs a value";     jobs=$2;     shift 2 ;;
    --manifest) (( $# >= 2 )) || die "--manifest needs a value"; manifest=$2; shift 2 ;;
    --force)     force=1; shift ;;
    --list-only) list_only=1; shift ;;
    -h|--help)  sed -n '3,/^[^#]/s/^# \{0,1\}//p' "$SELF"; exit 0 ;;
    --)         shift; break ;;
    *)          die "unknown option: $1 (try --help)" ;;
  esac
done

for cmd in curl jq date mktemp; do
  whence -p $cmd >/dev/null || die "required command not found: $cmd"
done

[[ -n $to ]] || to=$(today_utc) || die "cannot determine today's date"
for d in $from $to; do
  valid_date "$d" || die "bad date: $d (want a real YYYY-MM-DD)"
done
[[ $from > $to ]] && die "--from ($from) is after --to ($to)"
[[ $jobs == [0-9]## ]] && (( jobs >= 1 && jobs <= 16 )) || die "--jobs must be 1..16"
[[ -n $slug ]] || die "--slug must not be empty"

: ${out:=mcp_data_generation/data/raw/zoom}
: ${manifest:=$out/fetch-manifest.tsv}
mkdir -p -- "$out" || die "cannot create $out"
out=${out:A}

local work
work=$(mktemp -d) || die "cannot create temp dir"
trap '[[ -n ${work:-} && -d ${work:?} ]] && rm -rf -- "${work:?}"' EXIT INT TERM

# ---- list the calendar, a month at a time (public endpoint, no auth) -------
print -ru2 -- "$PROG: listing '$slug' meetings $from .. $to"
local to_excl
to_excl=$(day_after "$to") || die "date arithmetic failed (need BSD or GNU date)"
local cur=$from nxt="" n=0 chunks=0
while :; do
  nxt=$(month_after "$cur") || die "date arithmetic failed (need BSD or GNU date)"
  [[ $nxt > $to_excl ]] && nxt=$to_excl
  n=$(( n + 1 ))
  if fetch "$work/chunk.$n.json" "$LFX/public/meetings/$slug/past?start_date=$cur&end_date=$nxt"; then
    chunks=$(( chunks + 1 ))
  else
    warn "warning: could not list $cur .. $nxt (continuing)"
  fi
  [[ $nxt == $to_excl ]] && break
  cur=$nxt
done
(( chunks > 0 )) || die "could not list any meetings for '$slug' (is the slug right?)"

# ---- flatten, de-duplicate, derive a series slug --------------------------
if ! jq -c -s --arg from "$from" --arg to "$to" '
      # The calendar response carries the display names for the parent project
      # and every sub-project, so a project_slug can be turned into a real name
      # for metadata.json instead of guessing one.
      ( [ .[].project.Projects[]? | select(.Slug != null)
          | { key: (.Slug | ascii_downcase), value: .Name } ]
        + [ .[].project | select(.Slug != null)
            | { key: (.Slug | ascii_downcase), value: .Name } ]
        | from_entries ) as $names
      | [ .[].meetings[]? ]
      | map(
          ((.extendedProps.project_slug // "") | ascii_downcase) as $ps
          | {
          title:          (.title // "Untitled meeting"),
          start:          (.start // ""),
          occurrence_id:  (.id | tostring),
          meeting_id:     (.extendedProps.meeting_id | tostring),
          recording:      (.extendedProps.recording // ""),
          ai_summary_url: (.extendedProps.ai_summary_url // ""),
          restricted:     (.extendedProps.restricted // false),
          visibility:     (.extendedProps.visibility // ""),
          project_slug:   (if $ps == "" then "unknown-project" else $ps end),
          project_name:   ($names[$ps] // .extendedProps.project_slug // "unknown-project")
        })
      | map(select(.start != "" and .start[0:10] >= $from and .start[0:10] <= $to))
      | unique_by(.meeting_id + "-" + .occurrence_id)
      | sort_by(.start)
      | .[]
      | . + { series: ( .title
                        | ascii_downcase
                        | gsub("[^a-z0-9]+"; "_")
                        | gsub("^_+|_+$"; "")
                        | if . == "" then "untitled_meeting" else . end
                        | .[0:60] ) }
    ' "$work"/chunk.*.json > "$work/meetings.ndjson" 2>"$work/jq.err"; then
  warn "$(cat "$work/jq.err")"
  die "could not parse the meeting listing"
fi

local total
total=$(grep -c . "$work/meetings.ndjson" 2>/dev/null) || total=0
(( total > 0 )) || die "no past meetings for '$slug' between $from and $to"

if (( list_only )); then
  jq -r '[.start, (.meeting_id + "-" + .occurrence_id), .project_slug, .series, .title] | @tsv' \
    "$work/meetings.ndjson"
  print -ru2 -- "$PROG: $total meetings"
  exit 0
fi

print -ru2 -- "$PROG: $total meetings; $jobs workers; writing to $out"
(( force )) && print -ru2 -- "$PROG: --force: re-fetching assets already on disk"

# Each meeting goes to its own file and the queue carries paths, not payloads:
# xargs -I caps the replacement string (255 bytes on macOS), which a JSON blob
# would overrun.
local qn=0
if ! { : > "$work/jobs.txt" } 2>/dev/null; then
  die "could not prepare the work list"
fi
while IFS= read -r line; do
  [[ -n $line ]] || continue
  qn=$(( qn + 1 ))
  print -r -- "$line" > "$work/m.$qn.json" || die "could not write the work list"
  print -r -- "$work/m.$qn.json" >> "$work/jobs.txt" || die "could not write the work list"
done < "$work/meetings.ndjson"
(( qn > 0 )) || die "no meetings to fetch"

printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
  start meeting project series stem transcript cc chat summary note title > "$manifest" \
  || die "cannot write manifest $manifest"

local xrc=0
xargs -P $jobs -n 1 -I {} "$SELF" _one {} "$out" "$force" < "$work/jobs.txt" >> "$manifest" || xrc=$?
(( xrc == 0 )) || warn "warning: some workers exited non-zero (xargs rc=$xrc)"

# ---- report ---------------------------------------------------------------
print -ru2 -- ""
print -ru2 -- "$PROG: manifest -> $manifest"
awk -F'\t' '
  NR > 1 { rows++; for (i = 6; i <= 9; i++) c[i "_" $i]++ }
  END {
    printf "  %-10s %-7s %-9s %s\n", "asset", "ok", "missing", "error";
    split("6:transcript 7:cc 8:chat 9:summary", a, " ");
    for (j = 1; j <= 4; j++) {
      split(a[j], p, ":"); i = p[1];
      printf "  %-10s %-7d %-9d %d\n", p[2], c[i "_ok"], c[i "_missing"], c[i "_error"];
    }
    printf "  %d meetings\n", rows;
  }' "$manifest" >&2
