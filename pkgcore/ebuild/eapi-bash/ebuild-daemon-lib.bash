#!/bin/bash
# ebuild-daemon.bash; daemon lib code.
# Copyright 2005-2010 Brian Harring <ferringb@gmail.com>
# License BSD/GPL2


# ask the python side to display sandbox complaints.
request_sandbox_summary() {
	local line
	speak "request_sandbox_summary ${SANDBOX_LOG}"
	listen line
	while [ "$line" != "end_sandbox_summary" ]; do
		echo "$line"
		listen line
	done
}

internal_inherit() {
	local line
	if [ "$#" != "1" ]; then
		die "internal_inherit accepts one arg, requested eclass location.  $* is a bit much"
	fi
	speak "request_inherit $1"
	listen line
	if [ "$line" == "path" ]; then
		listen line;
		source "${line}" >&2 || die "failed sources inherit: ${line}"
	elif [ "$line" == "transfer" ]; then
		listen line;
		eval "$line" || die "failed evaluating eclass $x on an inherit transfer"
	elif [ "$line" == "failed" ]; then
		die "inherit for $x failed"
	else
		die "unknown inherit command from pythonic side, '$line' for eclass $x"
	fi
}

source_profiles() {
	local line
	speak request_profiles
	listen line
	while [ "$line" != end_request ]; do
		if [ "$line" == "path" ]; then
			listen line;
			source "${line}" >&2
		elif [ "$line" == "transfer" ]; then
			listen line;
			eval "$line" || die "failed evaluating profile bashrc: ${line}"
		else
			speak "failed"
			die "unknown profile bashrc transfer mode from pythonic side, '$line'"
		fi
		speak "next"
		listen line
	done
}
DONT_EXPORT_FUNCS="${DONT_EXPORT_FUNCS} $(declare -F | cut -s -d ' ' -f 3)"

: